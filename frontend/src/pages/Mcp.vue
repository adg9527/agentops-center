<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { api } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../labels'

interface McpServer {
  id: number
  name: string
  description: string
  transport: 'stdio' | 'streamable-http'
  command: string
  args: string[]
  url: string
  headers: Record<string, string>
  env: Record<string, string>
  enabled: boolean
  tools: { name: string; description?: string }[]
  tool_count: number
  last_check_at: string | null
  last_check_ok: boolean | null
  last_check_detail: string
  config_version: number
}

const servers = ref<McpServer[]>([])
const loading = ref(false)
const open = ref(false)
const editing = ref<number | null>(null)
const saving = ref(false)
const testing = ref<number | null>(null)
const testResult = ref<{ ok: boolean; detail: string; latency_ms: number; tools: any[] } | null>(null)
const testOpen = ref(false)

const form = reactive({
  name: '',
  description: '',
  transport: 'stdio' as 'stdio' | 'streamable-http',
  command: 'npx',
  argsText: '-y\n@modelcontextprotocol/server-everything',
  url: '',
  headersText: '',
  envText: '',
  enabled: true,
})

const canWrite = computed(() => auth.can('operator'))
const canDelete = computed(() => auth.can('admin'))
const isEdit = computed(() => editing.value !== null)

const columns = [
  { title: '名称', dataIndex: 'name', width: 180 },
  { title: '方式', key: 'transport', width: 130 },
  { title: '目标', key: 'target', ellipsis: true },
  { title: '工具', dataIndex: 'tool_count', width: 80 },
  { title: '最近检测', key: 'check', width: 190 },
  { title: '启用', key: 'enabled', width: 80 },
  { title: '操作', key: 'actions', width: 200 },
]

function parseJson(text: string, label: string): Record<string, string> {
  if (!text.trim()) return {}
  try {
    const value = JSON.parse(text)
    if (typeof value !== 'object' || Array.isArray(value)) throw new Error('必须是对象')
    return value
  } catch (e: any) {
    throw new Error(`${label} 不是合法 JSON：${e.message}`)
  }
}

