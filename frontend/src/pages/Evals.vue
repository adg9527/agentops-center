<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { api, type Agent } from '../api'
import { auth } from '../auth'

interface Suite {
  id: number
  name: string
  description: string
  agent_ids: number[]
  skill_ids: number[]
  prompt_ids: number[]
  mcp_ids: number[]
  cases: { prompt: string; expect?: string }[]
  thresholds: Record<string, number>
  max_rounds: number
  enabled: boolean
  last_run: Run | null
}

interface RunDetail extends Run {
  detail: {
    task_id: number
    prompt: string
    expect?: string
    status?: string
    error?: string
    agents: {
      agent: string
      status: string
      tool_calls: number
      keyword_hit: boolean | null
      tokens: number
      duration_ms: number
      output: string
    }[]
  }[]
}

interface Run {
  id: number
  suite_id: number
  round: number
  status: string
  passed: boolean | null
  metrics: Record<string, any>
  task_ids: number[]
  hints: string
  error: string | null
  started_at: string
  finished_at: string | null
}

interface Option {
  label: string
  value: number
}

const suites = ref<Suite[]>([])
const agents = ref<Agent[]>([])
const skills = ref<{ id: number; name: string }[]>([])
const prompts = ref<{ id: number; name: string }[]>([])
const mcps = ref<{ id: number; name: string }[]>([])
const loading = ref(false)
const saving = ref(false)
const open = ref(false)
const editing = ref<number | null>(null)

const runsOpen = ref(false)
const runsSuite = ref<Suite | null>(null)
const runs = ref<Run[]>([])
const runsLoading = ref(false)
const detail = ref<RunDetail | null>(null)
const detailOpen = ref(false)

const form = reactive({
  name: '',
  description: '',
  agent_ids: [] as number[],
  skill_ids: [] as number[],
  prompt_ids: [] as number[],
  mcp_ids: [] as number[],
  casesText: '[{"prompt": "用一句话说明什么是向量数据库", "expect": "向量"}]',
  completion: 100,
  tool_use: 0,
  keyword: 80,
  max_avg_tokens: 0,
  max_avg_ms: 0,
  max_rounds: 3,
  enabled: true,
})

let timer: number | undefined

const canWrite = computed(() => auth.can('operator'))
const canDelete = computed(() => auth.can('admin'))

function agentOptions(): Option[] {
  return agents.value.map((a) => ({ label: `${a.name}（${a.adapter_name}）`, value: a.id }))
}
const skillOptions = computed(() => skills.value.map((s) => ({ label: s.name, value: s.id })))
const promptOptions = computed(() => prompts.value.map((s) => ({ label: s.name, value: s.id })))
const mcpOptions = computed(() => mcps.value.map((s) => ({ label: s.name, value: s.id })))

function metricsText(run: Run | null): string {
  if (!run) return '尚未运行'
  const m = run.metrics || {}
  const parts = [
    `完成 ${(m.completion ?? 0).toFixed(2)}`,
    `工具 ${(m.tool_use ?? 0).toFixed(2)}`,
  ]
  if (m.keyword !== null && m.keyword !== undefined) parts.push(`关键词 ${m.keyword.toFixed(2)}`)
  parts.push(`${m.avg_tokens ?? 0} tok`, `${m.avg_ms ?? 0} ms`)
  return parts.join(' · ')
}

