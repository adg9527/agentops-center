<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ApiOutlined, PlusOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons-vue'
import { api, type AdapterInfo, type Agent, type ConfigField, type Level, type Run } from '../api'
import { auth } from '../auth'
import { useLive } from '../live'
import { CAPABILITY_LABELS, fmtCost, fmtMs, fmtNum, fmtTime, health } from '../labels'
import StatusTag from '../components/StatusTag.vue'
import LevelTag from '../components/LevelTag.vue'

const router = useRouter()
const can = (role: 'admin' | 'operator' | 'viewer') => auth.can(role)

const agents = ref<Agent[]>([])
const adapters = ref<AdapterInfo[]>([])
const loading = ref(true)
const editing = ref<Agent | 'new' | null>(null)
const detail = ref<Agent | null>(null)
const testing = ref<number | null>(null)

// ---- list ----
async function load() {
  try {
    const list = await api<Agent[]>('/api/agents')
    agents.value = list
    if (detail.value) detail.value = list.find((x) => x.id === detail.value!.id) ?? null
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  load()
  api<AdapterInfo[]>('/api/adapters')
    .then((a) => (adapters.value = a))
    .catch((e: any) => message.error(e.message))
})

useLive((m) => {
  if (m.topic === 'agent_updated') load()
})

const columns = [
  { key: 'name', title: '名称', dataIndex: 'name' },
  { key: 'platform', title: '平台 / 接入方式' },
  { key: 'model', title: '模型', dataIndex: 'model' },
  { key: 'status', title: '状态' },
  { key: 'version', title: '版本' },
  { key: 'last_seen_at', title: '最后在线', dataIndex: 'last_seen_at' },
  { key: 'actions', title: '操作', fixed: 'right' as const, width: 230 },
]

function agentRow(a: Agent) {
  return { onClick: () => openDetail(a), style: { cursor: 'pointer' } }
}

function badgeStatus(a: Agent): 'success' | 'error' | 'default' {
  if (!a.enabled) return 'default'
  const c = health(a.health).color
  return c === 'success' || c === 'error' ? c : 'default'
}

// ---- test connection ----
const testResult = reactive({
  open: false,
  ok: false,
  name: '',
  detail: '',
  version: '',
  latency_ms: null as number | null,
  models: 0,
})
const testTitle = computed(() => `${testResult.name} ${testResult.ok ? '连接正常' : '连接失败'}`)

async function testAgent(a: Agent) {
  testing.value = a.id
  try {
    const r = await api<any>(`/api/agents/${a.id}/test`, { method: 'POST' })
    testResult.name = a.name
    testResult.ok = !!r.ok
    testResult.detail = r.detail ?? '—'
    testResult.version = r.version ?? ''
    testResult.latency_ms = r.latency_ms ?? null
    testResult.models = Array.isArray(r.detected?.models) ? r.detected.models.length : 0
    testResult.open = true
    load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    testing.value = null
  }
}

