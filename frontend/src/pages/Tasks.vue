<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { api, type Agent, type Task } from '../api'
import { auth } from '../auth'
import { useLive } from '../live'
import StatusTag from '../components/StatusTag.vue'
import { fmtCost, fmtTime } from '../labels'

interface ConversationRow {
  id: number
  name: string
  member_count: number
  active_tasks: number
  last_message: { sender: string; content: string; at: string } | null
}

interface MessageRow {
  id: number
  sender_type: string
  sender_name: string
  kind: string
  content: string
  task_id: number | null
  created_at: string
}

const router = useRouter()

const tasks = ref<Task[]>([])
const agents = ref<Agent[]>([])
const conversations = ref<ConversationRow[]>([])
const messages = ref<MessageRow[]>([])
const loading = ref(false)
const msgLoading = ref(false)

const selectedConv = ref<number | null>(null)
const agentFilter = ref<number | undefined>(undefined)
const statusFilter = ref<string | undefined>(undefined)

const sendOpen = ref(false)
const sending = ref(false)
const form = reactive({ title: '', prompt: '', agent_ids: [] as number[] })

const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '待执行', value: 'pending' },
  { label: '执行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
  { label: '等待审批', value: 'waiting_approval' },
]

const visibleTasks = computed(() =>
  selectedConv.value ? tasks.value.filter((t) => ((t as any).conversation_id ?? null) === selectedConv.value) : tasks.value,
)

const enabledAgents = computed(() => agents.value.filter((a) => a.enabled))

/** 每个会话下有多少任务，用来在左侧列表上标数。 */
const convStats = computed(() => {
  const map = new Map<number, { total: number; running: number }>()
  for (const t of tasks.value) {
    const cid = (t as any).conversation_id as number | null
    if (!cid) continue
    const cur = map.get(cid) ?? { total: 0, running: 0 }
    cur.total += 1
    if (['running', 'pending', 'waiting_approval', 'waiting_handoff', 'paused'].includes(t.status)) cur.running += 1
    map.set(cid, cur)
  }
  return map
})

const conversationList = computed(() =>
  conversations.value
    .map((c) => ({ ...c, task_count: convStats.value.get(c.id)?.total ?? 0, live_count: convStats.value.get(c.id)?.running ?? 0 }))
    .filter((c) => c.task_count > 0 || c.active_tasks > 0),
)

const runningByAgent = computed(() => {
  const map = new Map<string, number>()
  for (const t of tasks.value) {
    if (!['running', 'pending', 'waiting_approval', 'waiting_handoff'].includes(t.status)) continue
    const names = new Set((t.runs ?? []).map((r: any) => r.agent_name).filter(Boolean))
    if (!names.size) {
      const key = '未分配'
      map.set(key, (map.get(key) ?? 0) + 1)
    }
    for (const n of names) map.set(n, (map.get(n) ?? 0) + 1)
  }
  return [...map.entries()].sort((a, b) => b[1] - a[1])
})

const currentConversation = computed(() => conversations.value.find((c) => c.id === selectedConv.value) ?? null)

function runAgents(t: Task): string[] {
  const names = (t.runs ?? []).map((r: any) => r.agent_name).filter(Boolean)
  return [...new Set(names)] as string[]
}

function taskCost(t: Task): number | null {
  const costs = (t.runs ?? []).map((r: any) => r.cost_usd).filter((c: any) => typeof c === 'number')
  return costs.length ? costs.reduce((a: number, b: number) => a + b, 0) : null
}

async function loadAgents() {
  try {
    agents.value = await api<Agent[]>('/api/agents')
  } catch {
    agents.value = []
  }
}

async function loadConversations() {
  try {
    conversations.value = await api<ConversationRow[]>('/api/conversations')
  } catch {
    conversations.value = []
  }
}

