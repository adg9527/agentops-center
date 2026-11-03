<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  CheckOutlined,
  CloseOutlined,
  CrownOutlined,
  DeleteOutlined,
  EditOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  RedoOutlined,
  RobotOutlined,
  SendOutlined,
  StopOutlined,
  SwapOutlined,
  TeamOutlined,
  UserOutlined,
} from '@ant-design/icons-vue'
import { api, type Agent } from '../api'
import { auth } from '../auth'
import { useLive, useLiveStatus } from '../live'
import MarkdownView from '../components/MarkdownView.vue'
import { fmtTime } from '../labels'
import StatusTag from '../components/StatusTag.vue'

// ------------------------------------------------------------------ labels

const ROLES: Record<string, { text: string; color: string }> = {
  analyst: { text: '分析', color: 'cyan' },
  executor: { text: '执行', color: 'blue' },
  reviewer: { text: '审查', color: 'purple' },
  summarizer: { text: '汇总', color: 'gold' },
}
const ROLE_OPTIONS = Object.entries(ROLES).map(([value, r]) => ({ value, label: r.text }))

const STEP_STATUS: Record<string, { text: string; color: string }> = {
  pending: { text: '待执行', color: 'default' },
  waiting_approval: { text: '等待审批', color: 'gold' },
  waiting_handoff: { text: '等待交接', color: 'purple' },
  running: { text: '执行中', color: 'processing' },
  completed: { text: '完成', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '取消', color: 'default' },
  skipped: { text: '跳过', color: 'default' },
}

const HANDOFF_STATUS: Record<string, string> = {
  proposed: '待接收',
  accepted: '已接收',
  completed: '已完成',
  rejected: '失败',
  cancelled: '取消',
}

const HANDOFF_INITIATOR: Record<string, string> = { platform: '平台调度', agent: 'Agent 主动', user: '人工' }
const STEP_ORIGIN: Record<string, string> = { user: '用户分工', plan: '主控计划', handoff: 'Agent 交接', intervention: '人工追加' }
const MODE_OPTIONS = [
  { value: 'manual', label: '人工指定' },
  { value: 'auto', label: '自动协作' },
  { value: 'chat', label: '仅聊天' },
]
const TERMINAL = ['completed', 'failed', 'cancelled']

// ------------------------------------------------------------------ types

interface Member {
  id: number
  agent_id: number
  name: string
  role: string
  enabled: boolean
  health: string
  model: string
  risk_warnings: string[]
}
interface Conv {
  id: number
  name: string
  coordinator_agent_id: number | null
  require_plan_approval: boolean
  members?: Member[]
  member_count?: number
  active_tasks?: number
  last_message?: { sender: string; content: string; at: string } | null
}
interface Msg {
  id: number
  sender_type: 'user' | 'agent' | 'system'
  sender_name: string
  sender_id: number | null
  kind: string
  content: string
  task_id: number | null
  step_id: number | null
  data: any
  created_at: string
}
interface Step {
  id: number
  step_key: string
  kind: string
  title: string
  instruction: string
  role: string
  agent_id: number
  agent_name: string
  depends_on: string[]
  status: string
  requires_approval: boolean
  origin: string
  output_text: string | null
  error: string | null
  subtask_id: number | null
}
interface HandoffT {
  id: number
  from_agent: string
  to_agent: string
  initiated_by: string
  reason: string
  context: string
  done_work: string
  remaining_work: string
  files: string[]
  result: string | null
  status: string
  created_at: string
  accepted_at: string | null
  completed_at: string | null
}
interface ApprovalT {
  id: number
  kind: string
  title: string
  detail: any
  status: string
  comment: string
  created_at: string
  decided_at: string | null
}
interface CollabTask {
  id: number
  conversation_id: number
  title: string
  prompt: string
  mode: string
  status: string
  error: string | null
  active: boolean
  paused: boolean
  steps: Step[]
  handoffs: HandoffT[]
  approvals: ApprovalT[]
  created_at: string
}

type BadgeStatus = 'success' | 'processing' | 'default' | 'error' | 'warning'

// ------------------------------------------------------------------ empty shells (keeps the template free of optional chaining)

const EMPTY_CONV: Conv = {
  id: 0,
  name: '',
  coordinator_agent_id: null,
  require_plan_approval: false,
  members: [],
  member_count: 0,
  active_tasks: 0,
  last_message: null,
}
const EMPTY_STEP: Step = {
  id: 0,
  step_key: '',
  kind: '',
  title: '',
  instruction: '',
  role: '',
  agent_id: 0,
  agent_name: '',
  depends_on: [],
  status: '',
  requires_approval: false,
  origin: '',
  output_text: null,
  error: null,
  subtask_id: null,
}
const EMPTY_HANDOFF: HandoffT = {
  id: 0,
  from_agent: '',
  to_agent: '',
  initiated_by: '',
  reason: '',
  context: '',
  done_work: '',
  remaining_work: '',
  files: [],
  result: null,
  status: '',
  created_at: '',
  accepted_at: null,
  completed_at: null,
}
const EMPTY_TASK: CollabTask = {
  id: 0,
  conversation_id: 0,
  title: '',
  prompt: '',
  mode: '',
  status: '',
  error: null,
  active: false,
  paused: false,
  steps: [],
  handoffs: [],
  approvals: [],
  created_at: '',
}

// ------------------------------------------------------------------ state

const canOperate = computed(() => auth.can('operator'))
const liveConnected = useLiveStatus()
// 流式增量：任务执行中先显示，落库后清掉
const streaming = ref<Record<number, { agent: string; text: string; thinking: string; at: number }>>({})

const convs = ref<Conv[]>([])
const current = ref<number | null>(null)
const agents = ref<Agent[]>([])
const creating = ref(false)
const creatingBusy = ref(false)
const newConv = reactive({
  name: '',
  agent_ids: [] as number[],
  coordinator_agent_id: undefined as number | undefined,
  require_plan_approval: false,
})
const renameTarget = ref<Conv | null>(null)
const renameText = ref('')

const convData = ref<Conv | null>(null)
const msgs = ref<Msg[]>([])
const tasks = ref<CollabTask[]>([])
const focus = ref<number | null>(null)
const rightTab = ref('task')
const streamRef = ref<HTMLElement | null>(null)
const roomError = ref(false)