// ---- enable / disable / delete ----
async function toggleAgent(a: Agent, on: boolean) {
  try {
    await api(`/api/agents/${a.id}/${on ? 'enable' : 'disable'}`, { method: 'POST' })
    message.success(on ? '已启用' : '已停用：新任务将不会分配给该 Agent')
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

async function removeAgent() {
  if (!detail.value) return
  try {
    await api(`/api/agents/${detail.value.id}`, { method: 'DELETE' })
    message.success('已删除（执行历史保留）')
    closeDetail()
    load()
  } catch (e: any) {
    message.error(e.message)
  }
}

// ---- form drawer ----
const formRef = ref<any>()
const formType = ref<string>('')
const saving = ref(false)
const clearSecrets = ref<string[]>([])
const models = ref<string[]>([])
const formModel = reactive<Record<string, any>>({
  name: '',
  description: '',
  role: '',
  capability_tags: [] as string[],
  model: '',
  max_concurrency: 1,
  timeout_sec: 600,
  max_retries: 0,
  config: {} as Record<string, any>,
  secrets: {} as Record<string, string>,
})

const editingAgent = computed<Agent | null>(() => (editing.value && editing.value !== 'new' ? editing.value : null))
const editingTitle = computed(() =>
  editingAgent.value ? `编辑 ${editingAgent.value.name}` : '接入 Agent',
)
const adapter = computed<AdapterInfo | undefined>(() =>
  adapters.value.find((a) => a.type === formType.value),
)

const adapterOptions = computed(() =>
  adapters.value.map((a) => ({
    value: a.type,
    label: `${a.display_name} · ${a.transport.toUpperCase()}${a.verified ? '' : '（未验证）'}`,
  })),
)

const modelOptions = computed(() => {
  const list = models.value.length ? models.value : (editingAgent.value?.detected?.check?.models ?? [])
  return (list as string[]).map((m) => ({ value: m }))
})

function buildModel() {
  const a = editingAgent.value
  const ad = adapter.value
  const cfg: Record<string, any> = {}
  for (const f of ad?.config_fields ?? []) {
    if (f.secret) continue
    const v = a && a.adapter_type === formType.value ? a.config[f.key] ?? f.default : f.default
    cfg[f.key] = f.type === 'json' ? JSON.stringify(v ?? {}, null, 0) : v
  }
  Object.assign(formModel, {
    name: a?.name ?? ad?.display_name ?? '',
    description: a?.description ?? '',
    role: a?.role ?? '',
    capability_tags: a?.capability_tags ?? [],
    model: a?.model ?? '',
    max_concurrency: a?.max_concurrency ?? 1,
    timeout_sec: a?.timeout_sec ?? 600,
    max_retries: a?.max_retries ?? 0,
    config: cfg,
    secrets: {},
  })
}

async function loadModels() {
  models.value = []
  if (!formType.value) return
  try {
    const r = await api<{ models: string[] }>(`/api/adapters/${formType.value}/models`)
    models.value = r.models ?? []
  } catch {
    models.value = []
  }
}

function openEditor(a: Agent | 'new') {
  editing.value = a
  clearSecrets.value = []
  formType.value =
    a === 'new' ? adapters.value.find((x) => x.verified)?.type ?? adapters.value[0]?.type ?? '' : a.adapter_type
  buildModel()
  loadModels()
}

function closeEditor() {
  editing.value = null
  models.value = []
}

function openDetail(a: Agent) {
  detail.value = a
  detailTab.value = 'info'
}

function closeDetail() {
  detail.value = null
  runs.value = []
  stats.value = null
}

watch(formType, () => {
  clearSecrets.value = []
  formModel.secrets = {}
  if (editing.value === 'new') {
    buildModel()
    loadModels()
  }
})

function fieldRules(f: ConfigField) {
  return f.required && !f.secret ? [{ required: true, message: `请填写${f.label}` }] : []
}

function fieldOptions(f: ConfigField) {
  return (f.options ?? []).map((o) => ({ label: o, value: o }))
}

function markClear(key: string) {
  clearSecrets.value = [...clearSecrets.value, key]
  message.info('保存后将清除该密钥')
}

function filterModel(input: string, option: any) {
  return String(option?.value ?? '').toLowerCase().includes(input.toLowerCase())
}

const modelExtra = computed(() =>
  formType.value === 'opencode'
    ? `必须是「provider/模型名」格式，如 opencode/big-pickle（本机检测到 ${models.value.length} 个可选模型）`
    : '留空使用 Agent 默认模型。Claude Code 例如 haiku / sonnet',
)

async function saveAgent() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  const ad = adapter.value
  if (!ad) {
    message.error('请选择接入类型')
    return
  }
  const config: Record<string, any> = {}
  const secrets: Record<string, string | null> = {}
  for (const f of ad.config_fields) {
    const raw = formModel.config?.[f.key]
    if (f.secret) {
      const secret = formModel.secrets?.[f.key]
      if (secret) secrets[f.key] = secret
      else if (clearSecrets.value.includes(f.key)) secrets[f.key] = null
      continue
    }
    if (f.type === 'json') {
      try {
        config[f.key] = raw ? JSON.parse(raw) : {}
      } catch {
        message.error(`${f.label} 不是合法 JSON`)
        return
      }
    } else {
      config[f.key] = raw
    }
  }
  const body: Record<string, any> = { ...formModel, config, secrets }
  saving.value = true
  try {
    if (editingAgent.value) await api(`/api/agents/${editingAgent.value.id}`, { method: 'PATCH', body })
    else await api('/api/agents', { body: { ...body, adapter_type: formType.value } })
    message.success('已保存')
    editing.value = null
    load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    saving.value = false
  }
}

// ---- detail drawer ----
const detailTab = ref('info')
const runs = ref<Run[]>([])
const stats = ref<any>(null)

const detailAdapter = computed<AdapterInfo | undefined>(() =>
  adapters.value.find((a) => a.type === detail.value?.adapter_type),
)
const runtime = computed<any>(() => detail.value?.detected?.runtime)

const capColumns = [
  { key: 'k', title: '能力' },
  { key: 'v', title: '支持程度' },
]
const capRows = computed<{ k: string; v: Level }[]>(() =>
  Object.entries(detailAdapter.value?.capabilities ?? detail.value?.capabilities ?? {}).map(([k, v]) => ({
    k,
    v: v as Level,
  })),
)

const runColumns = [
  { key: 'task', title: '任务' },
  { key: 'status', title: '状态', dataIndex: 'status' },
  { key: 'duration', title: '耗时', dataIndex: 'duration_ms' },
  { key: 'token', title: 'Token' },
  { key: 'cost', title: '成本', dataIndex: 'cost_usd' },
  { key: 'created', title: '时间', dataIndex: 'created_at' },
]

function runRow(r: Run) {
  return { onClick: () => router.push(`/tasks/${r.task_id}`), style: { cursor: 'pointer' } }
}

const statusEntries = computed<[string, number][]>(() =>
  stats.value ? (Object.entries(stats.value.by_status ?? {}) as [string, number][]) : [],
)

async function loadDetailData() {
  if (!detail.value) return
  const id = detail.value.id
  runs.value = []
  stats.value = null
  try {
    runs.value = await api<Run[]>(`/api/agents/${id}/runs`)
  } catch {
    runs.value = []
  }
  try {
    stats.value = await api<any>(`/api/agents/${id}/stats`)
  } catch {
    stats.value = null
  }
}

watch(() => [detail.value?.id, detail.value?.updated_at] as const, loadDetailData)
</script>

<template>
  <a-card title="Agent 接入管理">
    <template #extra>
      <a-space>
        <a-button @click="load">
          <template #icon><ReloadOutlined /></template>
        </a-button>
        <a-button v-if="can('admin')" type="primary" @click="openEditor('new')">
          <template #icon><PlusOutlined /></template>
          接入 Agent
        </a-button>
      </a-space>
    </template>

    <a-table
      row-key="id"
      :loading="loading"
      :data-source="agents"
      :columns="columns"
      :scroll="{ x: 1000 }"
      :custom-row="agentRow"
      :locale="{ emptyText: '尚未接入 Agent。点击右上角「接入 Agent」，可接入本机 Claude Code、OpenCode 等。' }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a-space direction="vertical" :size="0">
            <b>{{ record.name }}</b>
            <a-typography-text type="secondary" :style="{ fontSize: '12px' }">
              {{ record.role || record.description?.slice(0, 40) || '—' }}
            </a-typography-text>
          </a-space>
        </template>

        <template v-else-if="column.key === 'platform'">
          <a-space :size="4" wrap>
            <a-tag>{{ record.adapter_name }}</a-tag>
            <a-tag color="geekblue">{{ String(record.transport).toUpperCase() }}</a-tag>
            <a-tooltip v-if="!record.adapter_verified" title="该 Adapter 未在本环境经过端到端验证">
              <a-tag color="purple">未验证</a-tag>
            </a-tooltip>
          </a-space>
        </template>

        <template v-else-if="column.key === 'model'">
          <template v-if="record.model || record.detected?.runtime?.model">
            {{ record.model || record.detected?.runtime?.model }}
          </template>
          <a-typography-text v-else type="secondary">Agent 默认</a-typography-text>
        </template>

        <template v-else-if="column.key === 'status'">
          <a-space direction="vertical" :size="0">
            <a-badge
              :status="badgeStatus(record)"
              :text="record.enabled ? health(record.health).text : '已停用'"
            />
            <a-tooltip v-if="record.risk_warnings.length > 0" :title="record.risk_warnings.join('；')">
              <a-tag color="warning"><WarningOutlined /> 高风险配置</a-tag>
            </a-tooltip>
          </a-space>
        </template>

        <template v-else-if="column.key === 'version'">
          {{ record.detected?.version ?? '—' }}
          <br />
          <a-typography-text type="secondary" :style="{ fontSize: '12px' }">
            配置 v{{ record.config_version }}
          </a-typography-text>
        </template>

        <template v-else-if="column.key === 'last_seen_at'">
          {{ fmtTime(record.last_seen_at) }}
        </template>

        <template v-else-if="column.key === 'actions'">
          <a-space @click.stop>
            <a-button
              size="small"
              :loading="testing === record.id"
              :disabled="!can('operator')"
              @click="testAgent(record)"
            >
              <template #icon><ApiOutlined /></template>
              测试连接
            </a-button>
            <a-switch
              v-if="can('admin')"
              size="small"
              :checked="record.enabled"
              checked-children="启用"
              un-checked-children="停用"
              @change="toggleAgent(record, $event)"
            />
            <a-button v-if="can('admin')" size="small" type="link" @click="openEditor(record)">编辑</a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 接入 / 编辑 Agent -->
    <a-drawer
      v-if="editing"
      :open="true"
      :width="560"
      :title="editingTitle"
      @close="closeEditor"
    >
      <template #extra>
        <a-button type="primary" :loading="saving" @click="saveAgent">保存</a-button>
      </template>

      <div v-if="editing === 'new'" class="type-block">
        <div class="type-label">接入类型 <span class="req">*</span></div>
        <a-select v-model:value="formType" :options="adapterOptions" style="width: 100%" />
      </div>

      <a-alert
        v-if="adapter"
        style="margin-bottom: 16px"
        :type="adapter.verified ? 'info' : 'warning'"
        show-icon
        :message="adapter.platform"
        :description="adapter.description"
      />

      <a-form ref="formRef" :model="formModel" layout="vertical">
        <a-form-item name="name" label="名称" :rules="[{ required: true, message: '请输入名称' }]">
          <a-input v-model:value="formModel.name" />
        </a-form-item>
        <a-form-item name="role" label="角色">
          <a-input v-model:value="formModel.role" placeholder="如：代码实现 / 代码审查 / 需求分析" />
        </a-form-item>
        <a-form-item name="description" label="能力描述">
          <a-textarea v-model:value="formModel.description" :rows="2" />
        </a-form-item>
        <a-form-item name="capability_tags" label="能力标签">
          <a-select v-model:value="formModel.capability_tags" mode="tags" style="width: 100%" />
        </a-form-item>
        <a-form-item name="model" label="模型" :extra="modelExtra">
          <a-auto-complete
            v-model:value="formModel.model"
            allow-clear
            :filter-option="filterModel"
            :options="modelOptions"
          />
        </a-form-item>

        <a-typography-title :level="5">连接与启动配置</a-typography-title>
        <a-form-item
          v-for="f in adapter?.config_fields ?? []"
          :key="f.key"
          :name="f.secret ? ['secrets', f.key] : ['config', f.key]"
          :rules="fieldRules(f)"
        >
          <template #label>
            <a-space :size="4">
              {{ f.label }}
              <a-tag v-if="f.secret" color="gold">加密存储</a-tag>
            </a-space>
          </template>
          <template #extra>
            {{ f.help }}
            <template v-if="f.secret && editingAgent?.secret_keys_set.includes(f.key)">
              ·
              <a v-if="!clearSecrets.includes(f.key)" @click="markClear(f.key)">清除已设置的值</a>
              <span v-else>保存后将清除</span>
            </template>
          </template>

          <a-switch v-if="f.type === 'bool'" v-model:checked="formModel.config[f.key]" />
          <a-input-password
            v-else-if="f.secret"
            v-model:value="formModel.secrets[f.key]"
            :placeholder="editingAgent?.secret_keys_set.includes(f.key) ? '已设置（留空保持不变）' : '未设置'"
            autocomplete="new-password"
          />
          <a-textarea v-else-if="f.type === 'text'" v-model:value="formModel.config[f.key]" :rows="3" />
          <a-input-number
            v-else-if="f.type === 'number'"
            v-model:value="formModel.config[f.key]"
            style="width: 100%"
          />
          <a-select
            v-else-if="f.type === 'string_list'"
            v-model:value="formModel.config[f.key]"
            mode="tags"
            :token-separators="[',', ' ']"
            style="width: 100%"
          />
          <a-select
            v-else-if="f.type === 'select'"
            v-model:value="formModel.config[f.key]"
            :options="fieldOptions(f)"
            style="width: 100%"
          />
          <a-textarea
            v-else-if="f.type === 'json'"
            v-model:value="formModel.config[f.key]"
            :rows="3"
            class="mono"
          />
          <a-input v-else v-model:value="formModel.config[f.key]" />
        </a-form-item>

        <a-typography-title :level="5">执行控制</a-typography-title>
        <a-space wrap>
          <a-form-item name="max_concurrency" label="最大并发">
            <a-input-number v-model:value="formModel.max_concurrency" :min="1" :max="16" />
          </a-form-item>
          <a-form-item name="timeout_sec" label="超时（秒）">
            <a-input-number v-model:value="formModel.timeout_sec" :min="10" :max="86400" />
          </a-form-item>
          <a-form-item name="max_retries" label="失败重试次数">
            <a-input-number v-model:value="formModel.max_retries" :min="0" :max="5" />
          </a-form-item>
        </a-space>
      </a-form>
    </a-drawer>

    <!-- Agent 详情 -->
    <a-drawer v-if="detail" :open="true" :width="760" :title="detail.name" @close="closeDetail">
      <template #extra>
        <a-space>
          <a-button :disabled="!can('operator')" @click="testAgent(detail)">测试连接</a-button>
          <a-button v-if="can('admin')" @click="openEditor(detail)">编辑</a-button>
          <a-popconfirm
            v-if="can('admin')"
            title="删除该 Agent？"
            description="执行历史与 Trace 会保留。"
            @confirm="removeAgent"
          >
            <a-button danger>删除</a-button>
          </a-popconfirm>
        </a-space>
      </template>

      <a-alert
        v-if="detail.risk_warnings.length > 0"
        type="warning"
        show-icon
        style="margin-bottom: 12px"
        message="高风险配置"
        :description="detail.risk_warnings.join('；')"
      />

      <a-tabs v-model:active-key="detailTab">
        <a-tab-pane key="info" tab="概览">
          <a-descriptions :column="2" size="small" bordered>
            <a-descriptions-item label="接入类型">{{ detail.adapter_name }}</a-descriptions-item>
            <a-descriptions-item label="接入方式">
              {{ String(detail.transport).toUpperCase() }}
            </a-descriptions-item>
            <a-descriptions-item label="来源平台">{{ detail.platform }}</a-descriptions-item>
            <a-descriptions-item label="Adapter 验证">
              <a-tag v-if="detail.adapter_verified" color="success">本环境已验证</a-tag>
              <a-tag v-else color="purple">未验证</a-tag>
            </a-descriptions-item>
            <a-descriptions-item label="状态">
              {{ detail.enabled ? health(detail.health).text : '已停用' }}
            </a-descriptions-item>
            <a-descriptions-item label="Agent 版本">
              {{ detail.detected?.version ?? '未检测' }}
            </a-descriptions-item>
            <a-descriptions-item label="配置模型">{{ detail.model || 'Agent 默认' }}</a-descriptions-item>
            <a-descriptions-item label="实际模型">
              {{ runtime?.model ?? '首次执行后检测' }}
            </a-descriptions-item>
            <a-descriptions-item label="配置版本">v{{ detail.config_version }}</a-descriptions-item>
            <a-descriptions-item label="并发 / 超时 / 重试">
              {{ detail.max_concurrency }} / {{ detail.timeout_sec }}s / {{ detail.max_retries }}
            </a-descriptions-item>
            <a-descriptions-item label="最后在线">{{ fmtTime(detail.last_seen_at) }}</a-descriptions-item>
            <a-descriptions-item label="最后检测">{{ fmtTime(detail.last_check_at) }}</a-descriptions-item>
            <a-descriptions-item label="已设置密钥" :span="2">
              <template v-if="detail.secret_keys_set.length">
                <a-tag v-for="k in detail.secret_keys_set" :key="k">{{ k }}: ******</a-tag>
              </template>
              <template v-else>无</template>
            </a-descriptions-item>
            <a-descriptions-item label="实测可用工具" :span="2">
              <template v-if="runtime?.tools">
                <a-tag v-for="t in runtime.tools" :key="t">{{ t }}</a-tag>
              </template>
              <a-typography-text v-else type="secondary">
                {{ detail.adapter_type === 'claude_code' ? '首次执行任务后由 init 事件检测' : '该平台不上报工具清单' }}
              </a-typography-text>
            </a-descriptions-item>
            <a-descriptions-item label="实测 MCP" :span="2">
              <template v-if="runtime?.mcp_servers">
                <template v-if="runtime.mcp_servers.length">
                  <a-tag v-for="m in runtime.mcp_servers" :key="m.name">{{ m.name }} ({{ m.status }})</a-tag>
                </template>
                <template v-else>无（已隔离）</template>
              </template>
              <a-typography-text v-else type="secondary">不支持或未检测</a-typography-text>
            </a-descriptions-item>
            <a-descriptions-item label="配置" :span="2">
              <pre class="config-pre">{{ JSON.stringify(detail.config, null, 2) }}</pre>
            </a-descriptions-item>
          </a-descriptions>
        </a-tab-pane>

        <a-tab-pane key="caps" tab="能力矩阵">
          <a-table size="small" :pagination="false" row-key="k" :data-source="capRows" :columns="capColumns">
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'k'">{{ CAPABILITY_LABELS[record.k] ?? record.k }}</template>
              <template v-else><LevelTag :level="record.v" /></template>
            </template>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="runs" :tab="`执行历史 (${runs.length})`">
          <a-table
            size="small"
            row-key="id"
            :data-source="runs"
            :columns="runColumns"
            :pagination="{ pageSize: 10 }"
            :custom-row="runRow"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'task'">#{{ record.task_id }} {{ record.task_title }}</template>
              <template v-else-if="column.key === 'status'"><StatusTag :status="record.status" /></template>
              <template v-else-if="column.key === 'duration'">{{ fmtMs(record.duration_ms) }}</template>
              <template v-else-if="column.key === 'token'">
                {{ fmtNum(record.input_tokens) }} / {{ fmtNum(record.output_tokens) }}
              </template>
              <template v-else-if="column.key === 'cost'">{{ fmtCost(record.cost_usd) }}</template>
              <template v-else-if="column.key === 'created'">{{ fmtTime(record.created_at) }}</template>
            </template>
          </a-table>
        </a-tab-pane>

        <a-tab-pane key="stats" tab="调用统计">
          <a-descriptions v-if="stats" :column="2" size="small" bordered>
            <a-descriptions-item label="统计范围">近 {{ stats.days }} 天</a-descriptions-item>
            <a-descriptions-item label="执行次数">{{ stats.total }}</a-descriptions-item>
            <a-descriptions-item label="成功率">
              {{ stats.success_rate == null ? '—' : `${(stats.success_rate * 100).toFixed(1)}%` }}
            </a-descriptions-item>
            <a-descriptions-item label="平均耗时">{{ fmtMs(stats.avg_duration_ms) }}</a-descriptions-item>
            <a-descriptions-item label="Token 入 / 出">
              {{ fmtNum(stats.input_tokens) }} / {{ fmtNum(stats.output_tokens) }}
            </a-descriptions-item>
            <a-descriptions-item label="成本">
              {{ fmtCost(stats.cost_usd) }}（{{ stats.cost_known_runs }}/{{ stats.total }} 次可获取）
            </a-descriptions-item>
            <a-descriptions-item label="状态分布" :span="2">
              <span v-for="[k, v] in statusEntries" :key="k" class="status-cell">
                <StatusTag :status="k" />{{ v }}
              </span>
            </a-descriptions-item>
          </a-descriptions>
          <a-empty v-else description="暂无统计数据" />
        </a-tab-pane>

        <a-tab-pane key="evals" tab="评测历史">
          <a-empty description="自动化评测在第 5 阶段实现" />
        </a-tab-pane>
      </a-tabs>
    </a-drawer>

    <!-- 连接测试结果 -->
    <a-modal
      v-model:open="testResult.open"
      :title="testTitle"
      :width="440"
      :footer="null"
      @ok="testResult.open = false"
    >
      <a-descriptions :column="1" size="small">
        <a-descriptions-item label="结果">{{ testResult.detail }}</a-descriptions-item>
        <a-descriptions-item label="版本">{{ testResult.version || '—' }}</a-descriptions-item>
        <a-descriptions-item label="耗时">{{ fmtMs(testResult.latency_ms) }}</a-descriptions-item>
        <a-descriptions-item v-if="testResult.models > 0" label="可用模型">
          {{ testResult.models }} 个
        </a-descriptions-item>
      </a-descriptions>
      <div class="modal-actions">
        <a-button type="primary" @click="testResult.open = false">知道了</a-button>
      </div>
    </a-modal>
  </a-card>
</template>

<style scoped>
.type-block {
  margin-bottom: 16px;
}
.type-label {
  margin-bottom: 8px;
  font-size: 14px;
}
.req {
  color: #ff4d4f;
}
.config-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  max-height: 280px;
  overflow: auto;
}
.status-cell {
  margin-right: 8px;
}
.modal-actions {
  margin-top: 16px;
  text-align: right;
}
</style>
