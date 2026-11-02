<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { api } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../labels'

interface Skill {
  id: number
  name: string
  description: string
  content: string
  files: { path: string; content: string }[]
  tags: string[]
  version: number
  enabled: boolean
  created_at: string
  updated_at: string
}

const skills = ref<Skill[]>([])
const loading = ref(false)
const open = ref(false)
const editing = ref<number | null>(null)
const saving = ref(false)

const form = reactive({
  name: '',
  description: '',
  content: '',
  tags: [] as string[],
  filesText: '',
  enabled: true,
})

const canWrite = computed(() => auth.can('operator'))
const canDelete = computed(() => auth.can('admin'))
const isEdit = computed(() => editing.value !== null)

const columns = [
  { title: '名称', dataIndex: 'name', width: 200 },
  { title: '说明', dataIndex: 'description', ellipsis: true },
  { title: '标签', key: 'tags', width: 220 },
  { title: '版本', dataIndex: 'version', width: 70 },
  { title: '更新', key: 'updated', width: 150 },
  { title: '启用', key: 'enabled', width: 70 },
  { title: '操作', key: 'actions', width: 150 },
]

async function load() {
  loading.value = true
  try {
    skills.value = await api<Skill[]>('/api/skills')
  } catch (e: any) {
    message.error(e.message)
  } finally {
    loading.value = false
  }
}

function reset() {
  editing.value = null
  Object.assign(form, { name: '', description: '', content: '', tags: [], filesText: '', enabled: true })
}

function openCreate() {
  reset()
  open.value = true
}

function openEdit(s: Skill) {
  editing.value = s.id
  Object.assign(form, {
    name: s.name,
    description: s.description,
    content: s.content,
    tags: [...(s.tags ?? [])],
    filesText: (s.files ?? []).length ? JSON.stringify(s.files, null, 2) : '',
    enabled: s.enabled,
  })
  open.value = true
}

async function save() {
  if (!form.name.trim()) {
    message.warning('请填写名称')
    return
  }
  let files: { path: string; content: string }[] = []
  if (form.filesText.trim()) {
    try {
      const parsed = JSON.parse(form.filesText)
      if (!Array.isArray(parsed)) throw new Error('必须是数组')
      files = parsed
    } catch (e: any) {
      message.error(`附加文件不是合法 JSON：${e.message}`)
      return
    }
  }
  const body = {
    name: form.name.trim(),
    description: form.description,
    content: form.content,
    tags: form.tags,
    files,
    enabled: form.enabled,
  }
  saving.value = true
  try {
    if (isEdit.value) await api(`/api/skills/${editing.value}`, { method: 'PATCH', body })
    else await api('/api/skills', { body })
    message.success('已保存')
    open.value = false
    await load()
  } catch (e: any) {
    message.error(e.message)
  } finally {
    saving.value = false
  }
}

async function toggle(s: Skill, enabled: boolean) {
  try {
    await api(`/api/skills/${s.id}`, { method: 'PATCH', body: { enabled } })
    s.enabled = enabled
  } catch (e: any) {
    message.error(e.message)
  }
}

function remove(s: Skill) {
  Modal.confirm({
    title: `删除 Skill「${s.name}」？`,
    okType: 'danger',
    onOk: async () => {
      try {
        await api(`/api/skills/${s.id}`, { method: 'DELETE' })
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
  <a-card title="Skill 管理" :bordered="false">
    <template #extra>
      <a-space>
        <a-button size="small" :loading="loading" @click="load">刷新</a-button>
        <a-button v-if="canWrite" type="primary" size="small" @click="openCreate">新建 Skill</a-button>
      </a-space>
    </template>

    <a-alert
      type="info"
      show-icon
      style="margin-bottom: 12px"
      message="Skill = 可复用的指令包：一段 Markdown 主文件 + 若干附加文件，Agent 可引用其名称与内容。"
    />

    <a-table :columns="columns" :data-source="skills" :loading="loading" row-key="id" :pagination="false" size="middle">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'tags'">
          <a-tag v-for="t in record.tags" :key="t">{{ t }}</a-tag>
          <span v-if="!record.tags?.length" style="color: #bbb">—</span>
        </template>
        <template v-else-if="column.key === 'updated'">
          <a-typography-text type="secondary">{{ fmtTime(record.updated_at) }}</a-typography-text>
        </template>
        <template v-else-if="column.key === 'enabled'">
          <a-switch :checked="record.enabled" :disabled="!canWrite" size="small" @change="(v: any) => toggle(record, !!v)" />
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button v-if="canWrite" size="small" @click="openEdit(record)">编辑</a-button>
            <a-button v-if="canDelete" size="small" danger @click="remove(record)">删除</a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-empty v-if="!loading && !skills.length" description="还没有 Skill" />

    <a-drawer v-model:open="open" :title="isEdit ? '编辑 Skill' : '新建 Skill'" :width="720">
      <a-form layout="vertical">
        <a-form-item label="名称" required>
          <a-input v-model:value="form.name" placeholder="例如 code-review / data-cleaning" />
        </a-form-item>
        <a-form-item label="说明">
          <a-input v-model:value="form.description" placeholder="一句话说明适用场景" />
        </a-form-item>
        <a-form-item label="标签">
          <a-select v-model:value="form.tags" mode="tags" placeholder="回车添加" :token-separators="[',']" />
        </a-form-item>
        <a-form-item label="主文件（Markdown）">
          <a-textarea v-model:value="form.content" :rows="14" class="mono" placeholder="# 目标&#10;&#10;1. ..." />
        </a-form-item>
        <a-form-item label="附加文件（JSON 数组）" extra='[{"path": "checklist.md", "content": "- [ ] ..."}]'>
          <a-textarea v-model:value="form.filesText" :rows="5" class="mono" />
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
  </a-card>
</template>
