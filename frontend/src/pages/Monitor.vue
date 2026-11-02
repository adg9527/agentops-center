<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { Dayjs } from 'dayjs'
import { message } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { api, type Agent, type TraceEvent } from '../api'
import { useLive } from '../live'
import EventTag from '../components/EventTag.vue'
import { EVENT_TYPES, eventType, fmtMs, fmtTime } from '../labels'

const events = ref<TraceEvent[]>([])
const agents = ref<Agent[]>([])
const agentId = ref<number | undefined>(undefined)
const type = ref<string | undefined>(undefined)
const taskId = ref('')
const range = ref<[Dayjs, Dayjs] | undefined>(undefined)
const follow = ref(true)
const selected = ref<TraceEvent | null>(null)

const agentNames = computed<Record<number, string>>(() => {
  const map: Record<number, string> = {}
  for (const a of agents.value) map[a.id] = a.name
  return map
})

const agentOptions = computed(() => agents.value.map((a) => ({ value: a.id, label: a.name })))
const typeOptions = computed(() => [
  { value: 'task.', label: '任务生命周期' },
  { value: 'agent.run', label: 'Agent 执行' },
  ...Object.entries(EVENT_TYPES)
    .filter(([k]) => !k.startsWith('task.') && !k.startsWith('agent.run'))
    .map(([k, v]) => ({ value: k, label: v.text })),
])

const columns: TableColumnsType<TraceEvent> = [
  { title: '时间', dataIndex: 'timestamp', key: 'timestamp', width: 130 },
  { title: '类型', dataIndex: 'event_type', key: 'event_type', width: 130 },
  { title: '任务', dataIndex: 'task_id', key: 'task_id', width: 70 },
  { title: 'Agent', dataIndex: 'agent_id', key: 'agent_id', width: 130 },
  { title: '名称', dataIndex: 'name', key: 'name', width: 160, ellipsis: true },
  { title: '状态', dataIndex: 'status', key: 'status', width: 70 },
  { title: '耗时', dataIndex: 'duration_ms', key: 'duration_ms', width: 80 },
  { title: '摘要', key: 'summary', ellipsis: true },
]

async function load() {
  const q = new URLSearchParams({ limit: '300' })
  if (agentId.value) q.set('agent_id', String(agentId.value))
  if (type.value) q.set('event_type', type.value)
  if (taskId.value) q.set('task_id', taskId.value)
  if (range.value?.[0]) q.set('since', range.value[0].toISOString())
  if (range.value?.[1]) q.set('until', range.value[1].toISOString())
  try {
    events.value = await api<TraceEvent[]>(`/api/events?${q}`)
  } catch (e) {
    message.error(e instanceof Error ? e.message : '事件加载失败')
  }
}

function onTaskIdInput(e: Event) {
  const el = e.target as HTMLInputElement
  const digits = el.value.replace(/\D/g, '')
  if (el.value !== digits) el.value = digits
  taskId.value = digits
}

function onAgentChange(v: unknown) {
  agentId.value = typeof v === 'number' ? v : undefined
}

function onTypeChange(v: unknown) {
  type.value = typeof v === 'string' ? v : undefined
}

function onRangeChange(v: unknown) {
  if (Array.isArray(v) && v.length === 2 && v[0] && v[1]) {
    range.value = [v[0] as Dayjs, v[1] as Dayjs]
  } else {
    range.value = undefined
  }
}

function onFollowChange(v: unknown) {
  follow.value = v === true
}

function customRow(record: TraceEvent) {
  return {
    onClick: () => {
      selected.value = record
    },
    style: { cursor: 'pointer' },
  }
}

function closeDrawer() {
  selected.value = null
}

watch([agentId, type, taskId, range], () => {
  void load()
})

onMounted(() => {
  void load()
  api<Agent[]>('/api/agents')
    .then((list) => (agents.value = list))
    .catch(() => undefined)
})