async function loadTasks() {
  loading.value = true
  try {
    const params = new URLSearchParams({ limit: '200' })
    if (agentFilter.value) params.set('agent_id', String(agentFilter.value))
    if (statusFilter.value) params.set('status', String(statusFilter.value))
    const r = await api<{ items: Task[] }>(`/api/tasks?${params.toString()}`)
    tasks.value = r.items ?? []
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

async function loadMessages() {
  if (!selectedConv.value) {
    messages.value = []
    return
  }
  msgLoading.value = true
  try {
    messages.value = await api<MessageRow[]>(`/api/conversations/${selectedConv.value}/messages?limit=50`)
  } catch {
    messages.value = []
  } finally {
    msgLoading.value = false
  }
}

function selectConversation(id: number | null) {
  selectedConv.value = id
}


async function submitTask() {
  if (!form.prompt.trim() || !form.agent_ids.length) {
    message.warning('请填写任务内容并至少选择一个 Agent')
    return
  }
  sending.value = true
  try {
    const t = await api<Task>('/api/tasks', {
      body: { prompt: form.prompt, title: form.title, agent_ids: form.agent_ids },
    })
    message.success(`任务 #${t.id} 已派发`)
    sendOpen.value = false
    Object.assign(form, { title: '', prompt: '', agent_ids: [] })
    await loadTasks()
    router.push(`/tasks/${t.id}`)
  } catch (e: any) {
    message.error(e.message)
  } finally {
    sending.value = false
  }
}

watch([selectedConv], async () => {
  await loadMessages()
})
watch([agentFilter, statusFilter], () => loadTasks())

useLive((msg) => {
  if (['task_updated', 'run_updated', 'collab_updated', 'message_created'].includes(msg.topic)) {
    loadTasks()
    if (selectedConv.value) loadMessages()
  }
})

onMounted(async () => {
  await Promise.all([loadAgents(), loadConversations(), loadTasks()])
})
</script>

<template>
  <a-row :gutter="16">
    <a-col :xs="24" :md="7" :xl="6">
      <a-card size="small" title="会话" :bordered="false" :body-style="{ padding: '8px' }">
        <div class="conv-list">
          <div class="conv-entry" :class="{ active: selectedConv === null }" @click="selectConversation(null)">
            <span>全部任务</span>
            <a-badge :count="tasks.length" :number-style="{ backgroundColor: '#d9d9d9' }" />
          </div>
          <div
            v-for="c in conversationList"
            :key="c.id"
            class="conv-entry"
            :class="{ active: selectedConv === c.id }"
            @click="selectConversation(c.id)"
          >
            <div class="conv-title">
              <span>{{ c.name }}</span>
              <a-badge v-if="c.live_count" status="processing" />
            </div>
            <div class="conv-meta">{{ c.task_count }} 个任务 · {{ c.member_count }} 个参与者</div>
          </div>
        </div>
        <a-empty v-if="!conversationList.length" description="还没有会话" :image-style="{ height: '40px' }" />
      </a-card>

      <a-card size="small" title="Agent 正在执行" :bordered="false" style="margin-top: 16px" :body-style="{ padding: '12px' }">
        <a-list size="small" :data-source="runningByAgent">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-space>
                <a-tag color="processing">{{ item[1] }}</a-tag>
                <span>{{ item[0] }}</span>
              </a-space>
            </a-list-item>
          </template>
        </a-list>
        <a-empty v-if="!runningByAgent.length" description="当前没有执行中的任务" :image-style="{ height: '40px' }" />
      </a-card>
    </a-col>

    <a-col :xs="24" :md="17" :xl="18">
      <a-card size="small" :bordered="false">
        <template #title>
          <a-space>
            <span>{{ currentConversation ? currentConversation.name : '全部任务' }}</span>
            <a-tag v-if="currentConversation" color="blue">{{ conversationList.find((c) => c.id === currentConversation?.id)?.member_count ?? 0 }} 个参与者</a-tag>
            <a-tag v-if="currentConversation?.last_message">{{ fmtTime(currentConversation.last_message.at) }}</a-tag>
          </a-space>
        </template>
        <template #extra>
          <a-space wrap>
            <a-select
              v-model:value="agentFilter"
              allow-clear
              placeholder="按 Agent 过滤"
              style="width: 170px"
              :options="agents.map((a) => ({ label: a.name, value: a.id }))"
            />
            <a-select v-model:value="statusFilter" style="width: 130px" :options="STATUS_OPTIONS" />
            <a-button size="small" :loading="loading" @click="loadTasks">刷新</a-button>
            <a-button v-if="auth.can('operator')" type="primary" size="small" @click="sendOpen = true">发送任务</a-button>
          </a-space>
        </template>

        <div v-if="selectedConv" class="msg-panel">
          <a-spin :spinning="msgLoading">
            <div v-for="m in messages" :key="m.id" class="msg-row" :class="m.sender_type">
              <div class="msg-head">
                <b>{{ m.sender_name }}</b>
                <a-typography-text type="secondary" style="font-size: 11px">{{ fmtTime(m.created_at) }}</a-typography-text>
                <a-tag v-if="m.task_id" color="purple" style="cursor: pointer" @click="router.push(`/tasks/${m.task_id}`)">#{{ m.task_id }}</a-tag>
              </div>
              <div class="msg-body">{{ m.content }}</div>
            </div>
            <a-empty v-if="!messages.length" description="这个会话还没有消息" :image-style="{ height: '40px' }" />
          </a-spin>
        </div>

        <a-divider style="margin: 12px 0" />

        <a-spin :spinning="loading">
          <a-list :data-source="visibleTasks" item-layout="vertical" size="small">
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta>
                  <template #title>
                    <a-space>
                      <a class="task-title" @click="router.push(`/tasks/${item.id}`)">#{{ item.id }} {{ item.title }}</a>
                      <StatusTag :status="item.status" />
                      <a-tag v-if="item.mode">{{ item.mode === 'auto' ? '自动协作' : item.mode === 'manual' ? '人工指定' : item.mode }}</a-tag>
                    </a-space>
                  </template>
                  <template #description>
                    <a-space wrap size="small">
                      <a-tag v-for="name in runAgents(item)" :key="name" color="blue">{{ name }}</a-tag>
                      <a-typography-text type="secondary">{{ fmtTime(item.created_at) }}</a-typography-text>
                      <a-typography-text v-if="taskCost(item) !== null" type="secondary">成本 {{ fmtCost(taskCost(item)) }}</a-typography-text>
                      <a-typography-text v-if="item.error" type="danger">{{ item.error.slice(0, 80) }}</a-typography-text>
                    </a-space>
                  </template>
                </a-list-item-meta>
                <template #actions>
                  <a @click="router.push(`/tasks/${item.id}`)">查看详情</a>
                </template>
              </a-list-item>
            </template>
          </a-list>
          <a-empty v-if="!loading && !visibleTasks.length" description="没有符合条件的任务" />
        </a-spin>
      </a-card>
    </a-col>
  </a-row>

  <a-modal v-model:open="sendOpen" title="发送任务" :confirm-loading="sending" @ok="submitTask" ok-text="派发">
    <a-form layout="vertical">
      <a-form-item label="标题（可选）">
        <a-input v-model:value="form.title" placeholder="留空则取任务内容首行" />
      </a-form-item>
      <a-form-item label="任务内容" required>
        <a-textarea v-model:value="form.prompt" :rows="6" placeholder="描述要 Agent 完成的工作" />
      </a-form-item>
      <a-form-item label="执行 Agent" required>
        <a-select
          v-model:value="form.agent_ids"
          mode="multiple"
          placeholder="可多选，任务会并行派发"
          :options="enabledAgents.map((a) => ({ label: `${a.name}（${a.adapter_name}）`, value: a.id }))"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<style scoped>
.conv-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.conv-entry {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

.conv-entry:hover {
  background: #f5f7fa;
}

.conv-entry.active {
  background: #e6f0ff;
  border-color: #91caff;
}

.conv-item {
  padding: 2px 0;
}

.conv-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}

.conv-meta {
  font-size: 11px;
  color: #999;
}

.msg-panel {
  max-height: 320px;
  overflow: auto;
  padding-right: 6px;
}

.msg-row {
  padding: 8px 10px;
  border-radius: 8px;
  background: #fafafa;
  margin-bottom: 8px;
}

.msg-row.agent {
  background: #f0f5ff;
}

.msg-row.user {
  background: #f6ffed;
}

.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 2px;
}

.msg-body {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  color: #333;
  max-height: 120px;
  overflow: hidden;
}

.task-title {
  color: #1677ff;
}
</style>
