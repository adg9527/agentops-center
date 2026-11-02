<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { api, type Agent, type Task } from '../api'
import { auth } from '../auth'
import { useLive } from '../live'
import { TASK_STATUS, fmtCost, fmtTime } from '../labels'
import StatusTag from '../components/StatusTag.vue'

const router = useRouter()
const can = (role: 'admin' | 'operator' | 'viewer') => auth.can(role)

const data = ref<{ total: number; items: Task[] }>({ total: 0, items: [] })
const status = ref<string | undefined>(undefined)
const agentId = ref<number | undefined>(undefined)
const page = ref(1)
const agents = ref<Agent[]>([])
const open = ref(false)

const statusOptions = computed(() =>
  Object.entries(TASK_STATUS)
    .filter(([k]) => k !== 'timeout')
    .map(([k, v]) => ({ value: k, label: v.text })),
)
const agentOptions = computed(() => agents.value.map((a) => ({ value: a.id, label: a.name })))

const columns = [
  { key: 'id', title: 'ID', dataIndex: 'id', width: 70 },
  { key: 'title', title: '标题', dataIndex: 'title', ellipsis: true },
  { key: 'status', title: '状态', dataIndex: 'status', width: 100 },
  { key: 'agents', title: 'Agent' },
  { key: 'cost', title: '成本', width: 100 },
  { key: 'created_at', title: '创建时间', dataIndex: 'created_at', width: 140 },
]

async function load() {
  const q = new URLSearchParams({ limit: '20', offset: String((page.value - 1) * 20) })
  if (status.value) q.set('status', status.value)
  if (agentId.value) q.set('agent_id', String(agentId.value))
  try {
    data.value = await api<{ total: number; items: Task[] }>(`/api/tasks?${q}`)
  } catch (e: any) {
    message.error(e.message)
  }
}

watch([status, agentId, page], load, { immediate: true })

onMounted(() => {
  api<Agent[]>('/api/agents')
    .then((a) => (agents.value = a))
    .catch((e: any) => message.error(e.message))
})

useLive((m) => {
  if (m.topic === 'task_updated' || m.topic === 'run_updated') load()
})

function onTableChange(pag: any) {
  page.value = pag?.current ?? 1
}

function taskRow(t: Task) {
  return { onClick: () => router.push(`/tasks/${t.id}`), style: { cursor: 'pointer' } }
}

function uniqueAgents(t: Task) {
  const seen = new Set<number>()
  return t.runs
    .filter((r) => {
      if (seen.has(r.agent_id)) return false
      seen.add(r.agent_id)
      return true
    })
    .map((r) => ({ id: r.agent_id, name: r.agent_name }))
}

function taskCost(t: Task): string {
  const known = t.runs.filter((r) => r.cost_usd != null)
  return known.length ? fmtCost(known.reduce((s, r) => s + (r.cost_usd ?? 0), 0)) : '—'
}

// ---- create task ----
const formRef = ref<any>()
const busy = ref(false)
const newTask = reactive<{ agent_ids: number[]; title: string; prompt: string }>({
  agent_ids: [],
  title: '',
  prompt: '',
})
const usableAgents = computed(() => agents.value.filter((a) => a.enabled))
const usableOptions = computed(() =>
  usableAgents.value.map((a) => ({
    value: a.id,
    label: `${a.name}（${a.adapter_name}${a.model ? ' · ' + a.model : ''}）`,
  })),
)

function openNewTask() {
  newTask.agent_ids = []
  newTask.title = ''
  newTask.prompt = ''
  open.value = true
  nextTick(() => formRef.value?.clearValidate?.())
}

async function submitTask() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  busy.value = true
  try {
    const t = await api<Task>('/api/tasks', { body: { ...newTask } })
    message.success(`任务 #${t.id} 已提交`)
    open.value = false
    router.push(`/tasks/${t.id}`)
  } catch (e: any) {
    message.error(e.message)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <a-card title="任务中心">
    <template #extra>
      <a-space wrap>
        <a-select
          v-model:value="status"
          allow-clear
          placeholder="状态"
          style="width: 120px"
          :options="statusOptions"
          @change="page = 1"
        />
        <a-select
          v-model:value="agentId"
          allow-clear
          placeholder="Agent"
          style="width: 160px"
          :options="agentOptions"
          @change="page = 1"
        />
        <a-button @click="load">
          <template #icon><ReloadOutlined /></template>
        </a-button>
        <a-button v-if="can('operator')" type="primary" @click="openNewTask">
          <template #icon><PlusOutlined /></template>
          发送任务
        </a-button>
      </a-space>
    </template>

    <a-table
      row-key="id"
      :data-source="data.items"
      :columns="columns"
      :scroll="{ x: 800 }"
      :custom-row="taskRow"
      :pagination="{ current: page, pageSize: 20, total: data.total, showSizeChanger: false }"
      @change="onTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'id'">#{{ record.id }}</template>

        <template v-else-if="column.key === 'title'">
          <a-tag v-if="record.mode === 'auto'" color="gold">自动协作</a-tag>
          <a-tag v-else-if="record.mode === 'manual'" color="cyan">群聊分工</a-tag>
          {{ record.title }}
        </template>

        <template v-else-if="column.key === 'status'">
          <StatusTag :status="record.status" />
        </template>

        <template v-else-if="column.key === 'agents'">
          <a-space :size="4" wrap>
            <a-tag v-for="a in uniqueAgents(record)" :key="a.id">{{ a.name }}</a-tag>
          </a-space>
        </template>

        <template v-else-if="column.key === 'cost'">{{ taskCost(record) }}</template>

        <template v-else-if="column.key === 'created_at'">{{ fmtTime(record.created_at) }}</template>
      </template>
    </a-table>

    <a-modal
      v-model:open="open"
      title="发送任务"
      ok-text="发送"
      :confirm-loading="busy"
      :width="640"
      @ok="submitTask"
      @cancel="open = false"
    >
      <a-alert
        type="info"
        show-icon
        style="margin-bottom: 16px"
        message="任务在平台托管的独立工作目录中执行（data/workspaces/task-<id>），不会访问你的其他文件。选择多个 Agent 时各自独立执行同一任务，结果在同一 Trace 中串联。"
      />
      <a-form ref="formRef" :model="newTask" layout="vertical">
        <a-form-item
          name="agent_ids"
          label="执行 Agent"
          :rules="[{ required: true, type: 'array', message: '至少选择一个 Agent' }]"
        >
          <a-select
            v-model:value="newTask.agent_ids"
            mode="multiple"
            :options="usableOptions"
            placeholder="请选择执行 Agent"
          >
            <template #notFoundContent>
              <a-typography-text type="secondary">
                没有已启用的 Agent，请先到「Agent 管理」接入
              </a-typography-text>
            </template>
          </a-select>
        </a-form-item>
        <a-form-item name="title" label="标题（可选）">
          <a-input v-model:value="newTask.title" placeholder="默认取任务内容首行" />
        </a-form-item>
        <a-form-item name="prompt" label="任务内容" :rules="[{ required: true, message: '请输入任务内容' }]">
          <a-textarea v-model:value="newTask.prompt" :rows="8" placeholder="描述你希望 Agent 完成的任务……" />
        </a-form-item>
      </a-form>
    </a-modal>
  </a-card>
</template>
