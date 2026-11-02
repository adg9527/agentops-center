<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { api } from '../api'
import { useLive } from '../live'
import StatusTag from './StatusTag.vue'

interface GraphNode {
  id: string
  label: string
  type: 'task' | 'step' | 'agent'
  status?: string
  role?: string
  kind?: string
  agent_id?: number
  progress?: number
  total?: number
  done?: number
}

interface GraphEdge {
  source: string
  target: string
  kind: 'assign' | 'owns' | 'depends' | 'handoff'
  label?: string
}

interface GraphData {
  task: { id: number; title: string; status: string; mode?: string }
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: { total: number; done: number; running: number; failed: number; progress: number }
}

interface TaskRow {
  id: number
  title: string
  status: string
  created_at: string
}

const tasks = ref<TaskRow[]>([])
const taskId = ref<number | null>(null)
const graph = ref<GraphData | null>(null)
const tree = ref<any>(null)
const loading = ref(false)
const tab = ref<'graph' | 'tree'>('graph')
const graphEl = ref<HTMLDivElement | null>(null)
const treeEl = ref<HTMLDivElement | null>(null)

let graphChart: echarts.ECharts | null = null
let treeChart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const STATUS_COLOR: Record<string, string> = {
  completed: '#52c41a',
  running: '#1677ff',
  failed: '#cf1322',
  cancelled: '#8c8c8c',
  skipped: '#bfbfbf',
  pending: '#d9d9d9',
  waiting_approval: '#faad14',
  waiting_handoff: '#722ed1',
  paused: '#fa8c16',
  timeout: '#cf1322',
}

const TYPE_COLOR = { task: '#722ed1', agent: '#1677ff' } as const

const percent = computed(() => Math.round((graph.value?.stats.progress ?? 0) * 100))
const hasGraph = computed(() => !!graph.value && graph.value.nodes.length > 1)

function nodeColor(n: GraphNode): string {
  if (n.type === 'task') return TYPE_COLOR.task
  if (n.type === 'agent') return TYPE_COLOR.agent
  return STATUS_COLOR[n.status ?? ''] ?? '#d9d9d9'
}

/** Wait until the ref exists AND is laid out: echarts renders a 0×0 canvas inside a hidden tab pane. */
function waitForRef(target: { value: HTMLDivElement | null }, tries = 60): Promise<HTMLDivElement | null> {
  return new Promise((resolve) => {
    let left = tries
    const check = () => {
      const el = target.value
      if (el && el.clientWidth > 10 && el.clientHeight > 10) return resolve(el)
      if (--left <= 0) return resolve(null)
      requestAnimationFrame(check)
    }
    check()
  })
}

function observe(el: HTMLElement) {
  if (!resizeObserver || !el) return
  resizeObserver.unobserve(el)
  resizeObserver.observe(el)
}

async function loadTasks() {
  try {
    const r = await api<{ items: TaskRow[] }>('/api/tasks?limit=50')
    tasks.value = r.items ?? []
    if (taskId.value && !tasks.value.some((t) => t.id === taskId.value)) taskId.value = null
    if (taskId.value === null && tasks.value.length) taskId.value = tasks.value[0].id
  } catch {
    tasks.value = []
  }
}

async function loadGraph() {
  if (!taskId.value) {
    graph.value = null
    tree.value = null
    return
  }
  loading.value = true
  try {
    const [g, t] = await Promise.all([
      api<GraphData>(`/api/graph/task/${taskId.value}`),
      api<any>(`/api/graph/task/${taskId.value}/mindmap`),
    ])
    graph.value = g
    tree.value = t
    await nextTick()
    if (tab.value === 'graph') await renderGraph()
    else await renderTree()
  } catch {
    graph.value = null
    tree.value = null
  } finally {
    loading.value = false
  }
}

async function renderGraph() {
  const el = await waitForRef(graphEl)
  if (!el || !graph.value) return
  graphChart = graphChart ?? echarts.init(el)
  observe(el)

  const byId = new Map(graph.value.nodes.map((n) => [n.id, n]))
  const data = graph.value.nodes.map((n) => ({
    id: n.id,
    name: n.label,
    category: n.type === 'task' ? 0 : n.type === 'agent' ? 1 : 2,
    symbolSize: n.type === 'task' ? 52 : n.type === 'agent' ? 40 : 28,
    itemStyle: { color: nodeColor(n), borderColor: '#fff', borderWidth: 2 },
  }))
  const links = graph.value.edges.map((e) => ({
    source: e.source,
    target: e.target,
    label: {
      show: e.kind === 'depends' || e.kind === 'handoff',
      formatter: e.label ?? '',
      fontSize: 10,
      color: '#8c8c8c',
    },
    lineStyle: {
      type: e.kind === 'depends' ? 'dashed' : 'solid',
      width: e.kind === 'handoff' ? 2 : 1,
      color: e.kind === 'handoff' ? '#722ed1' : '#aab4c5',
      curveness: 0.08,
    },
    symbol: ['none', 'arrow'],
    symbolSize: 7,
  }))

  graphChart.setOption(
    {
      tooltip: {
        formatter: (p: any) => {
          if (p.dataType === 'edge') {
            const s = byId.get(p.data.source)
            const t = byId.get(p.data.target)
            return `${s?.label ?? p.data.source} → ${t?.label ?? p.data.target}<br/>${p.data.label?.formatter || ''}`
          }
          const n = byId.get(p.data.id)
          if (!n) return p.name
          const extra =
            n.type === 'step'
              ? `状态：${n.status}｜角色：${n.role ?? '-'}`
              : n.type === 'agent'
                ? `完成 ${n.done ?? 0}/${n.total ?? 0}`
                : ''
          return `<b>${n.label}</b><br/>${n.type === 'task' ? `任务 #${graph.value?.task.id}` : extra}`
        },
      },
      legend: { data: ['任务', '参与者', '步骤'], bottom: 0 },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          categories: [{ name: '任务' }, { name: '参与者' }, { name: '步骤' }],
          data,
          links,
          force: { repulsion: 300, edgeLength: [70, 150], gravity: 0.08 },
          emphasis: { focus: 'adjacency' },
          label: { show: true, fontSize: 11 },
        },
      ],
    },
    true,
  )
  graphChart.resize()
}