const text = ref('')
const mode = ref('manual')
const assign = ref<Record<number, string | undefined>>({})
const sending = ref(false)
const expanded = ref<Record<number, boolean>>({})

const note = ref('')
const stepDetail = ref<Step | null>(null)
const handoffDetail = ref<HandoffT | null>(null)
const reassignTo = ref<number | undefined>(undefined)
const addStepOpen = ref(false)
const addStepBusy = ref(false)
const addStep = reactive({
  agent_id: undefined as number | undefined,
  role: 'executor',
  title: '',
  instruction: '',
  depends_on: [] as string[],
})

const inviteAgent = ref<number | undefined>(undefined)

// ------------------------------------------------------------------ computed

const conv = computed<Conv>(() => convData.value ?? EMPTY_CONV)
const members = computed<Member[]>(() => conv.value.members ?? [])
const focused = computed<CollabTask>(() => tasks.value.find((t) => t.id === focus.value) ?? tasks.value[0] ?? EMPTY_TASK)
const pendingApprovals = computed<ApprovalT[]>(() => focused.value.approvals.filter((a) => a.status === 'pending'))
const taskOptions = computed(() => tasks.value.map((t) => ({ value: t.id, label: `#${t.id} ${t.title}` })))
const memberOptions = computed(() => members.value.map((m) => ({ value: m.agent_id, label: m.name })))
const stepKeyOptions = computed(() => focused.value.steps.map((s) => ({ value: s.step_key, label: `${s.step_key} ${s.title}` })))
const agentOptions = computed(() =>
  agents.value.map((a) => ({ value: a.id, label: `${a.name}${a.enabled ? '' : '（已停用）'}`, disabled: !a.enabled })),
)
const coordinatorOptions = computed(() =>
  agents.value.filter((a) => newConv.agent_ids.includes(a.id)).map((a) => ({ value: a.id, label: a.name })),
)
const outsideOptions = computed(() =>
  agents.value
    .filter((a) => !members.value.some((m) => m.agent_id === a.id))
    .map((a) => ({ value: a.id, label: a.name, disabled: !a.enabled })),
)
const mentionOptions = computed(() => [
  { value: '全体', label: '全体成员' },
  ...members.value.map((m) => ({ value: m.name, label: `${m.name}（${roleOf(m.role)?.text ?? ''}）` })),
])
const assignments = computed(() =>
  Object.entries(assign.value)
    .filter(([, r]) => !!r)
    .map(([id, role]) => ({ agent_id: Number(id), role })),
)
const hint = computed(() => {
  if (mode.value === 'manual') return '@Agent 指定执行者，按成员角色（分析→执行→审查→汇总）依次执行；或用「分工」指定'
  if (mode.value === 'auto') {
    const coord = members.value.find((m) => m.agent_id === conv.value.coordinator_agent_id)
    return coord ? `由主控 ${coord.name} 自动拆分、分配并汇总` : '未设置主控：平台按成员角色自动分工（可在「成员」中设置主控）'
  }
  return '仅记录消息，不发起任务'
})
const stepView = computed<Step>(() => stepDetail.value ?? EMPTY_STEP)
const handoffView = computed<HandoffT>(() => handoffDetail.value ?? EMPTY_HANDOFF)
const stepOpen = computed({
  get: () => stepDetail.value !== null,
  set: (v: boolean) => {
    if (!v) stepDetail.value = null
  },
})
const handoffOpen = computed({
  get: () => handoffDetail.value !== null,
  set: (v: boolean) => {
    if (!v) handoffDetail.value = null
  },
})
const stepTitle = computed(() => (stepDetail.value ? `步骤 ${stepView.value.step_key} · ${stepView.value.title}` : '步骤详情'))

// ------------------------------------------------------------------ helpers

const roleOf = (r?: string) => (r ? ROLES[r] : undefined)
const stepStatus = (s: string) => STEP_STATUS[s] ?? { text: s, color: 'default' }
const handoffStatus = (s: string) => HANDOFF_STATUS[s] ?? s
const healthStatus = (m: Member): BadgeStatus => {
  if (!m.enabled) return 'default'
  if (m.health === 'online') return 'success'
  if (m.health === 'offline') return 'error'
  return 'default'
}
const isLive = (t: CollabTask) => t.active && !TERMINAL.includes(t.status)
const canRetry = (s: string) => ['failed', 'cancelled', 'skipped'].includes(s)
const canSkip = (s: string) => ['pending', 'waiting_approval'].includes(s)
const canReassign = (s: string) => ['pending', 'waiting_approval', 'failed', 'skipped', 'cancelled'].includes(s)

const isSystem = (m: Msg) => m.sender_type === 'system' || (m.kind === 'intervention' && m.sender_type !== 'user')
const isLong = (m: Msg) => m.content.length > 1200
const displayText = (m: Msg) => (isLong(m) && !expanded.value[m.id] ? `${m.content.slice(0, 1200)}…` : m.content)

interface Part {
  text: string
  mention: boolean
}
function bubbleParts(m: Msg): Part[] {
  return splitMentions(displayText(m))
}
function splitMentions(content: string): Part[] {
  if (!content) return []
  const out: Part[] = []
  const re = /@[^\s@,，。；;:：、)）\]】]+/g
  let last = 0
  let hit: RegExpExecArray | null
  while ((hit = re.exec(content)) !== null) {
    if (hit.index > last) out.push({ text: content.slice(last, hit.index), mention: false })
    out.push({ text: hit[0], mention: true })
    last = hit.index + hit[0].length
  }
  if (last < content.length) out.push({ text: content.slice(last), mention: false })
  return out
}
function toggleExpand(id: number) {
  expanded.value = { ...expanded.value, [id]: !expanded.value[id] }
}

// ------------------------------------------------------------------ loading

async function loadConvs() {
  try {
    const list = await api<Conv[]>('/api/conversations')
    convs.value = list
    if (current.value === null || !list.some((c) => c.id === current.value)) current.value = list[0]?.id ?? null
  } catch (e: any) {
    message.error(e.message ?? '加载群聊失败')
  }
}

async function loadAgents() {
  try {
    agents.value = await api<Agent[]>('/api/agents')
  } catch {
    /* 无权限时成员面板仍可用 */
  }
}

