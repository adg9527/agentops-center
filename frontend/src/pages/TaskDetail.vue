<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowLeftOutlined, StopOutlined } from '@ant-design/icons-vue'
import { api, type Task, type TraceEvent } from '../api'
import { auth } from '../auth'
import { useLive } from '../live'
import { EVENT_TYPES, fmtCost, fmtMs, fmtNum, fmtTime } from '../labels'
import StatusTag from '../components/StatusTag.vue'
import MarkdownView from '../components/MarkdownView.vue'
import EventTag from '../components/EventTag.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()
const can = (role: 'admin' | 'operator' | 'viewer') => auth.can(role)

const task = ref<Task | null>(null)
const events = ref<TraceEvent[]>([])
const filter = ref('all')
const selected = ref<TraceEvent | null>(null)

async function load() {
  const id = props.id
  try {
    const t = await api<Task>(`/api/tasks/${id}`)
    if (props.id === id) task.value = t
  } catch (e: any) {
    message.error(e.message)
  }
  try {
    const ev = await api<TraceEvent[]>(`/api/tasks/${id}/events`)
    if (props.id === id) events.value = ev
  } catch {
    /* 事件按需加载失败时保留已有数据 */
  }
}

watch(
  () => props.id,
  () => {
    task.value = null
    events.value = []
    selected.value = null
    filter.value = 'all'
    load()
  },
  { immediate: true },
)

useLive((m) => {
  const id = props.id
  if (m.topic === 'trace_event' && String(m.data.task_id) === String(id)) {
    const exists = events.value.some((e) => e.event_id === m.data.event_id)
    if (!exists) events.value = [...events.value, m.data as TraceEvent]
  } else if (
    (m.topic === 'task_updated' && String(m.data.id) === String(id)) ||
    (m.topic === 'run_updated' && String(m.data.task_id) === String(id))
  ) {
    api<Task>(`/api/tasks/${id}`)
      .then((t) => {
        if (props.id === id) task.value = t
      })
      .catch(() => {})
  }
})

// 页面卸载时 useLive 会注销订阅；这里再清掉抽屉引用，避免残留状态
onBeforeUnmount(() => {
  selected.value = null
})

const agentName = computed<Record<number, string | undefined>>(() => {
  const map: Record<number, string | undefined> = {}
  for (const r of task.value?.runs ?? []) map[r.agent_id] = r.agent_name
  return map
})

const shownEvents = computed(() =>
  events.value.filter((e) => {
    if (filter.value === 'all') return true
    if (filter.value === 'tools')
      return e.event_type.startsWith('tool.') || e.event_type === 'permission.denied'
    if (filter.value === 'messages') return e.event_type === 'agent.message'
    return e.event_type.startsWith('task.') || e.event_type.startsWith('agent.run')
  }),
)

const filterOptions = [
  { label: '全部', value: 'all' },
  { label: '工具', value: 'tools' },
  { label: '消息', value: 'messages' },
  { label: '生命周期', value: 'life' },
]

const active = computed(() => task.value?.status === 'running' || task.value?.status === 'pending')

const selectedTitle = computed(() =>
  selected.value ? EVENT_TYPES[selected.value.event_type]?.text ?? selected.value.event_type : '',
)

function eventColor(e: TraceEvent): string {
  if (e.status === 'error' || e.status === 'denied') return 'red'
  if (e.event_type.startsWith('tool.')) return 'gold'
  return 'blue'
}

async function cancelTask() {
  if (!task.value) return
  try {
    await api(`/api/tasks/${task.value.id}/cancel`, { method: 'POST' })
    message.success('已发送终止信号')
  } catch (e: any) {
    message.error(e.message)
  }
}
</script>