async function renderTree() {
  const el = await waitForRef(treeEl)
  if (!el || !tree.value) return
  treeChart = treeChart ?? echarts.init(el)
  observe(el)

  treeChart.setOption(
    {
      tooltip: { trigger: 'item', triggerOn: 'mousemove' },
      series: [
        {
          type: 'tree',
          data: [tree.value],
          top: '4%',
          left: '10%',
          bottom: '4%',
          right: '22%',
          symbolSize: 10,
          orient: 'LR',
          label: { position: 'left', verticalAlign: 'middle', align: 'right', fontSize: 12 },
          leaves: { label: { position: 'right', align: 'left', fontSize: 11 } },
          initialTreeDepth: 3,
          expandAndCollapse: true,
          animationDuration: 350,
          lineStyle: { color: '#c3ccdb', width: 1.2 },
        },
      ],
    },
    true,
  )
  treeChart.resize()
}

watch(taskId, () => loadGraph())
watch(tab, async () => {
  await nextTick()
  if (tab.value === 'graph') await renderGraph()
  else await renderTree()
})

useLive((msg) => {
  if (msg.topic === 'task_updated' || msg.topic === 'run_updated') {
    if (msg.data?.id === taskId.value) loadGraph()
    loadTasks()
  }
})

onMounted(async () => {
  resizeObserver = new ResizeObserver(() => {
    graphChart?.resize()
    treeChart?.resize()
  })
  await loadTasks()
  await loadGraph()
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  graphChart?.dispose()
  treeChart?.dispose()
  graphChart = null
  treeChart = null
})
</script>

<template>
  <a-card title="任务关系图 / 思维导图" :bordered="false">
    <template #extra>
      <a-space>
        <a-select
          v-model:value="taskId"
          show-search
          option-filter-prop="label"
          placeholder="选择任务"
          style="width: 280px"
          :loading="loading"
        >
          <a-select-option v-for="t in tasks" :key="t.id" :value="t.id" :label="`#${t.id} ${t.title}`">
            #{{ t.id }} {{ t.title }}
          </a-select-option>
        </a-select>
        <a-button size="small" :loading="loading" @click="loadGraph">刷新</a-button>
      </a-space>
    </template>

    <a-empty v-if="!taskId" description="还没有任务，先到「任务中心」派一个" />

    <template v-else>
      <a-space wrap style="margin-bottom: 12px">
        <StatusTag v-if="graph" :status="graph.task.status" />
        <a-tag color="purple">任务 #{{ taskId }}</a-tag>
        <a-tag>完成 {{ graph?.stats.done ?? 0 }}/{{ graph?.stats.total ?? 0 }}</a-tag>
        <a-tag v-if="graph?.stats.running" color="processing">进行中 {{ graph.stats.running }}</a-tag>
        <a-tag v-if="graph?.stats.failed" color="error">失败 {{ graph.stats.failed }}</a-tag>
        <a-progress :percent="percent" size="small" style="width: 200px" />
      </a-space>

      <a-alert
        v-if="!hasGraph"
        type="info"
        show-icon
        style="margin-bottom: 12px"
        message="这个任务还没有可展示的协作结构"
        description="任务是单人执行且尚未产生运行记录，或者协作步骤还没拆分出来；派发后会自动出现节点与连线。"
      />

      <a-tabs v-model:active-key="tab">
        <a-tab-pane key="graph" tab="关系图" force-render>
          <div ref="graphEl" style="height: 460px; width: 100%" />
          <a-typography-text type="secondary" style="font-size: 12px">
            实线＝分配，虚线＝依赖，紫色粗线＝交接；拖拽节点、滚轮缩放可查看布局。
          </a-typography-text>
        </a-tab-pane>
        <a-tab-pane key="tree" tab="思维导图" force-render>
          <div ref="treeEl" style="height: 460px; width: 100%" />
          <a-typography-text type="secondary" style="font-size: 12px">
            参与者 → 调用过的工具 → 涉及的文件；点击节点可展开/收起。
          </a-typography-text>
        </a-tab-pane>
      </a-tabs>
    </template>
  </a-card>
</template>
