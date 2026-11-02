<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import type { FormInstance, TableColumnsType, TablePaginationConfig } from 'ant-design-vue'
import { PlusOutlined } from '@ant-design/icons-vue'
import { api, type User } from '../api'
import { auth } from '../auth'
import { fmtTime } from '../labels'

interface Project {
  id: number
  name: string
  description: string
}

interface AuditRow {
  id: number
  username: string
  action: string
  target_type: string | null
  target_id: number | null
  detail: unknown
  ip: string
  created_at: string
}

interface AuditPage {
  total: number
  items: AuditRow[]
}

const ROLES = [
  { value: 'admin', label: 'admin（管理员：接入/编辑 Agent、用户管理）' },
  { value: 'operator', label: 'operator（操作员：发送/终止任务、测试连接）' },
  { value: 'viewer', label: 'viewer（只读）' },
] as const
const ROLE_VALUES = ROLES.map((r) => ({ value: r.value, label: r.value }))

const isAdmin = computed(() => auth.can('admin'))
const meId = computed(() => auth.state.user?.id ?? 0)

const tab = ref('users')

// ---- users ----
const users = ref<User[]>([])
const userColumns: TableColumnsType<User> = [
  { title: '用户名', dataIndex: 'username', key: 'username' },
  { title: '角色', key: 'role' },
  { title: '启用', key: 'active' },
]
const userModalOpen = ref(false)
const userFormRef = ref<FormInstance | null>(null)
const userForm = ref<{ username: string; password: string; role: User['role'] }>({
  username: '',
  password: '',
  role: 'operator',
})
const userRules = {
  username: [
    { required: true, message: '请输入用户名' },
    { min: 2, message: '用户名至少 2 位' },
  ],
  password: [
    { required: true, message: '请输入初始密码' },
    { min: 8, message: '初始密码至少 8 位' },
  ],
}

async function loadUsers() {
  try {
    users.value = await api<User[]>('/api/users')
  } catch (e) {
    message.error(e instanceof Error ? e.message : '用户列表加载失败')
  }
}

async function patchUser(u: User, body: Partial<User>) {
  try {
    await api<User>(`/api/users/${u.id}`, { method: 'PATCH', body })
    await loadUsers()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '更新失败')
  }
}

function onRoleChange(u: User, v: unknown) {
  if (v === 'admin' || v === 'operator' || v === 'viewer') {
    void patchUser(u, { role: v })
  }
}

function toRole(v: unknown): User['role'] {
  return v === 'admin' || v === 'operator' || v === 'viewer' ? v : 'operator'
}

function onActiveChange(u: User, v: unknown) {
  if (typeof v === 'boolean') {
    void patchUser(u, { is_active: v })
  }
}

function openUserModal() {
  userForm.value = { username: '', password: '', role: 'operator' }
  userModalOpen.value = true
  void nextTick(() => userFormRef.value?.clearValidate())
}

async function submitUser() {
  try {
    await userFormRef.value?.validate()
  } catch {
    return
  }
  try {
    await api<User>('/api/users', { body: { ...userForm.value } })
    message.success('用户已创建')
    userModalOpen.value = false
    userForm.value = { username: '', password: '', role: 'operator' }
    await loadUsers()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '创建用户失败')
  }
}

// ---- projects ----
const projects = ref<Project[]>([])
const projectsLoaded = ref(false)
const projectFormRef = ref<FormInstance | null>(null)
const projectForm = ref({ name: '', description: '' })
const projectRules = {
  name: [{ required: true, message: '请输入项目名称' }],
}
const projectColumns: TableColumnsType<Project> = [
  { title: 'ID', dataIndex: 'id', width: 60 },
  { title: '名称', dataIndex: 'name' },
  { title: '描述', dataIndex: 'description' },
]

async function loadProjects() {
  try {
    projects.value = await api<Project[]>('/api/projects')
    projectsLoaded.value = true
  } catch (e) {
    message.error(e instanceof Error ? e.message : '项目列表加载失败')
  }
}

async function createProject() {
  try {
    await api<Project>('/api/projects', { body: { ...projectForm.value } })
    projectForm.value = { name: '', description: '' }
    projectFormRef.value?.clearValidate()
    await loadProjects()
  } catch (e) {
    message.error(e instanceof Error ? e.message : '创建项目失败')
  }
}

// ---- audit ----
const audit = ref<AuditPage>({ total: 0, items: [] })
const auditPage = ref(1)
const auditAction = ref('')
const auditInput = ref('')
const auditLoaded = ref(false)
const auditColumns: TableColumnsType<AuditRow> = [
  { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 130 },
  { title: '用户', dataIndex: 'username', width: 100 },
  { title: '操作', dataIndex: 'action', width: 150 },
  { title: '对象', key: 'target', width: 100 },
  { title: 'IP', dataIndex: 'ip', width: 110 },
  { title: '详情', dataIndex: 'detail', key: 'detail', ellipsis: true },
]
const auditPagination = computed<TablePaginationConfig>(() => ({
  current: auditPage.value,
  pageSize: 50,
  total: audit.value.total,
  showSizeChanger: false,
}))

