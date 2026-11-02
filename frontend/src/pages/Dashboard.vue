<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import type { TableColumnsType } from 'ant-design-vue'
import * as echarts from 'echarts'
import { api, type Agent } from '../api'
import { useLive } from '../live'
import StatusTag from '../components/StatusTag.vue'
import TaskGraphPanel from '../components/TaskGraphPanel.vue'
import { fmtCost, fmtMs, fmtNum, fmtTime } from '../labels'

interface DailyRun {
  date: string
  completed: number
  failed: number
  other: number
}

interface PerAgentRow {
  agent_id: number
  name: string
  total: number
  completed: number
}

interface RecentTask {
  id: number
  title: string
  status: string
  created_at: string
}

interface DashboardSummary {
  range_days: number
  agents_total: number
  agents_enabled: number
  agents_online: number
  tasks_by_status: Record<string, number>
  tasks_running: number
  tasks_completed: number
  tasks_failed: number
  runs_total: number
  run_success_rate: number | null
  avg_duration_ms: number | null
  input_tokens: number
  output_tokens: number
  cost_usd: number
  cost_known_runs: number
  daily_runs: DailyRun[]
  per_agent: PerAgentRow[]
  recent_tasks: RecentTask[]
  not_implemented: Record<string, { phase: number }>
}

interface Project {
  id: number
  name: string
}

/** Shape of the objects echarts hands to tooltip formatters. */
interface TipItem {
  axisValue?: string
  name?: string
  seriesName?: string
  value?: number | string
  marker?: string
  dataIndex?: number
}

// Validated palette (blue/orange pass CVD ΔE 26.8 on a light card surface).
const C_DONE = '#3987e5'
const C_FAIL = '#d95926'
const C_OTHER = '#5b6170'
const SERIES = [
  { key: 'completed', name: '成功', color: C_DONE },
  { key: 'failed', name: '失败/超时', color: C_FAIL },
  { key: 'other', name: '其他（取消/进行中）', color: C_OTHER },
] as const

const DAY_OPTIONS = [
  { label: '24 小时', value: 1 },
  { label: '7 天', value: 7 },
  { label: '30 天', value: 30 },
]
const VIEW_OPTIONS = [
  { label: '图表', value: 'chart' },
  { label: '表格', value: 'table' },
]
const PHASES: readonly (readonly [string, string])[] = [
  ['mcp_count', 'MCP 资源'],
  ['skill_count', 'Skill 资源'],
  ['prompt_count', 'Prompt 模板'],
  ['recent_evaluations', '最近评测'],
  ['recent_reports', '最近报告'],
]

const router = useRouter()
const days = ref(7)
const agentId = ref<number | undefined>(undefined)
const projectId = ref<number | undefined>(undefined)
const agents = ref<Agent[]>([])
const projects = ref<Project[]>([])
const summary = ref<DashboardSummary | null>(null)
const view = ref<'chart' | 'table'>('chart')

const daily = computed<DailyRun[]>(() => summary.value?.daily_runs ?? [])
const perAgent = computed<PerAgentRow[]>(() => summary.value?.per_agent ?? [])
const projectOptions = computed(() => projects.value.map((p) => ({ label: p.name, value: p.id })))
const agentOptions = computed(() => agents.value.map((a) => ({ label: a.name, value: a.id })))
const dailyColumns: TableColumnsType<DailyRun> = [
  { title: '日期', dataIndex: 'date' },
  { title: '成功', dataIndex: 'completed' },
  { title: '失败/超时', dataIndex: 'failed' },
  { title: '其他', dataIndex: 'other' },
]

const statCards = computed(() => {
  const d = summary.value
  if (!d) return []
  return [
    { title: '已接入 Agent', value: String(d.agents_total), hint: `启用 ${d.agents_enabled}` },
    { title: '在线 Agent', value: String(d.agents_online), hint: '启用且最近检测/执行成功' },
    { title: '执行中任务', value: String(d.tasks_running), hint: '含排队' },
    { title: '已完成任务', value: String(d.tasks_completed), hint: `${d.range_days} 天内` },
    { title: '失败任务', value: String(d.tasks_failed), hint: `${d.range_days} 天内` },
    {
      title: 'Agent 执行成功率',
      value: d.run_success_rate == null ? '—' : `${(d.run_success_rate * 100).toFixed(1)}%`,
      hint: `共 ${d.runs_total} 次执行`,
    },
    { title: '平均执行耗时', value: fmtMs(d.avg_duration_ms), hint: '已结束的执行' },
    {
      title: 'Token (入 / 出)',
      value: `${fmtNum(d.input_tokens)} / ${fmtNum(d.output_tokens)}`,
      hint: 'Agent 上报',
    },
    {
      title: '成本',
      value: fmtCost(d.cost_usd),
      hint: `${d.cost_known_runs}/${d.runs_total} 次执行可获取成本`,
    },
  ]
})