let roomSeq = 0
async function loadRoom(cid: number) {
  const seq = ++roomSeq
  roomError.value = false
  try {
    const [c, t, m] = await Promise.all([
      api<Conv>(`/api/conversations/${cid}`),
      api<CollabTask[]>(`/api/conversations/${cid}/tasks`),
      api<Msg[]>(`/api/conversations/${cid}/messages`),
    ])
    if (seq !== roomSeq) return
    convData.value = c
    tasks.value = t
    msgs.value = m
    void scrollToBottom()
  } catch (e: any) {
    if (seq !== roomSeq) return
    roomError.value = true
    message.error(e.message ?? '加载群聊失败')
  }
}

async function loadConv(cid: number) {
  try {
    convData.value = await api<Conv>(`/api/conversations/${cid}`)
  } catch {
    /* 已归档或网络抖动时忽略，轮询会重试 */
  }
}

async function loadTasks(cid: number) {
  try {
    tasks.value = await api<CollabTask[]>(`/api/conversations/${cid}/tasks`)
  } catch {
    /* ignore */
  }
}

async function scrollToBottom() {
  await nextTick()
  const el = streamRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function resetRoom() {
  convData.value = null
  msgs.value = []
  tasks.value = []
  focus.value = null
  rightTab.value = 'task'
  text.value = ''
  mode.value = 'manual'
  assign.value = {}
  note.value = ''
  stepDetail.value = null
  handoffDetail.value = null
  addStepOpen.value = false
  addStep.agent_id = undefined
  addStep.role = 'executor'
  addStep.title = ''
  addStep.instruction = ''
  addStep.depends_on = []
  inviteAgent.value = undefined
  expanded.value = {}
}

// ------------------------------------------------------------------ realtime + polling fallback

window.setInterval(() => {
  const now = Date.now()
  const stale = Object.entries(streaming.value).filter(([, s]) => now - (s.at || now) > 120_000)
  if (stale.length) {
    const next = { ...streaming.value }
    for (const [key] of stale) delete next[Number(key)]
    streaming.value = next
  }
}, 15_000)

useLive((m) => {
  if (['message_created', 'conversation_updated', 'collab_updated'].includes(m.topic)) void loadConvs()
  const cid = current.value
  if (cid == null) return
  if (m.topic === 'message_created' && m.data?.conversation_id === cid) {
    if (!msgs.value.some((x) => x.id === m.data.id)) {
      msgs.value.push(m.data as Msg)
      void scrollToBottom()
    }
    const subId = m.data.sub_task_id ?? m.data.task_id
    if (subId != null && streaming.value[subId]) {
      const next = { ...streaming.value }
      delete next[subId]
      streaming.value = next
    }
  } else if (m.topic === 'agent_delta' && m.data?.conversation_id === cid) {
    const key = Number(m.data.task_id)
    const prev = streaming.value[key] ?? { agent: m.data.agent_name ?? 'Agent', text: '', thinking: '', at: 0 }
    const isThinking = !!m.data.thinking
    const chunk = m.data.text ?? ''
    streaming.value = {
      ...streaming.value,
      [key]: {
        agent: prev.agent,
        text: isThinking ? prev.text : prev.text + chunk,
        thinking: isThinking ? prev.thinking + chunk : '',
        at: Date.now(),
      },
    }
    void scrollToBottom()
  } else if (m.topic === 'collab_updated' && m.data?.conversation_id === cid) {
    void loadTasks(cid)
  } else if (m.topic === 'conversation_updated' && m.data?.id === cid) {
    void loadConv(cid)
  }
})

async function poll() {
  if (document.hidden) return
  const cid = current.value
  if (cid == null) {
    if (!liveConnected.value) await loadConvs()
    return
  }
  if (!liveConnected.value) {
    // 兜底：WebSocket 断开时全量刷新
    await loadConvs()
    await loadRoom(cid)
    return
  }
  if (tasks.value.some((t) => isLive(t))) await loadTasks(cid)
}

let pollTimer: number | undefined

watch(current, (cid) => {
  resetRoom()
  if (cid != null) void loadRoom(cid)
})

watch(
  () => msgs.value.length,
  () => {
    void scrollToBottom()
  },
)

onMounted(() => {
  void loadConvs()
  void loadAgents()
  pollTimer = window.setInterval(() => {
    void poll()
  }, 5000)
})

onUnmounted(() => {
  window.clearInterval(pollTimer)
})

// ------------------------------------------------------------------ conversations

function openNewConv() {
  newConv.name = ''
  newConv.agent_ids = []
  newConv.coordinator_agent_id = undefined
  newConv.require_plan_approval = false
  creating.value = true
  void loadAgents()
}

async function createConv() {
  if (!newConv.name.trim()) {
    message.error('请填写群聊名称')
    return
  }
  if (!newConv.agent_ids.length) {
    message.error('请至少选择一个成员 Agent')
    return
  }
  creatingBusy.value = true
  try {
    const coord = newConv.coordinator_agent_id
    const c = await api<Conv>('/api/conversations', {
      body: {
        name: newConv.name.trim(),
        agent_ids: newConv.agent_ids,
        coordinator_agent_id: coord != null && newConv.agent_ids.includes(coord) ? coord : null,
        require_plan_approval: newConv.require_plan_approval,
      },
    })
    creating.value = false
    await loadConvs()
    current.value = c.id
  } catch (e: any) {
    message.error(e.message ?? '创建失败')
  } finally {
    creatingBusy.value = false
  }
}

function openRename(c: Conv) {
  renameTarget.value = c
  renameText.value = c.name
}

async function submitRename() {
  const target = renameTarget.value
  if (!target) return
  const name = renameText.value.trim()
  if (!name) {
    message.error('名称不能为空')
    return
  }
  try {
    await api<Conv>(`/api/conversations/${target.id}`, { method: 'PATCH', body: { name } })
    renameTarget.value = null
    await loadConvs()
    if (current.value === target.id) await loadConv(target.id)
  } catch (e: any) {
    message.error(e.message ?? '重命名失败')
  }
}

async function archiveConv(cid: number) {
  try {
    await api(`/api/conversations/${cid}`, { method: 'DELETE' })
    if (current.value === cid) current.value = null
    await loadConvs()
    message.success('已归档')
  } catch (e: any) {
    message.error(e.message ?? '归档失败')
  }
}

// ------------------------------------------------------------------ composer

function setAssign(agentId: number, role?: string) {
  assign.value = { ...assign.value, [agentId]: role }
}

function onComposerKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault()
    void send()
  }
}