<template>
  <a-space direction="vertical" :size="16" style="width: 100%">
    <a-card v-if="!task" loading />

    <template v-else>
      <a-card size="small">
        <template #title>
          <a-space>
            <a-button type="text" @click="router.push('/tasks')">
              <template #icon><ArrowLeftOutlined /></template>
            </a-button>
            任务 #{{ task.id }} {{ task.title }}
            <StatusTag :status="task.status" />
          </a-space>
        </template>
        <template #extra>
          <a-popconfirm
            v-if="can('operator') && active"
            title="终止任务？"
            description="将结束所有正在执行的 Agent 进程。"
            @confirm="cancelTask"
          >
            <a-button danger>
              <template #icon><StopOutlined /></template>
              终止任务
            </a-button>
          </a-popconfirm>
        </template>

        <a-descriptions size="small" :column="{ xs: 1, md: 3 }">
          <a-descriptions-item label="创建">{{ fmtTime(task.created_at) }}</a-descriptions-item>
          <a-descriptions-item label="开始">{{ fmtTime(task.started_at) }}</a-descriptions-item>
          <a-descriptions-item label="结束">{{ fmtTime(task.finished_at) }}</a-descriptions-item>
          <a-descriptions-item label="Trace ID">
            <a-typography-text code copyable>{{ task.trace_id }}</a-typography-text>
          </a-descriptions-item>
          <a-descriptions-item label="工作目录" :span="2">
            <a-typography-text code copyable :style="{ fontSize: '12px' }">
              {{ task.workspace_dir }}
            </a-typography-text>
          </a-descriptions-item>
        </a-descriptions>

        <a-alert v-if="task.error" type="error" show-icon :message="task.error" style="margin-top: 8px" />

        <a-typography-paragraph style="margin-top: 12px; margin-bottom: 0">
          <a-typography-text type="secondary">任务内容：</a-typography-text>
        </a-typography-paragraph>
        <pre class="prompt-pre">{{ task.prompt }}</pre>
      </a-card>

      <a-alert v-if="task.parent_task_id" type="info" show-icon>
        <template #message>
          这是协作任务
          <router-link :to="`/tasks/${task.parent_task_id}`">#{{ task.parent_task_id }}</router-link>
          的一个步骤（子任务），与其共享 Trace 与工作目录。
        </template>
      </a-alert>

      <a-row :gutter="[16, 16]">
        <a-col :xs="24" :xl="12">
          <a-card size="small" title="Agent 结果">
            <a-tabs v-if="task.runs.length">
              <a-tab-pane v-for="r in task.runs" :key="r.id">
                <template #tab>
                  <a-space :size="4">
                    {{ r.agent_name }}
                    <a-tag v-if="r.attempt > 1">第{{ r.attempt }}次</a-tag>
                    <StatusTag :status="r.status" />
                  </a-space>
                </template>

                <a-descriptions size="small" :column="2" bordered style="margin-bottom: 12px">
                  <a-descriptions-item label="模型">{{ r.model_used ?? '—' }}</a-descriptions-item>
                  <a-descriptions-item label="耗时">{{ fmtMs(r.duration_ms) }}</a-descriptions-item>
                  <a-descriptions-item label="Token 入 / 出">
                    {{ fmtNum(r.input_tokens) }} / {{ fmtNum(r.output_tokens) }}
                  </a-descriptions-item>
                  <a-descriptions-item label="成本">{{ fmtCost(r.cost_usd) }}</a-descriptions-item>
                  <a-descriptions-item label="会话 ID" :span="2">
                    <a-typography-text
                      :style="{ fontSize: '12px' }"
                      :copyable="!!r.external_session_id"
                    >
                      {{ r.external_session_id ?? '—' }}
                    </a-typography-text>
                  </a-descriptions-item>
                  <a-descriptions-item label="配置版本">v{{ r.agent_config_version }}</a-descriptions-item>
                  <a-descriptions-item label="Span">{{ r.span_id }}</a-descriptions-item>
                </a-descriptions>

                <a-alert v-if="r.error" type="error" show-icon :message="r.error" style="margin-bottom: 12px" />

                <MarkdownView v-if="r.output_text" :content="r.output_text" class="output-md" />
                <a-empty
                  v-else
                  :description="r.status === 'running' || r.status === 'pending' ? '执行中，等待结果…' : '无输出'"
                />
              </a-tab-pane>
            </a-tabs>
            <a-empty v-else description="暂无执行记录" />
          </a-card>
        </a-col>

        <a-col :xs="24" :xl="12">
          <a-card size="small" :title="`执行时间线（${events.length} 个事件）`">
            <template #extra>
              <a-segmented v-model:value="filter" size="small" :options="filterOptions" />
            </template>

            <div class="timeline-wrap">
              <a-timeline v-if="shownEvents.length">
                <a-timeline-item v-for="e in shownEvents" :key="e.event_id" :color="eventColor(e)">
                  <div
                    class="event-row"
                    :class="{ 'event-tool': e.event_type.startsWith('tool.') }"
                    @click="selected = e"
                  >
                    <a-space :size="4" wrap>
                      <a-typography-text type="secondary" :style="{ fontSize: '12px' }">
                        {{ fmtTime(e.timestamp) }}
                      </a-typography-text>
                      <EventTag :type="e.event_type" />
                      <a-tag v-if="e.agent_id">{{ agentName[e.agent_id] ?? `Agent#${e.agent_id}` }}</a-tag>
                      <b>{{ e.name }}</b>
                      <a-typography-text v-if="e.duration_ms != null" type="secondary">
                        {{ fmtMs(e.duration_ms) }}
                      </a-typography-text>
                    </a-space>
                    <div v-if="e.input_summary || e.output_summary" class="event-summary">
                      {{ e.output_summary || e.input_summary }}
                    </div>
                  </div>
                </a-timeline-item>
              </a-timeline>
              <a-empty v-else description="暂无事件" />
            </div>
          </a-card>
        </a-col>
      </a-row>
    </template>

    <a-drawer :open="!!selected" :width="600" :title="selectedTitle" @close="selected = null">
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
          <pre class="detail-pre">{{ selected.input_summary ?? '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="输出摘要">
          <pre class="detail-pre">{{ selected.output_summary ?? '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="metadata">
          <pre class="detail-pre">{{ JSON.stringify(selected.metadata, null, 2) }}</pre>
        </a-descriptions-item>
      </a-descriptions>
    </a-drawer>
  </a-space>
</template>

<style scoped>
.prompt-pre,
.output-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  max-height: 420px;
  overflow: auto;
  background: #0e1420;
  color: #d6e2f5;
  padding: 12px;
  border-radius: 6px;
}
.prompt-pre {
  max-height: 160px;
  padding: 10px;
}
.timeline-wrap {
  max-height: 640px;
  overflow: auto;
  padding-top: 8px;
}
.event-row {
  cursor: pointer;
}
.event-tool {
  margin-left: 16px;
}
.event-summary {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  margin: 2px 0 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.detail-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
}
</style>