const phaseCards = computed(() =>
  PHASES.map(([key, label]) => ({
    key,
    label,
    phase: summary.value?.not_implemented[key]?.phase,
  })),
)

async function load() {
  const q = new URLSearchParams({ days: String(days.value) })
  if (agentId.value) q.set('agent_id', String(agentId.value))
  if (projectId.value) q.set('project_id', String(projectId.value))
  try {
    summary.value = await api<DashboardSummary>(`/api/dashboard/summary?${q}`)
  } catch (e) {
    message.error(e instanceof Error ? e.message : '工作台数据加载失败')
  }
}

function onDaysChange(v: string | number) {
  days.value = Number(v)
}

function onViewChange(v: string | number) {
  view.value = v === 'table' ? 'table' : 'chart'
}

function onProjectChange(v: unknown) {
  projectId.value = typeof v === 'number' ? v : undefined
}

function onAgentChange(v: unknown) {
  agentId.value = typeof v === 'number' ? v : undefined
}

function openTask(id: number) {
  void router.push(`/tasks/${id}`)
}

// ---- echarts ----
type Chart = ReturnType<typeof echarts.init>
const dailyEl = ref<HTMLDivElement | null>(null)
const agentEl = ref<HTMLDivElement | null>(null)
let dailyChart: Chart | null = null
let agentChart: Chart | null = null

function dailyOption(): echarts.EChartsOption {
  const rows = daily.value
  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: '#ffffff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#1f2937', fontSize: 12 },
      formatter: (params: unknown) => {
        const list = Array.isArray(params) ? (params as TipItem[]) : [params as TipItem]
        const head = list[0]?.axisValue ?? ''
        const body = list.map((p) => `${p.marker ?? ''}${p.seriesName ?? ''} ${p.value ?? 0}`)
        return [head, ...body].join('<br/>')
      },
    },
    legend: {
      top: 0,
      right: 0,
      icon: 'roundRect',
      itemWidth: 8,
      itemHeight: 8,
      textStyle: { color: '#4b5563', fontSize: 12 },
    },
    grid: { left: 4, right: 8, top: 34, bottom: 0, containLabel: true },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.date),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#6b7280', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: {
        color: '#6b7280',
        fontSize: 11,
        formatter: (v: number) => (Number.isInteger(v) ? String(v) : ''),
      },
      splitLine: { lineStyle: { color: '#f0f2f5' } },
    },
    series: SERIES.map((s) => ({
      name: s.name,
      type: 'bar',
      stack: 'runs',
      barMaxWidth: 28,
      itemStyle: { color: s.color, borderColor: '#ffffff', borderWidth: 2 },
      emphasis: { focus: 'series' },
      data: rows.map((r) => r[s.key]),
    })),
  }
}

function agentOption(): echarts.EChartsOption {
  const rows = perAgent.value
  return {
    tooltip: {
      trigger: 'item',
      backgroundColor: '#ffffff',
      borderColor: '#e5e7eb',
      textStyle: { color: '#1f2937', fontSize: 12 },
      formatter: (params: unknown) => {
        const p = params as TipItem
        const row = rows[p.dataIndex ?? 0]
        return `${p.name ?? ''}<br/>执行次数 ${row?.total ?? 0}<br/>成功次数 ${row?.completed ?? 0}`
      },
    },
    grid: { left: 4, right: 48, top: 8, bottom: 0, containLabel: true },
    xAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { color: '#6b7280', fontSize: 11 },
      splitLine: { lineStyle: { color: '#f0f2f5' } },
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: rows.map((r) => r.name),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: '#e5e7eb' } },
      axisLabel: { color: '#4b5563', fontSize: 12 },
    },
    series: [
      {
        type: 'bar',
        barMaxWidth: 18,
        itemStyle: { color: C_DONE, borderRadius: [0, 3, 3, 0] },
        label: { show: true, position: 'right', color: '#4b5563', fontSize: 11 },
        data: rows.map((r) => r.total),
      },
    ],
  }
}