async function load() {
  loading.value = true
  try {
    servers.value = await api<McpServer[]>('/api/mcp-servers')
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

function reset() {
  editing.value = null
  Object.assign(form, {
    name: '', description: '', transport: 'stdio', command: 'npx',
    argsText: '', url: '', headersText: '', envText: '', enabled: true,
  })
}

function openCreate() {
  reset()
  open.value = true
}

function openEdit(s: McpServer) {
  editing.value = s.id
  Object.assign(form, {
    name: s.name,
    description: s.description,
    transport: s.transport,
    command: s.command,
    argsText: (s.args ?? []).join('\n'),
    url: s.url,
    headersText: Object.keys(s.headers ?? {}).length ? JSON.stringify(s.headers, null, 2) : '',
    envText: Object.keys(s.env ?? {}).length ? JSON.stringify(s.env, null, 2) : '',
    enabled: s.enabled,
  })
  open.value = true
}

async function save() {
  if (!form.name.trim()) {
    message.warning('请填写名称')
    return
  }
  let headers: Record<string, string> = {}
  let env: Record<string, string> = {}
  try {
    headers = parseJson(form.headersText, '请求头')
    env = parseJson(form.envText, '环境变量')
  } catch (e: any) {
    message.error(e.message)
    return
  }
  const body = {
    name: form.name.trim(),
    description: form.description,
    transport: form.transport,
    command: form.transport === 'stdio' ? form.command.trim() : '',
    args: form.transport === 'stdio' ? form.argsText.split('\n').map((s) => s.trim()).filter(Boolean) : [],
    url: form.transport === 'streamable-http' ? form.url.trim() : '',
    headers,
    env,
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (isEdit.value) await api(`/api/mcp-servers/${editing.value}`, { method: 'PATCH', body })
    else await api('/api/mcp-servers', { body })
    message.success('已保存')
    open.value = false
    await load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    saving.value = false
  }
}

async function toggle(s: McpServer, enabled: boolean) {
  try {
    await api(`/api/mcp-servers/${s.id}`, { method: 'PATCH', body: { enabled } })
    s.enabled = enabled
    message.success(enabled ? '已启用' : '已停用')
  } catch (e: any) {
    message.error(e.message)
  }
}

async function test(s: McpServer) {
  testing.value = s.id
  try {
    const r = await api<{ ok: boolean; detail: string; latency_ms: number; tools: any[] }>(
      `/api/mcp-servers/${s.id}/test`,
      { method: 'POST', body: {} },
    )
    testResult.value = r
    testOpen.value = true
    await load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    testing.value = null
  }
}

function remove(s: McpServer) {
  Modal.confirm({
    title: `删除 MCP Server「${s.name}」？`,
    content: '软删除，已引用它的 Agent 需要重新配置。',
    okType: 'danger',
    onOk: async () => {
      try {
        await api(`/api/mcp-servers/${s.id}`, { method: 'DELETE' })
        message.success('已删除')
        await load()
      } catch (e: any) {
        message.error(e.message)
      }
    },
  })
}

onMounted(load)
</script>

<template>
  <a-card title="MCP 管理" :bordered="false">
    <template #extra>
      <a-space>
        <a-button size="small" :loading="loading" @click="load">刷新</a-button>
        <a-button v-if="canWrite" type="primary" size="small" @click="openCreate">接入 MCP Server</a-button>
      </a-space>
    </template>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="登记 Agent 可以使用的 MCP 服务；「测试连接」会真的握手并拉取 tools/list，结果留档。"
    />

    <a-table :columns="columns" :data-source="servers" :loading="loading" row-key="id" :pagination="false" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'transport'">
          <a-tag :color="record.transport === 'stdio' ? 'blue' : 'purple'">
            {{ record.transport === 'stdio' ? 'stdio' : 'HTTP' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'target'">
          <span class="mono">{{ record.transport === 'stdio' ? [record.command, ...record.args].join(' ') : record.url }}</span>
        </template>
        <template v-else-if="column.key === 'check'">
          <a-space direction="vertical" size="0">
            <a-tag :color="record.last_check_ok === true ? 'success' : record.last_check_ok === false ? 'error' : 'default'">
              {{ record.last_check_ok === true ? '通过' : record.last_check_ok === false ? '失败' : '未检测' }}
            </a-tag>
            <a-typography-text type="secondary" style="font-size: 11px">{{ fmtTime(record.last_check_at) }}</a-typography-text>
          </a-space>
        </template>
        <template v-else-if="column.key === 'enabled'">
          <a-switch :checked="record.enabled" :disabled="!canWrite" size="small" @change="(v: any) => toggle(record, !!v)" />
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button size="small" :loading="testing === record.id" @click="test(record)">测试</a-button>
            <a-button v-if="canWrite" size="small" @click="openEdit(record)">编辑</a-button>
            <a-button v-if="canDelete" size="small" danger @click="remove(record)">删除</a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && !servers.length" description="还没有接入 MCP Server" />

    <a-drawer v-model:open="open" :title="isEdit ? '编辑 MCP Server' : '接入 MCP Server'" :width="560">
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="例如 filesystem / github" />
        </a-form-item>
        <a-form-item label="说明">
          <a-input v-model:value="form.description" placeholder="这个服务提供什么能力" />
        </a-form-item>
        <a-form-item label="方式">
          <a-radio-group v-model:value="form.transport">
            <a-radio-button value="stdio">stdio（本地进程）</a-radio-button>
            <a-radio-button value="streamable-http">Streamable HTTP</a-radio-button>
          </a-radio-group>
        </a-form-item>

        <template v-if="form.transport === 'stdio'">
          <a-form-item label="启动命令" required>
            <a-input v-model:value="form.command" placeholder="npx / python / node" />
          </a-form-item>
          <a-form-item label="参数（每行一个）" extra="第一个参数通常就是 -y 或 -m 之类">
            <a-textarea v-model:value="form.argsText" :rows="5" class="mono" />
          </a-form-item>
        </template>
        <template v-else>
          <a-form-item label="URL" required>
            <a-input v-model:value="form.url" placeholder="http://127.0.0.1:8000/mcp/" />
          </a-form-item>
          <a-form-item label="请求头（JSON）" extra='例如 {"Authorization": "Bearer xxx"}'>
            <a-textarea v-model:value="form.headersText" :rows="4" class="mono" />
          </a-form-item>
        </template>

        <a-form-item label="环境变量（JSON）">
          <a-textarea v-model:value="form.envText" :rows="3" class="mono" placeholder="{}" />
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model:checked="form.enabled" />
        </a-form-item>
      </a-form>
      <template #footer>
        <a-space>
          <a-button @click="open = false">取消</a-button>
          <a-button type="primary" :loading="saving" @click="save">保存</a-button>
        </a-space>
      </template>
    </a-drawer>

    <a-modal v-model:open="testOpen" title="连接测试结果" :footer="null" :width="680">
      <template v-if="testResult">
        <a-result
          :status="testResult.ok ? 'success' : 'error'"
          :title="testResult.ok ? `握手成功（${testResult.latency_ms} ms）` : '连接失败'"
          :sub-title="testResult.detail"
        />
        <a-list v-if="testResult.tools.length" size="small" :data-source="testResult.tools" style="max-height: 320px; overflow: auto">
          <template #header><b>tools（{{ testResult.tools.length }}）</b></template>
          <template #renderItem="{ item }">
            <a-list-item>
              <a-space direction="vertical" size="0">
                <span class="mono">{{ item.name }}</span>
                <a-typography-text type="secondary" style="font-size: 12px">{{ item.description }}</a-typography-text>
              </a-space>
            </a-list-item>
          </template>
        </a-list>
      </template>
    </a-modal>
  </a-card>
</template>