async function send() {
  const content = text.value
  if (!content.trim() || sending.value) return
  sending.value = true
  try {
    const cid = conv.value.id
    const r = await api<{ task: { id: number } | null }>(`/api/conversations/${cid}/messages`, {
      body: {
        content,
        mode: mode.value,
        mention_all: /@(全体|all)\b/.test(content) || content.includes('@全体'),
        assignments: mode.value === 'chat' ? [] : assignments.value,
      },
    })
    text.value = ''
    assign.value = {}
    if (r?.task?.id) focus.value = r.task.id
    await loadTasks(cid)
    await loadConvs()
    await scrollToBottom()
  } catch (e: any) {
    message.error(e.message ?? '发送失败')
  } finally {
    sending.value = false
  }
}

function focusTask(id: number) {
  if (!id) return
  rightTab.value = 'task'
  focus.value = id
}

// ------------------------------------------------------------------ members

function applyConv(c: Conv) {
  convData.value = c
  message.success('已更新')
}

async function patchConv(body: Record<string, unknown>) {
  try {
    applyConv(await api<Conv>(`/api/conversations/${conv.value.id}`, { method: 'PATCH', body }))
  } catch (e: any) {
    message.error(e.message ?? '更新失败')
  }
}

async function patchMember(path: string, method: string, body?: unknown) {
  try {
    applyConv(await api<Conv>(path, { method, body }))
  } catch (e: any) {
    message.error(e.message ?? '更新失败')
  }
}

function setMemberRole(agentId: number, role?: string) {
  if (!role) return
  void patchMember(`/api/conversations/${conv.value.id}/members/${agentId}`, 'PATCH', { role })
}

function toggleCoordinator(agentId: number) {
  const isCoord = conv.value.coordinator_agent_id === agentId
  void patchConv(isCoord ? { clear_coordinator: true } : { coordinator_agent_id: agentId })
}

function removeMember(agentId: number) {
  void patchMember(`/api/conversations/${conv.value.id}/members/${agentId}`, 'DELETE')
}

function invite() {
  const agentId = inviteAgent.value
  if (agentId == null) return
  inviteAgent.value = undefined
  void patchMember(`/api/conversations/${conv.value.id}/members`, 'POST', { agent_id: agentId, role: 'executor' })
}

function onRequirePlanApproval(e: any) {
  void patchConv({ require_plan_approval: !!e?.target?.checked })
}

// ------------------------------------------------------------------ collab control

async function taskCall(path: string, body?: unknown) {
  try {
    await api(path, { body: body ?? {} })
    const cid = current.value
    if (cid != null) await loadTasks(cid)
  } catch (e: any) {
    message.error(e.message ?? '操作失败')
  }
}

async function control(action: 'pause' | 'resume' | 'cancel') {
  const t = focused.value
  if (!t.id) return
  await taskCall(`/api/collab/tasks/${t.id}/${action}`)
}

async function decide(id: number, approve: boolean) {
  if (!id) return
  await taskCall(`/api/approvals/${id}/${approve ? 'approve' : 'reject'}`, approve ? {} : { comment: '' })
}

async function intervene() {
  const t = focused.value
  const value = note.value.trim()
  if (!t.id || !value) return
  await taskCall(`/api/collab/tasks/${t.id}/intervene`, { note: value })
  note.value = ''
}

function openStep(s: Step) {
  reassignTo.value = undefined
  stepDetail.value = s
}

function openHandoff(h: HandoffT) {
  handoffDetail.value = h
}

async function stepAction(kind: 'retry' | 'skip') {
  const s = stepView.value
  if (!s.id) return
  await taskCall(`/api/collab/steps/${s.id}/${kind}`)
  message.success('已提交')
  stepDetail.value = null
}

async function reassign() {
  const s = stepView.value
  if (!s.id || reassignTo.value == null) return
  await taskCall(`/api/collab/steps/${s.id}/reassign`, { agent_id: reassignTo.value })
  message.success('已提交')
  stepDetail.value = null
}

async function submitAddStep() {
  const t = focused.value
  if (!t.id) return
  if (addStep.agent_id == null) {
    message.error('请选择执行 Agent')
    return
  }
  if (!addStep.title.trim()) {
    message.error('请填写标题')
    return
  }
  if (!addStep.instruction.trim()) {
    message.error('请填写指令')
    return
  }
  addStepBusy.value = true
  try {
    await api(`/api/collab/tasks/${t.id}/steps`, {
      body: {
        agent_id: addStep.agent_id,
        role: addStep.role,
        title: addStep.title.trim(),
        instruction: addStep.instruction,
        depends_on: addStep.depends_on,
      },
    })
    addStepOpen.value = false
    addStep.agent_id = undefined
    addStep.role = 'executor'
    addStep.title = ''
    addStep.instruction = ''
    addStep.depends_on = []
    const cid = current.value
    if (cid != null) await loadTasks(cid)
  } catch (e: any) {
    message.error(e.message ?? '追加失败')
  } finally {
    addStepBusy.value = false
  }
}
</script>