async function load() {
  loading.value = true
  try {
    const [s, a, sk, pr, mc] = await Promise.all([
      api<Suite[]>('/api/evals'),
      api<Agent[]>('/api/agents'),
      api<{ id: number; name: string }[]>('/api/skills'),
      api<{ id: number; name: string }[]>('/api/prompts'),
      api<{ id: number; name: string }[]>('/api/mcp-servers'),
    ])
    suites.value = s
    agents.value = a
    skills.value = sk
    prompts.value = pr
    mcps.value = mc
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

function reset() {
  editing.value = null
  Object.assign(form, {
    name: '',
    description: '',
    agent_ids: [],
    skill_ids: [],
    prompt_ids: [],
    mcp_ids: [],
    casesText: '[{"prompt": "用一句话说明什么是向量数据库", "expect": "向量"}]',
    completion: 100,
    tool_use: 0,
    keyword: 80,
    max_avg_tokens: 0,
    max_avg_ms: 0,
    max_rounds: 3,
    enabled: true,
  })
}

function openCreate() {
  reset()
  open.value = true
}

function openEdit(s: Suite) {
  editing.value = s.id
  const t = s.thresholds || {}
  Object.assign(form, {
    name: s.name,
    description: s.description,
    agent_ids: [...(s.agent_ids || [])],
    skill_ids: [...(s.skill_ids || [])],
    prompt_ids: [...(s.prompt_ids || [])],
    mcp_ids: [...(s.mcp_ids || [])],
    casesText: JSON.stringify(s.cases || [], null, 2),
    completion: Math.round((t.completion ?? 1) * 100),
    tool_use: Math.round((t.tool_use ?? 0) * 100),
    keyword: Math.round((t.keyword ?? 0.8) * 100),
    max_avg_tokens: t.max_avg_tokens ?? 0,
    max_avg_ms: t.max_avg_ms ?? 0,
    max_rounds: s.max_rounds,
    enabled: s.enabled,
  })
  open.value = true
}

async function save() {
  if (!form.name.trim() || !form.agent_ids.length) {
    message.warning('请填写名称并至少选择一个 Agent')
    return
  }
  let cases: any[] = []
  try {
    cases = JSON.parse(form.casesText)
    if (!Array.isArray(cases) || !cases.length) throw new Error('至少一条用例')
  } catch (e: any) {
    message.error(`用例不是合法 JSON：${e.message}`)
    return
  }
  const body = {
    name: form.name.trim(),
    description: form.description,
    agent_ids: form.agent_ids,
    skill_ids: form.skill_ids,
    prompt_ids: form.prompt_ids,
    mcp_ids: form.mcp_ids,
    cases,
    thresholds: {
      completion: form.completion / 100,
      tool_use: form.tool_use / 100,
      keyword: form.keyword / 100,
      max_avg_tokens: Number(form.max_avg_tokens) || 0,
      max_avg_ms: Number(form.max_avg_ms) || 0,
    },
    max_rounds: form.max_rounds,
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (editing.value) await api(`/api/evals/${editing.value}`, { method: 'PATCH', body })
    else await api('/api/evals', { body })
    message.success('已保存')
    open.value = false
    await load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    saving.value = false
  }
}

async function start(s: Suite) {
  try {
    await api(`/api/evals/${s.id}/run`, { method: 'POST', body: {} })
    message.success('评测已启动，最多 ' + s.max_rounds + ' 轮')
    await load()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function stop(s: Suite) {
  try {
    await api(`/api/evals/${s.id}/stop`, { method: 'POST', body: {} })
    message.info('已请求停止（当前用例结束后生效）')
  } catch (e: any) {
    message.error(e.message)
  }
}

function remove(s: Suite) {
  Modal.confirm({
    title: `删除套件「${s.name}」？`,
    okType: 'danger',
    onOk: async () => {
      try {
        await api(`/api/evals/${s.id}`, { method: 'DELETE' })
        message.success('已删除')
        await load()
      } catch (e: any) {
        message.error(e.message)
      }
    },
  })
}

async function openRuns(s: Suite) {
  runsSuite.value = s
  runsOpen.value = true
  await loadRuns(s.id)
}

async function loadRuns(suiteId: number) {
  runsLoading.value = true
  try {
    runs.value = await api<Run[]>(`/api/evals/${suiteId}/runs`)
  } catch (e: any) {
    message.error(e.message)
  } finally {
    runsLoading.value = false
  }
}

async function openDetail(run: Run) {
  try {
    detail.value = await api<RunDetail>(`/api/evals/runs/${run.id}`)
    detailOpen.value = true
  } catch (e: any) {
    message.error(e.message)
  }
}

const anyRunning = computed(() => suites.value.some((s) => s.last_run?.status === 'running'))

onMounted(async () => {
  await load()
  timer = window.setInterval(async () => {
    if (anyRunning.value || runsOpen.value) {
      await load()
      if (runsSuite.value) await loadRuns(runsSuite.value.id)
    }
  }, 3000)
})

onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer)
})
</script>

<template>
  <a-card title="自动化评测" :bordered="false">
    <template #extra>
      <a-space>
        <a-button size="small" :loading="loading" @click="load">刷新</a-button>
        <a-button v-if="canWrite" type="primary" size="small" @click="openCreate">新建评测套件</a-button>
      </a-space>
    </template>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="选择 Agent / 技能 / Prompt / MCP，配置用例与各维度阈值；未达标会自动进入下一轮（把上一轮的问题作为反馈注入 Prompt），直到全部达标或用完轮数。"
    />

    <a-table :columns="[
      { title: '名称', dataIndex: 'name', width: 180 },
      { title: '组合', key: 'mix', width: 260 },
      { title: '用例', key: 'cases', width: 70 },
      { title: '阈值', key: 'thresholds', width: 240 },
      { title: '最近结果', key: 'last', width: 260 },
      { title: '操作', key: 'actions', width: 260 },
    ]" :data-source="suites" :loading="loading" row-key="id" :pagination="false" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'mix'">
          <a-space wrap size="4">
            <a-tag color="blue">{{ record.agent_ids.length }} Agent</a-tag>
            <a-tag v-if="record.skill_ids.length" color="green">{{ record.skill_ids.length }} 技能</a-tag>
            <a-tag v-if="record.prompt_ids.length" color="purple">{{ record.prompt_ids.length }} Prompt</a-tag>
            <a-tag v-if="record.mcp_ids.length" color="orange">{{ record.mcp_ids.length }} MCP</a-tag>
          </a-space>
        </template>
        <template v-else-if="column.key === 'cases'">{{ record.cases.length }}</template>
        <template v-else-if="column.key === 'thresholds'">
          <a-space wrap size="4">
            <a-tag>完成 ≥ {{ Math.round((record.thresholds.completion ?? 1) * 100) }}%</a-tag>
            <a-tag v-if="record.thresholds.tool_use">工具 ≥ {{ Math.round(record.thresholds.tool_use * 100) }}%</a-tag>
            <a-tag v-if="record.thresholds.keyword">关键词 ≥ {{ Math.round(record.thresholds.keyword * 100) }}%</a-tag>
            <a-tag>≤ {{ record.max_rounds }} 轮</a-tag>
          </a-space>
        </template>
        <template v-else-if="column.key === 'last'">
          <template v-if="record.last_run">
            <a-space direction="vertical" size="0">
              <a-space>
                <a-tag :color="record.last_run.passed ? 'success' : record.last_run.status === 'running' ? 'processing' : 'error'">
                  {{ record.last_run.status === 'running' ? '运行中' : record.last_run.passed ? '达标' : '未达标' }}
                </a-tag>
                <span style="font-size: 12px">第 {{ record.last_run.round }} 轮</span>
              </a-space>
              <span style="font-size: 11px; color: #888">{{ metricsText(record.last_run) }}</span>
            </a-space>
          </template>
          <span v-else style="color: #bbb">—</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button type="primary" size="small" :disabled="!canWrite || record.last_run?.status === 'running'" @click="start(record)">
              运行
            </a-button>
            <a-button size="small" :disabled="record.last_run?.status !== 'running'" @click="stop(record)">停止</a-button>
            <a-button size="small" @click="openRuns(record)">记录</a-button>
            <a-button v-if="canWrite" size="small" @click="openEdit(record)">编辑</a-button>
            <a-button v-if="canDelete" size="small" danger @click="remove(record)">删除</a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && !suites.length" description="还没有评测套件" />

    <a-drawer v-model:open="open" :title="editing ? '编辑评测套件' : '新建评测套件'" :width="720">
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="例如 代码助手基线" />
        </a-form-item>
        <a-form-item label="说明">
          <a-input v-model:value="form.description" />
        </a-form-item>
        <a-form-item label="评测 Agent（可多选）" required>
          <a-select v-model:value="form.agent_ids" mode="multiple" :options="agentOptions()" placeholder="选择参与评测的 Agent" />
        </a-form-item>
        <a-form-item label="技能 / Prompt / MCP（可选，会注入到任务提示词）">
          <a-space direction="vertical" style="width: 100%">
            <a-select v-model:value="form.skill_ids" mode="multiple" :options="skillOptions" placeholder="技能" />
            <a-select v-model:value="form.prompt_ids" mode="multiple" :options="promptOptions" placeholder="Prompt 模板（取第一个）" />
            <a-select v-model:value="form.mcp_ids" mode="multiple" :options="mcpOptions" placeholder="MCP（记录在评测上下文，便于追溯）" />
          </a-space>
        </a-form-item>
        <a-form-item label="用例（JSON 数组）" extra='[{"prompt": "任务内容", "expect": "期望出现的关键词（可选）"}]' required>
          <a-textarea v-model:value="form.casesText" :rows="7" class="mono" />
        </a-form-item>
        <a-row :gutter="12">
          <a-col :span="8"><a-form-item label="完成率阈值 %"><a-input-number v-model:value="form.completion" :min="0" :max="100" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="工具使用率阈值 %"><a-input-number v-model:value="form.tool_use" :min="0" :max="100" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="关键词命中阈值 %"><a-input-number v-model:value="form.keyword" :min="0" :max="100" style="width: 100%" /></a-form-item></a-col>
        </a-row>
        <a-row :gutter="12">
          <a-col :span="8"><a-form-item label="平均 token 上限（0=不限）"><a-input-number v-model:value="form.max_avg_tokens" :min="0" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="平均耗时上限 ms（0=不限）"><a-input-number v-model:value="form.max_avg_ms" :min="0" style="width: 100%" /></a-form-item></a-col>
          <a-col :span="8"><a-form-item label="最多评测轮数"><a-input-number v-model:value="form.max_rounds" :min="1" :max="20" style="width: 100%" /></a-form-item></a-col>
        </a-row>
        <a-form-item label="启用"><a-switch v-model:checked="form.enabled" /></a-form-item>
      </a-form>
      <template #footer>
        <a-space>
          <a-button @click="open = false">取消</a-button>
          <a-button type="primary" :loading="saving" @click="save">保存</a-button>
        </a-space>
      </template>
    </a-drawer>

    <a-drawer v-model:open="runsOpen" :title="`评测记录 · ${runsSuite?.name ?? ''}`" :width="860">
      <a-spin :spinning="runsLoading">
        <a-collapse accordion>
          <a-collapse-panel v-for="r in runs" :key="r.id" :header="`第 ${r.round} 轮 · ${r.status}${r.passed ? ' · 达标' : r.passed === false ? ' · 未达标' : ''}`">
            <template #extra>
              <a-space>
                <a-tag :color="r.passed ? 'success' : r.passed === false ? 'error' : 'processing'">
                  {{ r.passed ? 'PASS' : r.passed === false ? 'FAIL' : r.status }}
                </a-tag>
                <a @click.stop="openDetail(r)">详情</a>
              </a-space>
            </template>
            <a-descriptions size="small" :column="2" bordered>
              <a-descriptions-item label="完成率">{{ (r.metrics.completion ?? 0).toFixed(2) }}</a-descriptions-item>
              <a-descriptions-item label="工具使用率">{{ (r.metrics.tool_use ?? 0).toFixed(2) }}</a-descriptions-item>
              <a-descriptions-item label="关键词命中">{{ r.metrics.keyword ?? '—' }}</a-descriptions-item>
              <a-descriptions-item label="平均 token">{{ r.metrics.avg_tokens ?? 0 }}</a-descriptions-item>
              <a-descriptions-item label="平均耗时">{{ r.metrics.avg_ms ?? 0 }} ms</a-descriptions-item>
              <a-descriptions-item label="任务">{{ (r.task_ids || []).join(', ') || '—' }}</a-descriptions-item>
            </a-descriptions>
            <a-alert
              v-if="r.metrics.failures && r.metrics.failures.length"
              type="warning"
              show-icon
              style="margin-top: 8px"
              :message="`未达标项：${r.metrics.failures.join('；')}`"
            />
            <div v-if="r.hints" style="margin-top: 8px">
              <b style="font-size: 12px">下一轮注入的反馈：</b>
              <pre class="hint">{{ r.hints }}</pre>
            </div>
          </a-collapse-panel>
        </a-collapse>
        <a-empty v-if="!runs.length" description="还没有评测记录" />
      </a-spin>
    </a-drawer>

    <a-modal v-model:open="detailOpen" title="本轮明细" :width="900" :footer="null">
      <template v-if="detail">
        <a-list :data-source="detail.detail || []" size="small">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-space direction="vertical" style="width: 100%">
                <div><b>用例：</b>{{ item.prompt }}</div>
                <a-table
                  size="small"
                  :pagination="false"
                  :data-source="item.agents"
                  :columns="[
                    { title: 'Agent', dataIndex: 'agent', width: 140 },
                    { title: '状态', dataIndex: 'status', width: 100 },
                    { title: '工具', dataIndex: 'tool_calls', width: 70 },
                    { title: '关键词', dataIndex: 'keyword_hit', width: 90 },
                    { title: 'token', dataIndex: 'tokens', width: 90 },
                    { title: '耗时', dataIndex: 'duration_ms', width: 100 },
                    { title: '输出', dataIndex: 'output', ellipsis: true },
                  ]"
                />
                <a-typography-text v-if="item.error" type="danger">{{ item.error }}</a-typography-text>
              </a-space>
            </a-list-item>
          </template>
        </a-list>
      </template>
    </a-modal>
  </a-card>
</template>

<style scoped>
.hint {
  white-space: pre-wrap;
  font-size: 11px;
  background: #fafafa;
  padding: 6px 8px;
  border-radius: 6px;
  margin: 4px 0 0;
}
</style>