useLive((m) => {
  if (!follow.value || m.topic !== 'trace_event') return
  const e: TraceEvent = m.data
  if (
    (agentId.value && e.agent_id !== agentId.value) ||
    (type.value && !e.event_type.startsWith(type.value)) ||
    (taskId.value && String(e.task_id) !== taskId.value) ||
    range.value
  ) {
    return
  }
  events.value = [e, ...events.value].slice(0, 500)
})
</script>

<template>
  <a-card title="执行监控">
    <template #extra>
      <a-space wrap>
        <a-input
          allow-clear
          placeholder="任务 ID"
          style="width: 100px"
          :value="taskId"
          @input="onTaskIdInput"
        />
        <a-select
          allow-clear
          placeholder="Agent"
          style="width: 150px"
          :value="agentId"
          :options="agentOptions"
          @change="onAgentChange"
        />
        <a-select
          allow-clear
          placeholder="事件类型"
          style="width: 150px"
          :value="type"
          :options="typeOptions"
          @change="onTypeChange"
        />
        <a-range-picker show-time :value="range" @change="onRangeChange" />
        <a-space :size="4">
          <a-switch size="small" :checked="follow" @change="onFollowChange" />
          <a-typography-text type="secondary">实时</a-typography-text>
        </a-space>
        <a-button @click="load">
          <template #icon><ReloadOutlined /></template>
        </a-button>
      </a-space>
    </template>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="第一阶段：实时事件流与多维筛选已可用。Trace 瀑布图、单 Agent 轨迹视图、任务回放与日志全文检索在第三阶段实现。不采集模型内部思维链。"
    />

    <a-table
      size="small"
      row-key="event_id"
      :data-source="events"
      :columns="columns"
      :scroll="{ x: 1000 }"
      :pagination="{ pageSize: 50 }"
      :custom-row="customRow"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'timestamp'">{{ fmtTime(record.timestamp) }}</template>
        <template v-else-if="column.key === 'event_type'">
          <EventTag :type="record.event_type" />
        </template>
        <template v-else-if="column.key === 'task_id'">
          <router-link
            v-if="record.task_id"
            :to="`/tasks/${record.task_id}`"
            @click.stop
          >
            #{{ record.task_id }}
          </router-link>
        </template>
        <template v-else-if="column.key === 'agent_id'">
          {{ record.agent_id ? agentNames[record.agent_id] ?? `#${record.agent_id}` : '—' }}
        </template>
        <template v-else-if="column.key === 'duration_ms'">
          {{ fmtMs(record.duration_ms) }}
        </template>
        <template v-else-if="column.key === 'summary'">
          {{ record.output_summary || record.input_summary }}
        </template>
      </template>
    </a-table>

    <a-drawer
      :open="!!selected"
      :width="600"
      :title="selected ? eventType(selected.event_type).text : ''"
      @close="closeDrawer"
    >
      <a-descriptions v-if="selected" :column="1" size="small" bordered>
        <a-descriptions-item label="事件类型">{{ selected.event_type }}</a-descriptions-item>
        <a-descriptions-item label="名称">{{ selected.name }}</a-descriptions-item>
        <a-descriptions-item label="时间">{{ selected.timestamp }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ selected.status }}</a-descriptions-item>
        <a-descriptions-item label="耗时">{{ fmtMs(selected.duration_ms) }}</a-descriptions-item>
        <a-descriptions-item label="trace_id">{{ selected.trace_id }}</a-descriptions-item>
        <a-descriptions-item label="span_id / parent">
          {{ selected.span_id }} / {{ selected.parent_span_id ?? '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="task / run / agent">
          {{ selected.task_id }} / {{ selected.run_id ?? '—' }} / {{ selected.agent_id ?? '—' }}
        </a-descriptions-item>
        <a-descriptions-item label="输入摘要">
          <pre class="pre">{{ selected.input_summary ?? '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="输出摘要">
          <pre class="pre">{{ selected.output_summary ?? '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="metadata">
          <pre class="pre">{{ JSON.stringify(selected.metadata, null, 2) }}</pre>
        </a-descriptions-item>
      </a-descriptions>
    </a-drawer>
  </a-card>
</template>

<style scoped>
.pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  margin: 0;
  max-height: 420px;
  overflow: auto;
}
</style>