<template>
  <div class="chat">
    <!-- 左：会话列表 -->
    <a-card size="small" class="col conv-card" :body-style="{ padding: 0, overflow: 'auto', flex: 1, minHeight: '0' }">
      <template #title>
        <TeamOutlined />
        群聊
      </template>
      <template #extra>
        <a-button size="small" type="primary" @click="openNewConv">
          <template #icon><PlusOutlined /></template>
        </a-button>
      </template>

      <div v-if="!convs.length" class="conv-empty">
        <a-empty description="还没有群聊" />
      </div>
      <div
        v-for="c in convs"
        :key="c.id"
        class="conv-item"
        :class="{ active: c.id === current }"
        @click="current = c.id"
      >
        <div class="conv-row">
          <span class="conv-name">{{ c.name }}</span>
          <a-badge v-if="c.active_tasks" :count="c.active_tasks ?? 0" size="small" color="#4f7cff" />
          <span class="conv-actions">
            <a-tooltip title="重命名">
              <a-button type="text" size="small" @click.stop="openRename(c)">
                <template #icon><EditOutlined /></template>
              </a-button>
            </a-tooltip>
            <a-popconfirm title="归档该群聊？" @confirm="archiveConv(c.id)">
              <a-button type="text" size="small" danger @click.stop>
                <template #icon><DeleteOutlined /></template>
              </a-button>
            </a-popconfirm>
          </span>
        </div>
        <div class="conv-desc">
          {{ c.last_message ? `${c.last_message.sender}: ${c.last_message.content}` : `${c.member_count ?? 0} 个 Agent` }}
        </div>
      </div>
    </a-card>

    <!-- 中 + 右 -->
    <template v-if="current != null">
      <template v-if="convData">
        <!-- 中：消息流 -->
        <a-card
          size="small"
          class="col room"
          :body-style="{ flex: 1, display: 'flex', flexDirection: 'column', padding: 0, minHeight: 0 }"
        >
          <template #title>
            <a-space>
              {{ conv.name }}
              <a-typography-text type="secondary" :style="{ fontSize: '12px' }">{{ members.length }} 个 Agent</a-typography-text>
            </a-space>
          </template>
          <template #extra>
            <a-popconfirm v-if="canOperate" title="归档该群聊？" @confirm="archiveConv(conv.id)">
              <a-button size="small" type="text">归档</a-button>
            </a-popconfirm>
          </template>

          <div ref="streamRef" class="stream">
            <template v-for="m in msgs" :key="m.id">
              <div v-if="isSystem(m)" class="sys-row">
                <span class="sys-bubble" :class="{ approval: m.kind === 'approval' }">
                  {{ m.content
                  }}<a v-if="m.task_id" class="task-link" @click="focusTask(m.task_id ?? 0)">#{{ m.task_id }}</a>
                </span>
              </div>
              <div v-else class="msg-row" :class="{ mine: m.sender_type === 'user' }">
                <a-avatar :size="32" :style="{ background: m.sender_type === 'user' ? '#4f7cff' : '#8c9bb5', flexShrink: 0 }">
                  <template #icon>
                    <UserOutlined v-if="m.sender_type === 'user'" />
                    <RobotOutlined v-else />
                  </template>
                </a-avatar>
                <div class="msg-body">
                  <div class="msg-meta" :class="{ mine: m.sender_type === 'user' }">
                    <b class="msg-sender">{{ m.sender_name }}</b>
                    <a-tag
                      v-if="roleOf(m.data?.role)"
                      :color="roleOf(m.data?.role)?.color"
                      class="tiny-tag"
                    >
                      {{ roleOf(m.data?.role)?.text }}
                    </a-tag>
                    <a-tag v-if="m.kind === 'plan'" color="gold" class="tiny-tag">计划</a-tag>
                    <a-tag v-if="m.data?.brief" color="geekblue" class="tiny-tag">进度</a-tag>
                    <a-tag v-if="m.kind === 'intervention'" color="orange" class="tiny-tag">人工介入</a-tag>
                    <span class="msg-time">{{ fmtTime(m.created_at) }}</span>
                    <a v-if="m.task_id" class="task-link" @click="focusTask(m.task_id ?? 0)">#{{ m.task_id }}</a>
                  </div>
                  <div class="msg-text">
                    <MarkdownView v-if="m.sender_type === 'agent'" :content="displayText(m)" />
                    <template v-else>
                      <template v-for="(p, i) in bubbleParts(m)" :key="i">
                        <span v-if="p.mention" class="mention">{{ p.text }}</span>
                        <span v-else>{{ p.text }}</span>
                      </template>
                    </template>
                    <div v-if="isLong(m)">
                      <a class="task-link" @click="toggleExpand(m.id)">{{ expanded[m.id] ? '收起' : '展开全文' }}</a>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <div v-for="(s, tid) in streaming" :key="'stream-' + tid" class="msg-row">
              <a-avatar :size="32" :style="{ background: '#8c9bb5', flexShrink: 0 }">
                <template #icon><RobotOutlined /></template>
              </a-avatar>
              <div class="msg-body">
                <div class="msg-meta">
                  <b class="msg-sender">{{ s.agent }}</b>
                  <a-tag color="processing" class="tiny-tag">{{ s.text ? '生成中' : '思考中' }}</a-tag>
                  <a-typography-text type="secondary" style="font-size: 11px">#{{ tid }}</a-typography-text>
                </div>
                <div class="msg-text" :style="s.text ? {} : { opacity: 0.65, fontStyle: 'italic' }">
                  <MarkdownView :content="s.text || s.thinking" />
                </div>
              </div>
            </div>
            <div v-if="!msgs.length" class="stream-empty">
              <a-empty description="还没有消息，发一条消息开始协作" />
            </div>
          </div>

          <div v-if="canOperate" class="composer">
            <div class="composer-bar">
              <a-segmented v-model:value="mode" size="small" :options="MODE_OPTIONS" />
              <a-popover v-if="mode !== 'chat'" trigger="click" title="本次分工（覆盖成员默认角色）">
                <template #content>
                  <table class="assign-table">
                    <thead>
                      <tr>
                        <th>Agent</th>
                        <th>角色</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="m in members" :key="m.agent_id">
                        <td>{{ m.name }}</td>
                        <td>
                          <a-select
                            size="small"
                            allow-clear
                            placeholder="不参与"
                            :style="{ width: '110px' }"
                            :value="assign[m.agent_id]"
                            :options="ROLE_OPTIONS"
                            @change="setAssign(m.agent_id, $event)"
                          />
                        </td>
                      </tr>
                      <tr v-if="!members.length">
                        <td colspan="2" class="muted">还没有成员</td>
                      </tr>
                    </tbody>
                  </table>
                </template>
                <a-button size="small">分工{{ assignments.length ? `（${assignments.length}）` : '' }}</a-button>
              </a-popover>
              <a-typography-text type="secondary" :style="{ fontSize: '12px' }">{{ hint }}</a-typography-text>
            </div>
            <div class="composer-row" @keydown="onComposerKeydown">
              <a-mentions
                v-model:value="text"
                :rows="3"
                :options="mentionOptions"
                placeholder="输入任务，@ 选择 Agent，Ctrl+Enter 发送"
                style="flex: 1"
              />
              <a-button type="primary" :loading="sending" class="send-btn" @click="send">
                <template #icon><SendOutlined /></template>
                发送
              </a-button>
            </div>
          </div>
          <a-alert v-else type="info" banner message="只读账号不能发布任务" />
        </a-card>

        <!-- 右：任务 / 成员 -->
        <a-card size="small" class="col side" :body-style="{ overflow: 'auto', flex: 1, paddingTop: 0, minHeight: '0' }">
          <a-tabs v-model:activeKey="rightTab" size="small">
            <a-tab-pane key="task" :tab="`协作任务 (${tasks.length})`">
              <template v-if="tasks.length">
                <a-select
                  size="small"
                  :style="{ width: '100%', marginBottom: '8px' }"
                  :value="focused.id"
                  :options="taskOptions"
                  @change="focus = $event"
                />
                <div class="collab">
                  <a-space wrap size="small">
                    <StatusTag :status="focused.status" />
                    <a-tag>{{ focused.mode === 'auto' ? '自动协作' : '人工指定' }}</a-tag>
                    <a-tag v-if="focused.paused" color="warning">已请求暂停</a-tag>
                    <router-link :to="`/tasks/${focused.id}`">执行时间线</router-link>
                  </a-space>

                  <a-alert v-if="focused.error" type="error" show-icon :message="focused.error" />

                  <a-space v-if="canOperate && isLive(focused)" wrap size="small">
                    <a-button v-if="focused.paused" size="small" @click="control('resume')">
                      <template #icon><PlayCircleOutlined /></template>
                      恢复
                    </a-button>
                    <a-tooltip v-else title="当前步骤完成后生效；外部 Agent 进程无法冻结">
                      <a-button size="small" @click="control('pause')">
                        <template #icon><PauseOutlined /></template>
                        暂停
                      </a-button>
                    </a-tooltip>
                    <a-popconfirm title="终止任务并结束所有 Agent 进程？" @confirm="control('cancel')">
                      <a-button size="small" danger>
                        <template #icon><StopOutlined /></template>
                        终止
                      </a-button>
                    </a-popconfirm>
                  </a-space>

                  <!-- 待审批 -->
                  <div v-for="a in pendingApprovals" :key="a.id" class="approval">
                    <a-alert type="warning" show-icon :message="a.title">
                      <template #description>
                        <ol v-if="a.kind === 'plan'" class="plan-list">
                          <li v-for="s in a.detail?.steps ?? []" :key="s.key">
                            [{{ s.key }}] {{ s.title }}{{ s.depends_on?.length ? ` ← ${s.depends_on.join(',')}` : '' }}
                          </li>
                        </ol>
                        <template v-else>
                          风险：{{ (a.detail?.risk_warnings ?? []).join('；') }}<br />
                          任务：{{ a.detail?.instruction }}
                        </template>
                      </template>
                    </a-alert>
                    <div v-if="canOperate" class="approval-actions">
                      <a-button size="small" type="primary" @click="decide(a.id, true)">
                        <template #icon><CheckOutlined /></template>
                        批准
                      </a-button>
                      <a-button size="small" danger @click="decide(a.id, false)">
                        <template #icon><CloseOutlined /></template>
                        拒绝
                      </a-button>
                    </div>
                  </div>

                  <div class="list-head">
                    <b>步骤</b>
                    <span class="muted">{{ focused.steps.length }}</span>
                  </div>
                  <div class="step-list">
                    <div v-for="s in focused.steps" :key="s.id" class="step-item" @click="openStep(s)">
                      <div class="step-line">
                        <a-tag :color="stepStatus(s.status).color">{{ stepStatus(s.status).text }}</a-tag>
                        <code class="step-key">{{ s.step_key }}</code>
                        <b>{{ s.agent_name }}</b>
                        <a-tag :color="roleOf(s.role)?.color" class="tiny-tag">
                          {{ s.kind === 'plan' ? '规划' : s.kind === 'summary' ? '汇总' : roleOf(s.role)?.text }}
                        </a-tag>
                        <a-tag v-if="s.origin === 'handoff'" color="purple" class="tiny-tag">交接</a-tag>
                        <a-tag v-if="s.origin === 'intervention'" color="orange" class="tiny-tag">追加</a-tag>
                      </div>
                      <div class="step-sub">{{ s.title }}{{ s.depends_on.length ? ` ← ${s.depends_on.join(', ')}` : '' }}</div>
                    </div>
                    <div v-if="!focused.steps.length" class="muted">暂无步骤</div>
                  </div>

                  <div class="list-head">
                    <b>任务交接（{{ focused.handoffs.length }}）</b>
                  </div>
                  <div v-if="focused.handoffs.length" class="step-list">
                    <div v-for="h in focused.handoffs" :key="h.id" class="step-item" @click="openHandoff(h)">
                      <div class="step-line">
                        <SwapOutlined />
                        <span>{{ h.from_agent }} → {{ h.to_agent }}</span>
                        <a-tag class="tiny-tag">{{ handoffStatus(h.status) }}</a-tag>
                        <a-tag v-if="h.initiated_by === 'agent'" color="purple" class="tiny-tag">Agent 发起</a-tag>
                        <a-tag v-if="h.initiated_by === 'user'" color="orange" class="tiny-tag">人工</a-tag>
                      </div>
                      <div class="step-sub">{{ h.reason }}</div>
                    </div>
                  </div>
                  <div v-else class="muted">暂无</div>

                  <template v-if="canOperate">
                    <div class="list-head"><b>人工介入</b></div>
                    <a-textarea v-model:value="note" :rows="2" placeholder="补充说明 / 修改要求，将注入后续所有步骤" />
                    <a-space wrap size="small">
                      <a-button size="small" :disabled="!note.trim()" @click="intervene">提交补充说明</a-button>
                      <a-button size="small" @click="addStepOpen = true">
                        <template #icon><PlusOutlined /></template>
                        追加步骤
                      </a-button>
                    </a-space>
                  </template>
                </div>
              </template>
              <a-empty v-else description="发送消息并 @Agent 即可创建协作任务" />
            </a-tab-pane>

            <a-tab-pane key="members" tab="成员">
              <div class="member-list">
                <div v-for="m in members" :key="m.agent_id" class="member">
                  <div class="member-main">
                    <a-badge :status="healthStatus(m)" />
                    <b>{{ m.name }}</b>
                    <a-tooltip
                      v-if="m.risk_warnings.length"
                      :title="`高风险配置，执行前需管理员审批：${m.risk_warnings.join('；')}`"
                    >
                      <a-tag color="warning" class="tiny-tag">需审批</a-tag>
                    </a-tooltip>
                    <span class="muted member-role">{{ roleOf(m.role)?.text }}</span>
                  </div>
                  <div v-if="canOperate" class="member-ops">
                    <a-select
                      size="small"
                      :bordered="false"
                      :style="{ width: '86px' }"
                      :value="m.role"
                      :options="ROLE_OPTIONS"
                      @change="setMemberRole(m.agent_id, $event)"
                    />
                    <a-tooltip :title="conv.coordinator_agent_id === m.agent_id ? '取消主控' : '设为主控'">
                      <a-button
                        size="small"
                        :type="conv.coordinator_agent_id === m.agent_id ? 'primary' : 'text'"
                        @click="toggleCoordinator(m.agent_id)"
                      >
                        <template #icon><CrownOutlined /></template>
                      </a-button>
                    </a-tooltip>
                    <a-popconfirm :title="`将 ${m.name} 移出群聊？`" @confirm="removeMember(m.agent_id)">
                      <a-button size="small" type="text" danger>
                        <template #icon><CloseOutlined /></template>
                      </a-button>
                    </a-popconfirm>
                  </div>
                </div>
                <div v-if="!members.length" class="muted">还没有成员</div>
              </div>

              <div v-if="canOperate" class="invite">
                <a-select
                  placeholder="邀请 Agent 加入"
                  :style="{ flex: 1 }"
                  :value="inviteAgent"
                  :options="outsideOptions"
                  @change="inviteAgent = $event"
                />
                <a-button :disabled="inviteAgent == null" @click="invite">添加</a-button>
              </div>

              <a-checkbox v-if="canOperate" :checked="conv.require_plan_approval" @change="onRequirePlanApproval">
                自动拆分的计划需人工审批
              </a-checkbox>
            </a-tab-pane>
          </a-tabs>
        </a-card>
      </template>
      <a-card v-else-if="roomError" class="col room">
        <a-empty description="群聊加载失败，请重新选择" />
      </a-card>
      <a-card v-else :loading="true" class="col room" />
    </template>
    <a-card v-else class="col room">
      <a-empty description="创建一个群聊，邀请已接入的 Agent 协作" />
    </a-card>

    <!-- 新建群聊 -->
    <a-modal
      v-model:open="creating"
      title="新建群聊"
      :confirm-loading="creatingBusy"
      ok-text="创建"
      cancel-text="取消"
      @ok="createConv"
    >
      <a-form layout="vertical">
        <a-form-item label="群聊名称">
          <a-input v-model:value="newConv.name" placeholder="例如：需求评审小组" />
        </a-form-item>
        <a-form-item label="成员 Agent">
          <a-select v-model:value="newConv.agent_ids" mode="multiple" :options="agentOptions" placeholder="选择成员" />
        </a-form-item>
        <a-form-item label="主控 Agent（自动协作时负责拆分与汇总）">
          <a-select
            v-model:value="newConv.coordinator_agent_id"
            allow-clear
            :options="coordinatorOptions"
            placeholder="可不设置"
          />
        </a-form-item>
        <a-form-item label="自动拆分的计划需人工审批后执行">
          <a-switch v-model:checked="newConv.require_plan_approval" />
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 重命名 -->
    <a-modal
      :open="renameTarget !== null"
      title="重命名群聊"
      ok-text="保存"
      cancel-text="取消"
      @ok="submitRename"
      @cancel="renameTarget = null"
    >
      <a-input v-model:value="renameText" placeholder="群聊名称" @press-enter="submitRename" />
    </a-modal>

    <!-- 交接详情 -->
    <a-drawer v-model:open="handoffOpen" :width="560" title="任务交接详情">
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="交接 ID">{{ handoffView.id }}</a-descriptions-item>
        <a-descriptions-item label="交出方 → 接收方">{{ handoffView.from_agent }} → {{ handoffView.to_agent }}</a-descriptions-item>
        <a-descriptions-item label="发起方">{{ HANDOFF_INITIATOR[handoffView.initiated_by] ?? handoffView.initiated_by }}</a-descriptions-item>
        <a-descriptions-item label="接收状态">{{ handoffStatus(handoffView.status) }}</a-descriptions-item>
        <a-descriptions-item label="交接原因">{{ handoffView.reason }}</a-descriptions-item>
        <a-descriptions-item label="已完成工作">
          <pre class="pre">{{ handoffView.done_work }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="尚未完成工作">
          <pre class="pre">{{ handoffView.remaining_work }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="相关文件">
          <template v-if="handoffView.files.length">
            <a-tag v-for="f in handoffView.files" :key="f">{{ f }}</a-tag>
          </template>
          <template v-else>无</template>
        </a-descriptions-item>
        <a-descriptions-item label="执行结果">
          <pre class="pre">{{ handoffView.result ?? '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="任务上下文">
          <pre class="pre">{{ handoffView.context || '—' }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="交接 / 接收 / 完成时间">
          {{ fmtTime(handoffView.created_at) }} / {{ fmtTime(handoffView.accepted_at) }} / {{ fmtTime(handoffView.completed_at) }}
        </a-descriptions-item>
      </a-descriptions>
    </a-drawer>

    <!-- 步骤详情 -->
    <a-drawer v-model:open="stepOpen" :width="600" :title="stepTitle">
      <template #extra>
        <a-space v-if="canOperate" wrap>
          <a-button v-if="canRetry(stepView.status)" size="small" @click="stepAction('retry')">
            <template #icon><RedoOutlined /></template>
            重试
          </a-button>
          <a-button v-if="canSkip(stepView.status)" size="small" @click="stepAction('skip')">跳过</a-button>
          <a-space-compact v-if="canReassign(stepView.status)">
            <a-select
              size="small"
              placeholder="改派给"
              :style="{ width: '120px' }"
              :value="reassignTo"
              :options="memberOptions.filter((o) => o.value !== stepView.agent_id)"
              @change="reassignTo = $event"
            />
            <a-button size="small" :disabled="reassignTo == null" @click="reassign">改派</a-button>
          </a-space-compact>
        </a-space>
      </template>
      <a-descriptions :column="1" size="small" bordered>
        <a-descriptions-item label="执行 Agent">{{ stepView.agent_name }}（{{ roleOf(stepView.role)?.text ?? stepView.role }}）</a-descriptions-item>
        <a-descriptions-item label="状态">{{ stepStatus(stepView.status).text }}</a-descriptions-item>
        <a-descriptions-item label="依赖">{{ stepView.depends_on.join(', ') || '无' }}</a-descriptions-item>
        <a-descriptions-item label="来源">{{ STEP_ORIGIN[stepView.origin] ?? stepView.origin }}</a-descriptions-item>
        <a-descriptions-item label="子任务">
          <router-link v-if="stepView.subtask_id" :to="`/tasks/${stepView.subtask_id}`">
            #{{ stepView.subtask_id }}（含完整执行事件）
          </router-link>
          <template v-else>未开始</template>
        </a-descriptions-item>
        <a-descriptions-item label="指令">
          <pre class="pre">{{ stepView.instruction }}</pre>
        </a-descriptions-item>
        <a-descriptions-item v-if="stepView.error" label="错误">
          <pre class="pre">{{ stepView.error }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="输出">
          <pre class="pre">{{ stepView.output_text ?? '—' }}</pre>
        </a-descriptions-item>
      </a-descriptions>
    </a-drawer>

    <!-- 追加步骤 -->
    <a-modal
      v-model:open="addStepOpen"
      title="追加步骤"
      :confirm-loading="addStepBusy"
      ok-text="追加"
      cancel-text="取消"
      @ok="submitAddStep"
    >
      <a-form layout="vertical">
        <a-form-item label="执行 Agent">
          <a-select v-model:value="addStep.agent_id" :options="memberOptions" placeholder="选择 Agent" />
        </a-form-item>
        <a-form-item label="角色">
          <a-select v-model:value="addStep.role" :options="ROLE_OPTIONS" />
        </a-form-item>
        <a-form-item label="标题">
          <a-input v-model:value="addStep.title" placeholder="这一步要做什么" />
        </a-form-item>
        <a-form-item label="指令">
          <a-textarea v-model:value="addStep.instruction" :rows="4" placeholder="给 Agent 的完整指令" />
        </a-form-item>
        <a-form-item label="依赖步骤（完成后才执行，并接收其交接）">
          <a-select v-model:value="addStep.depends_on" mode="multiple" :options="stepKeyOptions" placeholder="可不选" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
.chat {
  display: flex;
  gap: 12px;
  height: calc(100vh - 96px);
  min-height: 520px;
}

.col {
  display: flex;
  flex-direction: column;
}

.conv-card {
  width: 240px;
  flex-shrink: 0;
}

.room {
  flex: 1;
  min-width: 0;
}

.side {
  width: 400px;
  flex-shrink: 0;
}

.conv-empty {
  padding: 16px 8px;
}

.conv-item {
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid #f2f4f8;
}

.conv-item:hover {
  background: #f5f7fb;
}

.conv-item.active {
  background: #e8efff;
}

.conv-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.conv-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-actions {
  margin-left: auto;
  display: none;
  align-items: center;
}

.conv-item:hover .conv-actions,
.conv-item.active .conv-actions {
  display: inline-flex;
}

.conv-desc {
  margin-top: 2px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stream {
  flex: 1;
  overflow: auto;
  padding: 12px 16px;
  min-height: 0;
}

.stream-empty {
  padding-top: 40px;
}

.sys-row {
  text-align: center;
  margin: 8px 0;
}

.sys-bubble {
  display: inline-block;
  text-align: left;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.55);
  background: #f2f4f8;
  padding: 4px 10px;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-word;
  max-width: 92%;
}

.sys-bubble.approval {
  background: #fff7e6;
}

.msg-row {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}

.msg-row.mine {
  flex-direction: row-reverse;
}

.msg-body {
  max-width: 78%;
  min-width: 0;
}

.msg-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  flex-wrap: wrap;
}

.msg-meta.mine {
  justify-content: flex-end;
}

.msg-sender {
  font-size: 12px;
}

.msg-time {
  font-size: 11px;
  color: rgba(0, 0, 0, 0.45);
}

.tiny-tag {
  font-size: 10px;
  line-height: 16px;
  padding: 0 4px;
  margin-inline-end: 0;
}

.msg-text {
  background: #f7f9fc;
  border: 1px solid #e9edf5;
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 2px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 13px;
}

.msg-row.mine .msg-text {
  background: #e8f0ff;
  border-color: #d3e0ff;
}

.mention {
  color: #2f5fd0;
  font-weight: 600;
  background: rgba(79, 124, 255, 0.12);
  border-radius: 3px;
  padding: 0 2px;
}

.task-link {
  margin-left: 6px;
  font-size: 12px;
}

.composer {
  border-top: 1px solid #eef1f6;
  padding: 12px;
}

.composer-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.composer-row {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.send-btn {
  height: auto;
}

.assign-table {
  width: 300px;
  border-collapse: collapse;
}

.assign-table th,
.assign-table td {
  text-align: left;
  padding: 4px 6px;
  font-size: 12px;
  border-bottom: 1px solid #f0f0f0;
}

.collab {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.approval {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.approval-actions {
  display: flex;
  gap: 8px;
}

.plan-list {
  padding-left: 18px;
  margin: 4px 0;
}

.list-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.step-list {
  border: 1px solid #f0f2f7;
  border-radius: 6px;
  overflow: hidden;
}

.step-item {
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid #f7f8fb;
}

.step-item:last-child {
  border-bottom: none;
}

.step-item:hover {
  background: #f7f9fd;
}

.step-line {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  font-size: 12px;
}

.step-key {
  font-size: 11px;
  background: #f5f5f5;
  padding: 0 4px;
  border-radius: 3px;
}

.step-sub {
  margin-top: 2px;
  font-size: 12px;
  color: rgba(0, 0, 0, 0.5);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.member-list {
  border: 1px solid #f0f2f7;
  border-radius: 6px;
  margin-bottom: 12px;
}

.member {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border-bottom: 1px solid #f7f8fb;
}

.member:last-child {
  border-bottom: none;
}

.member-main {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  flex: 1;
}

.member-role {
  font-size: 12px;
}

.member-ops {
  display: flex;
  align-items: center;
  gap: 2px;
}

.invite {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  max-height: 320px;
  overflow: auto;
  font-family: 'Cascadia Code', Consolas, 'Courier New', monospace;
}

.muted {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
}
</style>