function paint() {
  const dEl = dailyEl.value
  if (dEl && view.value === 'chart' && daily.value.length && dEl.clientWidth > 0) {
    dailyChart ??= echarts.init(dEl)
    dailyChart.setOption(dailyOption(), true)
    dailyChart.resize()
  }
  const aEl = agentEl.value
  if (aEl && perAgent.value.length && aEl.clientWidth > 0) {
    agentChart ??= echarts.init(aEl)
    agentChart.setOption(agentOption(), true)
    agentChart.resize()
  }
}

function onResize() {
  dailyChart?.resize()
  agentChart?.resize()
}

watch([daily, perAgent, view], () => {
  void nextTick(paint)
})

watch([days, agentId, projectId], () => {
  void load()
})

onMounted(() => {
  void load()
  api<Agent[]>('/api/agents')
    .then((list) => (agents.value = list))
    .catch(() => undefined)
  api<Project[]>('/api/projects')
    .then((list) => (projects.value = list))
    .catch(() => undefined)
})

onMounted(() => {
  window.addEventListener('resize', onResize)
  void nextTick(paint)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  dailyChart?.dispose()
  dailyChart = null
  agentChart?.dispose()
  agentChart = null
})

useLive((m) => {
  if (m.topic === 'task_updated' || m.topic === 'agent_updated') void load()
})
</script>

<template>
  <div class="page">
    <a-card v-if="!summary" loading />

    <template v-else>
      <a-space wrap>
        <a-typography-title :level="4" style="margin: 0">工作台</a-typography-title>
        <a-segmented :value="days" :options="DAY_OPTIONS" @change="onDaysChange" />
        <a-select
          allow-clear
          placeholder="全部项目"
          style="width: 150px"
          :value="projectId"
          :options="projectOptions"
          @change="onProjectChange"
        />
        <a-select
          allow-clear
          placeholder="全部 Agent"
          style="width: 180px"
          :value="agentId"
          :options="agentOptions"
          @change="onAgentChange"
        />
      </a-space>

      <a-row :gutter="[16, 16]">
        <a-col v-for="c in statCards" :key="c.title" :xs="12" :md="8" :xl="6" :xxl="4">
          <a-card size="small">
            <a-statistic :title="c.title" :value="c.value" :value-style="{ fontSize: '22px' }" />
            <a-typography-text type="secondary" class="hint">{{ c.hint }}</a-typography-text>
          </a-card>
        </a-col>
        <a-col v-for="p in phaseCards" :key="p.key" :xs="12" :md="8" :xl="6" :xxl="4">
          <a-card size="small">
            <a-statistic
              :title="p.label"
              value="—"
              :value-style="{ fontSize: '22px', color: '#5b6170' }"
            />
            <a-tooltip title="该模块尚未实现，不显示虚构数字">
              <a-tag>第 {{ p.phase ?? '—' }} 阶段实现</a-tag>
            </a-tooltip>
          </a-card>
        </a-col>
      </a-row>

      <a-row :gutter="[16, 16]">
        <a-col :xs="24" :xl="14">
          <a-card size="small" title="每日 Agent 执行">
            <template #extra>
              <a-segmented
                size="small"
                :value="view"
                :options="VIEW_OPTIONS"
                @change="onViewChange"
              />
            </template>
            <a-empty v-if="!daily.length" description="该时间范围内暂无执行记录" />
            <div v-show="daily.length > 0 && view === 'chart'" ref="dailyEl" class="chart" />
            <a-table
              v-if="daily.length > 0 && view === 'table'"
              size="small"
              row-key="date"
              :data-source="daily"
              :pagination="false"
              :columns="dailyColumns"
            />
          </a-card>
        </a-col>
        <a-col :xs="24" :xl="10">
          <a-card size="small" title="各 Agent 执行次数">
            <a-empty v-if="!perAgent.length" description="暂无数据" />
            <div v-show="perAgent.length > 0" ref="agentEl" class="chart" />
          </a-card>
        </a-col>
      </a-row>

      <TaskGraphPanel style="margin-bottom: 16px" />
      <a-card size="small" title="最近任务">
        <a-list :data-source="summary.recent_tasks" :locale="{ emptyText: '暂无任务' }">
          <template #renderItem="{ item }">
            <a-list-item class="recent-item" @click="openTask(item.id)">
              <a-space>
                <StatusTag :status="item.status" />
                <span>#{{ item.id }} {{ item.title }}</span>
              </a-space>
              <template #extra>
                <a-typography-text type="secondary">{{ fmtTime(item.created_at) }}</a-typography-text>
              </template>
            </a-list-item>
          </template>
        </a-list>
      </a-card>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.hint {
  font-size: 12px;
}

.chart {
  width: 100%;
  height: 260px;
}

.recent-item {
  cursor: pointer;
}
</style>
