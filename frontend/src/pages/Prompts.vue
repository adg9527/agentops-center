<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { api } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../labels'

interface Prompt {
  id: number
  name: string
  description: string
  content: string
  variables: string[]
  tags: string[]
  version: number
  enabled: boolean
  created_at: string
  updated_at: string
}

const prompts = ref<Prompt[]>([])
const loading = ref(false)
const open = ref(false)
const editing = ref<number | null>(null)
const saving = ref(false)

const renderOpen = ref(false)
const renderTarget = ref<Prompt | null>(null)
const renderValues = reactive<Record<string, string>>({})
const rendered = ref('')
const missing = ref<string[]>([])

const form = reactive({ name: '', description: '', content: '', tags: [] as string[], enabled: true })

const canWrite = computed(() => auth.can('operator'))
const canDelete = computed(() => auth.can('admin'))
const isEdit = computed(() => editing.value !== null)
const liveVars = computed(() => {
  const found: string[] = []
  const re = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  let m: RegExpExecArray | null
  while ((m = re.exec(form.content))) if (!found.includes(m[1])) found.push(m[1])
  return found
})

const columns = [
  { title: '名称', dataIndex: 'name', width: 200 },
  { title: '说明', dataIndex: 'description', ellipsis: true },
  { title: '变量', key: 'vars', width: 240 },
  { title: '版本', dataIndex: 'version', width: 70 },
  { title: '更新', key: 'updated', width: 150 },
  { title: '操作', key: 'actions', width: 210 },
]

async function load() {
  loading.value = true
  try {
    prompts.value = await api<Prompt[]>('/api/prompts')
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

function reset() {
  editing.value = null
  Object.assign(form, { name: '', description: '', content: '', tags: [], enabled: true })
}

function openCreate() {
  reset()
  open.value = true
}

function openEdit(p: Prompt) {
  editing.value = p.id
  Object.assign(form, {
    name: p.name, description: p.description, content: p.content, tags: [...(p.tags ?? [])], enabled: p.enabled,
  })
  open.value = true
}

async function save() {
  if (!form.name.trim()) {
    message.warning('请填写名称')
    return
  }
  const body = {
    name: form.name.trim(),
    description: form.description,
    content: form.content,
    tags: form.tags,
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (isEdit.value) await api(`/api/prompts/${editing.value}`, { method: 'PATCH', body })
    else await api('/api/prompts', { body })
    message.success('已保存')
    open.value = false
    await load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    saving.value = false
  }
}

function openRender(p: Prompt) {
  renderTarget.value = p
  for (const key of Object.keys(renderValues)) delete renderValues[key]
  for (const v of p.variables ?? []) renderValues[v] = ''
  rendered.value = ''
  missing.value = []
  renderOpen.value = true
}

async function doRender() {
  if (!renderTarget.value) return
  try {
    const r = await api<{ rendered: string; missing: string[] }>(
      `/api/prompts/${renderTarget.value.id}/render`,
      { body: { variables: { ...renderValues } } },
    )
    rendered.value = r.rendered
    missing.value = r.missing
  } catch (e: any) {
    message.error(e.message)
  }
}

function remove(p: Prompt) {
  Modal.confirm({
    title: `删除 Prompt「${p.name}」？`,
    okType: 'danger',
    onOk: async () => {
      try {
        await api(`/api/prompts/${p.id}`, { method: 'DELETE' })
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
  <a-card title="Prompt 管理" :bordered="false">
    <template #extra>
      <a-space>
        <a-button size="small" :loading="loading" @click="load">刷新</a-button>
        <a-button v-if="canWrite" type="primary" size="small" @click="openCreate">新建 Prompt</a-button>
      </a-space>
    </template>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="Prompt 模板：用 {{变量名}} 占位，版本随每次修改自增；可以在这里填入变量做一次真实渲染预览。"
    />

    <a-table :columns="columns" :data-source="prompts" :loading="loading" row-key="id" :pagination="false" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'vars'">
          <a-tag v-for="v in record.variables" :key="v" color="blue">{{ v }}</a-tag>
          <span v-if="!record.variables?.length" style="color: #bbb">无变量</span>
        </template>
        <template v-else-if="column.key === 'updated'">
          <a-typography-text type="secondary">{{ fmtTime(record.updated_at) }}</a-typography-text>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button size="small" @click="openRender(record)">渲染</a-button>
            <a-button v-if="canWrite" size="small" @click="openEdit(record)">编辑</a-button>
            <a-button v-if="canDelete" size="small" danger @click="remove(record)">删除</a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && !prompts.length" description="还没有 Prompt" />

    <a-drawer v-model:open="open" :title="isEdit ? '编辑 Prompt' : '新建 Prompt'" :width="720">
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="例如 bug-report / code-review" />
        </a-form-item>
        <a-form-item label="说明">
          <a-input v-model:value="form.description" placeholder="一句话说明用途" />
        </a-form-item>
        <a-form-item label="标签">
          <a-select v-model:value="form.tags" mode="tags" placeholder="回车添加" :token-separators="[',']" />
        </a-form-item>
        <a-form-item label="模板内容">
          <a-textarea v-model:value="form.content" :rows="14" class="mono" placeholder="项目：{{project}}&#10;问题：{{issue}}" />
        </a-form-item>
        <a-form-item label="识别到的变量">
          <a-space wrap>
            <a-tag v-for="v in liveVars" :key="v" color="blue">{{ v }}</a-tag>
            <a-typography-text v-if="!liveVars.length" type="secondary">暂无</a-typography-text>
          </a-space>
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

    <a-modal v-model:open="renderOpen" title="渲染预览" :width="720" :footer="null">
      <template v-if="renderTarget">
        <a-form layout="vertical">
          <a-form-item v-for="v in renderTarget.variables" :key="v" :label="v">
            <a-input v-model:value="renderValues[v]" />
          </a-form-item>
        </a-form>
        <a-space style="margin-bottom: 12px">
          <a-button type="primary" size="small" @click="doRender">渲染</a-button>
          <a-typography-text v-if="missing.length" type="warning">缺少：{{ missing.join(', ') }}</a-typography-text>
        </a-space>
        <a-textarea v-model:value="rendered" :rows="12" readonly class="mono" />
      </template>
    </a-modal>
  </a-card>
</template>