async function loadAudit() {
  const q = new URLSearchParams({ limit: '50', offset: String((auditPage.value - 1) * 50) })
  if (auditAction.value) q.set('action', auditAction.value)
  try {
    audit.value = await api<AuditPage>(`/api/audit-logs?${q}`)
    auditLoaded.value = true
  } catch (e) {
    message.error(e instanceof Error ? e.message : '审计日志加载失败')
  }
}

function onAuditSearch(v: string) {
  if (auditAction.value === v && auditPage.value === 1) {
    void loadAudit()
    return
  }
  auditAction.value = v
  auditPage.value = 1
}

function onAuditTableChange(p: TablePaginationConfig) {
  auditPage.value = p.current ?? 1
}

watch(tab, (key) => {
  if (key === 'projects' && !projectsLoaded.value) void loadProjects()
  if (key === 'audit' && !auditLoaded.value) void loadAudit()
})

watch([auditPage, auditAction], () => {
  if (tab.value === 'audit') void loadAudit()
})

onMounted(() => {
  if (isAdmin.value) void loadUsers()
})
</script>

<template>
  <a-card v-if="!isAdmin">
    <a-result status="403" title="需要管理员权限" />
  </a-card>

  <a-card v-else title="系统设置">
    <a-tabs v-model:active-key="tab">
      <a-tab-pane key="users" tab="用户与权限">
        <a-button style="margin-bottom: 12px" @click="openUserModal">
          <template #icon><PlusOutlined /></template>
          新建用户
        </a-button>

        <a-table
          size="small"
          row-key="id"
          :data-source="users"
          :columns="userColumns"
          :pagination="false"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'role'">
              <a-select
                size="small"
                style="width: 120px"
                :value="record.role"
                :disabled="record.id === meId"
                :options="ROLE_VALUES"
                @change="(v: unknown) => onRoleChange(record, v)"
              />
            </template>
            <template v-else-if="column.key === 'active'">
              <a-switch
                size="small"
                :checked="record.is_active"
                :disabled="record.id === meId"
                @change="(v: unknown) => onActiveChange(record, v)"
              />
            </template>
          </template>
        </a-table>

        <a-modal
          :open="userModalOpen"
          title="新建用户"
          @cancel="userModalOpen = false"
          @ok="submitUser"
        >
          <a-form ref="userFormRef" layout="vertical" :model="userForm">
            <a-form-item name="username" label="用户名" :rules="userRules.username">
              <a-input v-model:value="userForm.username" />
            </a-form-item>
            <a-form-item name="password" label="初始密码" :rules="userRules.password">
              <a-input-password v-model:value="userForm.password" autocomplete="new-password" />
            </a-form-item>
            <a-form-item name="role" label="角色">
              <a-select
                :value="userForm.role"
                :options="ROLES"
                @change="(v: unknown) => (userForm.role = toRole(v))"
              />
            </a-form-item>
          </a-form>
        </a-modal>
      </a-tab-pane>

      <a-tab-pane key="projects" tab="项目">
        <a-form
          ref="projectFormRef"
          layout="inline"
          style="margin-bottom: 12px"
          :model="projectForm"
          :rules="projectRules"
          @finish="createProject"
        >
          <a-form-item name="name">
            <a-input v-model:value="projectForm.name" placeholder="项目名称" />
          </a-form-item>
          <a-form-item name="description">
            <a-input v-model:value="projectForm.description" placeholder="描述" />
          </a-form-item>
          <a-button html-type="submit">
            <template #icon><PlusOutlined /></template>
            新建项目
          </a-button>
        </a-form>

        <a-table
          size="small"
          row-key="id"
          :data-source="projects"
          :columns="projectColumns"
          :pagination="false"
        />

        <a-typography-paragraph type="secondary" style="margin-top: 8px">
          项目级隔离（独立群聊、资产、评测与报告）随对应模块在后续阶段落地。
        </a-typography-paragraph>
      </a-tab-pane>

      <a-tab-pane key="audit" tab="审计日志">
        <a-input-search
          v-model:value="auditInput"
          allow-clear
          placeholder="按操作前缀筛选，如 agent. / auth. / task."
          style="width: 320px; margin-bottom: 12px"
          @search="onAuditSearch"
        />

        <a-table
          size="small"
          row-key="id"
          :data-source="audit.items"
          :columns="auditColumns"
          :scroll="{ x: 800 }"
          :pagination="auditPagination"
          @change="onAuditTableChange"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'created_at'">
              {{ fmtTime(record.created_at) }}
            </template>
            <template v-else-if="column.key === 'target'">
              {{ record.target_type ? `${record.target_type}#${record.target_id}` : '' }}
            </template>
            <template v-else-if="column.key === 'detail'">
              <a-typography-text :style="{ fontSize: '12px' }">
                {{ JSON.stringify(record.detail) }}
              </a-typography-text>
            </template>
          </template>
        </a-table>
      </a-tab-pane>
    </a-tabs>
  </a-card>
</template>
