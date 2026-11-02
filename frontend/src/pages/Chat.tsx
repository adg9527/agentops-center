import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Alert, App, Avatar, Badge, Button, Card, Checkbox, Descriptions, Drawer, Empty, Form, Input, List, Mentions, Modal,
  Popconfirm, Popover, Segmented, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import {
  CheckOutlined, CloseOutlined, CrownOutlined, PauseOutlined, PlayCircleOutlined, PlusOutlined, RedoOutlined,
  RobotOutlined, SendOutlined, StopOutlined, SwapOutlined, TeamOutlined, UserOutlined,
} from '@ant-design/icons'
import { api, type Agent } from '../api'
import { useAuth } from '../auth'
import { useLive } from '../live'
import { StatusTag, fmtTime } from '../labels'

export const ROLES: Record<string, { text: string; color: string }> = {
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
const HANDOFF_STATUS: Record<string, string> = { proposed: '待接收', accepted: '已接收', completed: '已完成', rejected: '失败', cancelled: '取消' }

interface Member { id: number; agent_id: number; name: string; role: string; enabled: boolean; health: string; model: string; risk_warnings: string[] }
interface Conv {
  id: number; name: string; coordinator_agent_id: number | null; require_plan_approval: boolean
  members?: Member[]; member_count?: number; active_tasks?: number; last_message?: { sender: string; content: string; at: string } | null
}
interface Msg { id: number; sender_type: 'user' | 'agent' | 'system'; sender_name: string; sender_id: number | null; kind: string; content: string; task_id: number | null; step_id: number | null; data: any; created_at: string }
export interface Step { id: number; step_key: string; kind: string; title: string; instruction: string; role: string; agent_id: number; agent_name: string; depends_on: string[]; status: string; requires_approval: boolean; origin: string; output_text: string | null; error: string | null; subtask_id: number | null }
export interface HandoffT { id: number; from_agent: string; to_agent: string; initiated_by: string; reason: string; context: string; done_work: string; remaining_work: string; files: string[]; result: string | null; status: string; created_at: string; accepted_at: string | null; completed_at: string | null }
export interface ApprovalT { id: number; kind: string; title: string; detail: any; status: string; comment: string; created_at: string; decided_at: string | null }
export interface CollabTask { id: number; conversation_id: number; title: string; prompt: string; mode: string; status: string; error: string | null; active: boolean; paused: boolean; steps: Step[]; handoffs: HandoffT[]; approvals: ApprovalT[]; created_at: string }

export default function Chat() {
  const [convs, setConvs] = useState<Conv[]>([])
  const [current, setCurrent] = useState<number | null>(null)
  const [creating, setCreating] = useState(false)
  const load = useCallback(() => api<Conv[]>('/api/conversations').then((c) => {
    setConvs(c)
    setCurrent((cur) => cur ?? c[0]?.id ?? null)
  }), [])
  useEffect(() => { load() }, [load])
  useLive((m) => { if (['message_created', 'conversation_updated', 'collab_updated'].includes(m.topic)) load() })

  return (
    <div style={{ display: 'flex', gap: 12, height: 'calc(100vh - 96px)', minHeight: 520 }}>
      <Card size="small" title={<Space><TeamOutlined />群聊</Space>} style={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
            styles={{ body: { padding: 0, overflow: 'auto', flex: 1 } }}
            extra={<Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => setCreating(true)} />}>
        <List dataSource={convs} locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有群聊" /> }}
              renderItem={(c) => (
                <List.Item onClick={() => setCurrent(c.id)} style={{ cursor: 'pointer', padding: '10px 12px', background: c.id === current ? '#17213a' : undefined }}>
                  <List.Item.Meta
                    title={<Space size={4}>{c.name}{!!c.active_tasks && <Badge count={c.active_tasks} size="small" color="#4f7cff" />}</Space>}
                    description={<Typography.Text type="secondary" ellipsis style={{ fontSize: 12, width: 190 }}>
                      {c.last_message ? `${c.last_message.sender}: ${c.last_message.content}` : `${c.member_count} 个 Agent`}
                    </Typography.Text>} />
                </List.Item>
              )} />
      </Card>
      {current ? <Room key={current} cid={current} onArchived={() => { setCurrent(null); load() }} />
        : <Card style={{ flex: 1 }}><Empty description="创建一个群聊，邀请已接入的 Agent 协作" /></Card>}
      {creating && <NewConv onClose={() => setCreating(false)} onCreated={(id) => { setCreating(false); setCurrent(id); load() }} />}
    </div>
  )
}

function NewConv({ onClose, onCreated }: { onClose: () => void; onCreated: (id: number) => void }) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const picked: number[] = Form.useWatch('agent_ids', form) ?? []
  useEffect(() => { api<Agent[]>('/api/agents').then(setAgents) }, [])
  return (
    <Modal open title="新建群聊" onCancel={onClose} onOk={async () => {
      const v = await form.validateFields()
      try { const c = await api<Conv>('/api/conversations', { body: v }); onCreated(c.id) } catch (e: any) { message.error(e.message) }
    }}>
      <Form form={form} layout="vertical" initialValues={{ require_plan_approval: false }}>
        <Form.Item name="name" label="群聊名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="agent_ids" label="成员 Agent" rules={[{ required: true }]}>
          <Select mode="multiple" options={agents.map((a) => ({ value: a.id, label: `${a.name}${a.enabled ? '' : '（已停用）'}`, disabled: !a.enabled }))} />
        </Form.Item>
        <Form.Item name="coordinator_agent_id" label="主控 Agent（自动协作时负责拆分与汇总）">
          <Select allowClear options={agents.filter((a) => picked.includes(a.id)).map((a) => ({ value: a.id, label: a.name }))} />
        </Form.Item>
        <Form.Item name="require_plan_approval" label="自动拆分的计划需人工审批后执行" valuePropName="checked"><Switch /></Form.Item>
      </Form>
    </Modal>
  )
}

function Room({ cid, onArchived }: { cid: number; onArchived: () => void }) {
  const [conv, setConv] = useState<Conv | null>(null)
  const [msgs, setMsgs] = useState<Msg[]>([])
  const [tasks, setTasks] = useState<CollabTask[]>([])
  const [focus, setFocus] = useState<number | null>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const { can } = useAuth()
  const { message } = App.useApp()

  const loadConv = useCallback(() => api<Conv>(`/api/conversations/${cid}`).then(setConv), [cid])
  const loadTasks = useCallback(() => api<CollabTask[]>(`/api/conversations/${cid}/tasks`).then(setTasks), [cid])
  useEffect(() => {
    loadConv(); loadTasks()
    api<Msg[]>(`/api/conversations/${cid}/messages`).then(setMsgs)
  }, [cid, loadConv, loadTasks])
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }) }, [msgs.length])

  useLive((m) => {
    if (m.topic === 'message_created' && m.data.conversation_id === cid) setMsgs((x) => (x.some((y) => y.id === m.data.id) ? x : [...x, m.data]))
    if (m.topic === 'collab_updated' && m.data.conversation_id === cid) loadTasks()
    if (m.topic === 'conversation_updated' && m.data.id === cid) loadConv()
  })

  if (!conv) return <Card loading style={{ flex: 1 }} />
  const members = conv.members ?? []
  const focused = tasks.find((t) => t.id === focus) ?? tasks[0]

  return (
    <>
      <Card size="small" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}
            styles={{ body: { flex: 1, display: 'flex', flexDirection: 'column', padding: 0, minHeight: 0 } }}
            title={<Space>{conv.name}<Typography.Text type="secondary" style={{ fontSize: 12 }}>{members.length} 个 Agent</Typography.Text></Space>}
            extra={can('operator') && <Popconfirm title="归档该群聊？" onConfirm={async () => { await api(`/api/conversations/${cid}`, { method: 'DELETE' }); onArchived() }}>
              <Button size="small" type="text">归档</Button></Popconfirm>}>
        <div style={{ flex: 1, overflow: 'auto', padding: '12px 16px' }}>
          {msgs.map((m) => <Bubble key={m.id} m={m} onTask={(id) => setFocus(id)} />)}
          <div ref={bottom} />
        </div>
        {can('operator') ? <Composer conv={conv} members={members} onSent={(tid) => { if (tid) setFocus(tid); loadTasks() }} />
          : <Alert type="info" banner message="只读账号不能发布任务" />}
      </Card>
      <Card size="small" style={{ width: 400, flexShrink: 0, display: 'flex', flexDirection: 'column' }} styles={{ body: { overflow: 'auto', flex: 1, paddingTop: 0 } }}>
        <Tabs size="small" items={[
          { key: 'task', label: `协作任务 (${tasks.length})`, children: tasks.length ? (
            <>
              <Select size="small" style={{ width: '100%', marginBottom: 8 }} value={focused?.id}
                      onChange={setFocus} options={tasks.map((t) => ({ value: t.id, label: `#${t.id} ${t.title}` }))} />
              {focused && <CollabPanel task={focused} members={members} onChange={loadTasks} />}
            </>) : <Empty description="发送消息并 @Agent 即可创建协作任务" /> },
          { key: 'members', label: '成员', children: <MembersPanel conv={conv} onChange={(c) => { setConv(c); message.success('已更新') }} /> },
        ]} />
      </Card>
    </>
  )
}

function Bubble({ m, onTask }: { m: Msg; onTask: (id: number) => void }) {
  const [open, setOpen] = useState(false)
  if (m.sender_type === 'system' || m.kind === 'intervention' && m.sender_type !== 'user') {
    return (
      <div style={{ textAlign: 'center', margin: '8px 0' }}>
        <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'pre-wrap', display: 'inline-block', textAlign: 'left',
                                                   background: m.kind === 'approval' ? '#2b2411' : '#121a2a', padding: '4px 10px', borderRadius: 6 }}>
          {m.content}{m.task_id && <a style={{ marginLeft: 6 }} onClick={() => onTask(m.task_id!)}>#{m.task_id}</a>}
        </Typography.Text>
      </div>
    )
  }
  const mine = m.sender_type === 'user'
  const long = m.content.length > 1200
  const text = long && !open ? m.content.slice(0, 1200) + '…' : m.content
  return (
    <div style={{ display: 'flex', flexDirection: mine ? 'row-reverse' : 'row', gap: 8, margin: '10px 0' }}>
      <Avatar size={32} style={{ background: mine ? '#4f7cff' : '#2c3a55', flexShrink: 0 }} icon={mine ? <UserOutlined /> : <RobotOutlined />} />
      <div style={{ maxWidth: '78%' }}>
        <Space size={4} style={{ fontSize: 12, flexDirection: mine ? 'row-reverse' : 'row' }}>
          <Typography.Text strong style={{ fontSize: 12 }}>{m.sender_name}</Typography.Text>
          {m.data?.role && <Tag color={ROLES[m.data.role]?.color} style={{ fontSize: 10, lineHeight: '16px' }}>{ROLES[m.data.role]?.text}</Tag>}
          {m.kind === 'plan' && <Tag color="gold">计划</Tag>}
          {m.kind === 'intervention' && <Tag color="orange">人工介入</Tag>}
          <Typography.Text type="secondary" style={{ fontSize: 11 }}>{fmtTime(m.created_at)}</Typography.Text>
          {m.task_id && <a style={{ fontSize: 11 }} onClick={() => onTask(m.task_id!)}>#{m.task_id}</a>}
        </Space>
        <div style={{ background: mine ? '#1d3266' : '#151d2c', border: '1px solid #222c40', borderRadius: 8, padding: '8px 12px', marginTop: 2,
                      whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13 }}>
          {text}
          {long && <div><a onClick={() => setOpen(!open)}>{open ? '收起' : '展开全文'}</a></div>}
        </div>
      </div>
    </div>
  )
}

function Composer({ conv, members, onSent }: { conv: Conv; members: Member[]; onSent: (taskId: number | null) => void }) {
  const [text, setText] = useState('')
  const [mode, setMode] = useState<'manual' | 'auto' | 'chat'>('manual')
  const [assign, setAssign] = useState<Record<number, string | undefined>>({})
  const [busy, setBusy] = useState(false)
  const { message } = App.useApp()
  const assignments = Object.entries(assign).filter(([, r]) => r).map(([id, role]) => ({ agent_id: Number(id), role }))
  const coordinator = members.find((m) => m.agent_id === conv.coordinator_agent_id)

  const send = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      const r = await api<{ task: { id: number } | null }>(`/api/conversations/${conv.id}/messages`, {
        body: { content: text, mode, mention_all: /@(全体|all)\b/.test(text) || text.includes('@全体'), assignments: mode === 'chat' ? [] : assignments },
      })
      setText('')
      setAssign({})
      onSent(r.task?.id ?? null)
    } catch (e: any) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  const hint = mode === 'manual' ? '@Agent 指定执行者，按成员角色（分析→执行→审查→汇总）依次执行；或用「分工」指定'
    : mode === 'auto' ? (coordinator ? `由主控 ${coordinator.name} 自动拆分、分配并汇总` : '未设置主控：平台按成员角色自动分工（可在「成员」中设置主控）')
      : '仅记录消息，不发起任务'

  return (
    <div style={{ borderTop: '1px solid #1f2735', padding: 12 }}>
      <Space style={{ marginBottom: 8 }} wrap>
        <Segmented size="small" value={mode} onChange={(v) => setMode(v as any)}
                   options={[{ value: 'manual', label: '人工指定' }, { value: 'auto', label: '自动协作' }, { value: 'chat', label: '仅聊天' }]} />
        {mode !== 'chat' && (
          <Popover trigger="click" title="本次分工（覆盖成员默认角色）" content={
            <Table size="small" pagination={false} rowKey="agent_id" dataSource={members} style={{ width: 300 }}
                   columns={[{ title: 'Agent', dataIndex: 'name' }, { title: '角色', render: (_, m) => (
                     <Select size="small" allowClear placeholder="不参与" style={{ width: 110 }} value={assign[m.agent_id]}
                             options={ROLE_OPTIONS} onChange={(v) => setAssign((a) => ({ ...a, [m.agent_id]: v }))} />) }]} />
          }>
            <Button size="small">分工{assignments.length ? `（${assignments.length}）` : ''}</Button>
          </Popover>
        )}
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hint}</Typography.Text>
      </Space>
      <div style={{ display: 'flex', gap: 8 }}>
        <Mentions value={text} onChange={setText} autoSize={{ minRows: 2, maxRows: 8 }} placeholder="输入任务，@ 选择 Agent，Ctrl+Enter 发送"
                  options={[{ value: '全体', label: '全体成员' }, ...members.map((m) => ({ value: m.name, label: `${m.name}（${ROLES[m.role]?.text}）` }))]}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send() } }} style={{ flex: 1 }} />
        <Button type="primary" icon={<SendOutlined />} loading={busy} onClick={send} style={{ height: 'auto' }}>发送</Button>
      </div>
    </div>
  )
}

function MembersPanel({ conv, onChange }: { conv: Conv; onChange: (c: Conv) => void }) {
  const [agents, setAgents] = useState<Agent[]>([])
  const [adding, setAdding] = useState<number>()
  const { message } = App.useApp()
  useEffect(() => { api<Agent[]>('/api/agents').then(setAgents) }, [])
  const call = async (path: string, method: string, body?: unknown) => {
    try { onChange(await api<Conv>(path, { method, body })) } catch (e: any) { message.error(e.message) }
  }
  const members = conv.members ?? []
  const outside = agents.filter((a) => !members.some((m) => m.agent_id === a.id))
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <List size="small" dataSource={members} renderItem={(m) => (
        <List.Item actions={[
          <Tooltip key="c" title={conv.coordinator_agent_id === m.agent_id ? '取消主控' : '设为主控'}>
            <Button size="small" type={conv.coordinator_agent_id === m.agent_id ? 'primary' : 'text'} icon={<CrownOutlined />}
                    onClick={() => call(`/api/conversations/${conv.id}`, 'PATCH', conv.coordinator_agent_id === m.agent_id ? { clear_coordinator: true } : { coordinator_agent_id: m.agent_id })} />
          </Tooltip>,
          <Popconfirm key="r" title={`将 ${m.name} 移出群聊？`} onConfirm={() => call(`/api/conversations/${conv.id}/members/${m.agent_id}`, 'DELETE')}>
            <Button size="small" type="text" danger icon={<CloseOutlined />} />
          </Popconfirm>,
        ]}>
          <Space direction="vertical" size={0}>
            <Space size={4}><Badge status={!m.enabled ? 'default' : m.health === 'online' ? 'success' : m.health === 'offline' ? 'error' : 'default'} />
              <b>{m.name}</b>{m.risk_warnings.length > 0 && <Tooltip title={`高风险配置，执行前需管理员审批：${m.risk_warnings.join('；')}`}><Tag color="warning">需审批</Tag></Tooltip>}</Space>
            <Select size="small" variant="borderless" value={m.role} options={ROLE_OPTIONS} style={{ width: 90 }}
                    onChange={(role) => call(`/api/conversations/${conv.id}/members/${m.agent_id}`, 'PATCH', { role })} />
          </Space>
        </List.Item>
      )} />
      <Space.Compact style={{ width: '100%' }}>
        <Select placeholder="邀请 Agent 加入" style={{ flex: 1 }} value={adding} onChange={setAdding}
                options={outside.map((a) => ({ value: a.id, label: a.name, disabled: !a.enabled }))} />
        <Button disabled={!adding} onClick={() => { call(`/api/conversations/${conv.id}/members`, 'POST', { agent_id: adding, role: 'executor' }); setAdding(undefined) }}>添加</Button>
      </Space.Compact>
      <Checkbox checked={conv.require_plan_approval} onChange={(e) => call(`/api/conversations/${conv.id}`, 'PATCH', { require_plan_approval: e.target.checked })}>
        自动拆分的计划需人工审批
      </Checkbox>
    </Space>
  )
}

export function CollabPanel({ task, members, onChange }: { task: CollabTask; members: Member[]; onChange: () => void }) {
  const [handoff, setHandoff] = useState<HandoffT | null>(null)
  const [step, setStep] = useState<Step | null>(null)
  const [note, setNote] = useState('')
  const { message } = App.useApp()
  const { can } = useAuth()
  const live = task.active && !['completed', 'failed', 'cancelled'].includes(task.status)
  const pending = task.approvals.filter((a) => a.status === 'pending')
  const call = async (path: string, body?: unknown) => {
    try { await api(path, { body: body ?? {} }); onChange() } catch (e: any) { message.error(e.message) }
  }
  const memberOpts = useMemo(() => members.map((m) => ({ value: m.agent_id, label: m.name })), [members])

  return (
    <Space direction="vertical" style={{ width: '100%' }} size={10}>
      <Space wrap>
        <StatusTag status={task.status} />
        <Tag>{task.mode === 'auto' ? '自动协作' : '人工指定'}</Tag>
        {task.paused && <Tag color="warning">已请求暂停</Tag>}
        <Link to={`/tasks/${task.id}`}>执行时间线</Link>
      </Space>
      {task.error && <Alert type="error" showIcon message={task.error} />}
      {can('operator') && live && (
        <Space wrap>
          {task.paused ? <Button size="small" icon={<PlayCircleOutlined />} onClick={() => call(`/api/collab/tasks/${task.id}/resume`)}>恢复</Button>
            : <Tooltip title="当前步骤完成后生效；外部 Agent 进程无法冻结"><Button size="small" icon={<PauseOutlined />} onClick={() => call(`/api/collab/tasks/${task.id}/pause`)}>暂停</Button></Tooltip>}
          <Popconfirm title="终止任务并结束所有 Agent 进程？" onConfirm={() => call(`/api/collab/tasks/${task.id}/cancel`)}>
            <Button size="small" danger icon={<StopOutlined />}>终止</Button>
          </Popconfirm>
        </Space>
      )}

      {pending.map((a) => (
        <Alert key={a.id} type="warning" showIcon message={a.title}
               description={a.kind === 'plan' ? (
                 <ol style={{ paddingLeft: 18, margin: '4px 0' }}>{a.detail.steps.map((s: any) => <li key={s.key}>[{s.key}] {s.title}{s.depends_on.length ? ` ← ${s.depends_on.join(',')}` : ''}</li>)}</ol>
               ) : <>风险：{a.detail.risk_warnings?.join('；')}<br />任务：{a.detail.instruction}</>}
               action={can('operator') && <Space direction="vertical">
                 <Button size="small" type="primary" icon={<CheckOutlined />} onClick={() => call(`/api/approvals/${a.id}/approve`)}>批准</Button>
                 <Button size="small" danger icon={<CloseOutlined />} onClick={() => call(`/api/approvals/${a.id}/reject`, { comment: '' })}>拒绝</Button>
               </Space>} />
      ))}

      <Typography.Text strong>步骤</Typography.Text>
      <List size="small" bordered dataSource={task.steps} renderItem={(s) => (
        <List.Item style={{ cursor: 'pointer', padding: '6px 10px' }} onClick={() => setStep(s)}>
          <Space direction="vertical" size={0} style={{ width: '100%' }}>
            <Space size={4} wrap>
              <Tag color={STEP_STATUS[s.status]?.color}>{STEP_STATUS[s.status]?.text ?? s.status}</Tag>
              <Typography.Text code style={{ fontSize: 11 }}>{s.step_key}</Typography.Text>
              <b>{s.agent_name}</b>
              <Tag color={ROLES[s.role]?.color} style={{ fontSize: 10 }}>{s.kind === 'plan' ? '规划' : s.kind === 'summary' ? '汇总' : ROLES[s.role]?.text}</Tag>
              {s.origin === 'handoff' && <Tag color="purple">交接</Tag>}
              {s.origin === 'intervention' && <Tag color="orange">追加</Tag>}
            </Space>
            <Typography.Text type="secondary" ellipsis style={{ fontSize: 12 }}>{s.title}{s.depends_on.length ? ` ← ${s.depends_on.join(', ')}` : ''}</Typography.Text>
          </Space>
        </List.Item>
      )} />

      <Typography.Text strong>任务交接（{task.handoffs.length}）</Typography.Text>
      {task.handoffs.length ? (
        <List size="small" bordered dataSource={task.handoffs} renderItem={(h) => (
          <List.Item style={{ cursor: 'pointer', padding: '6px 10px' }} onClick={() => setHandoff(h)}>
            <Space direction="vertical" size={0}>
              <Space size={4}><SwapOutlined />{h.from_agent} → {h.to_agent}<Tag>{HANDOFF_STATUS[h.status] ?? h.status}</Tag>
                {h.initiated_by === 'agent' && <Tag color="purple">Agent 发起</Tag>}{h.initiated_by === 'user' && <Tag color="orange">人工</Tag>}</Space>
              <Typography.Text type="secondary" ellipsis style={{ fontSize: 12, maxWidth: 330 }}>{h.reason}</Typography.Text>
            </Space>
          </List.Item>
        )} />
      ) : <Typography.Text type="secondary">暂无</Typography.Text>}

      {can('operator') && (
        <>
          <Typography.Text strong>人工介入</Typography.Text>
          <Input.TextArea rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="补充说明 / 修改要求，将注入后续所有步骤" />
          <Space wrap>
            <Button size="small" disabled={!note.trim()} onClick={() => { call(`/api/collab/tasks/${task.id}/intervene`, { note }); setNote('') }}>提交补充说明</Button>
            <AddStep task={task} memberOpts={memberOpts} onDone={onChange} />
          </Space>
        </>
      )}

      <Drawer open={!!handoff} width={560} title="任务交接详情" onClose={() => setHandoff(null)}>
        {handoff && (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="交接 ID">{handoff.id}</Descriptions.Item>
            <Descriptions.Item label="交出方 → 接收方">{handoff.from_agent} → {handoff.to_agent}</Descriptions.Item>
            <Descriptions.Item label="发起方">{{ platform: '平台调度', agent: 'Agent 主动', user: '人工' }[handoff.initiated_by]}</Descriptions.Item>
            <Descriptions.Item label="接收状态">{HANDOFF_STATUS[handoff.status]}</Descriptions.Item>
            <Descriptions.Item label="交接原因">{handoff.reason}</Descriptions.Item>
            <Descriptions.Item label="已完成工作"><Pre>{handoff.done_work}</Pre></Descriptions.Item>
            <Descriptions.Item label="尚未完成工作"><Pre>{handoff.remaining_work}</Pre></Descriptions.Item>
            <Descriptions.Item label="相关文件">{handoff.files.length ? handoff.files.map((f) => <Tag key={f}>{f}</Tag>) : '无'}</Descriptions.Item>
            <Descriptions.Item label="执行结果"><Pre>{handoff.result ?? '—'}</Pre></Descriptions.Item>
            <Descriptions.Item label="任务上下文"><Pre>{handoff.context || '—'}</Pre></Descriptions.Item>
            <Descriptions.Item label="交接 / 接收 / 完成时间">{fmtTime(handoff.created_at)} / {fmtTime(handoff.accepted_at)} / {fmtTime(handoff.completed_at)}</Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
      <Drawer open={!!step} width={600} title={step && `步骤 ${step.step_key} · ${step.title}`} onClose={() => setStep(null)}
              extra={step && can('operator') && <StepActions step={step} memberOpts={memberOpts} onDone={() => { setStep(null); onChange() }} />}>
        {step && (
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="执行 Agent">{step.agent_name}（{ROLES[step.role]?.text}）</Descriptions.Item>
            <Descriptions.Item label="状态">{STEP_STATUS[step.status]?.text}</Descriptions.Item>
            <Descriptions.Item label="依赖">{step.depends_on.join(', ') || '无'}</Descriptions.Item>
            <Descriptions.Item label="来源">{{ user: '用户分工', plan: '主控计划', handoff: 'Agent 交接', intervention: '人工追加' }[step.origin] ?? step.origin}</Descriptions.Item>
            <Descriptions.Item label="子任务">{step.subtask_id ? <Link to={`/tasks/${step.subtask_id}`}>#{step.subtask_id}（含完整执行事件）</Link> : '未开始'}</Descriptions.Item>
            <Descriptions.Item label="指令"><Pre>{step.instruction}</Pre></Descriptions.Item>
            {step.error && <Descriptions.Item label="错误"><Pre>{step.error}</Pre></Descriptions.Item>}
            <Descriptions.Item label="输出"><Pre>{step.output_text ?? '—'}</Pre></Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </Space>
  )
}

function Pre({ children }: { children: string }) {
  return <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 12, maxHeight: 320, overflow: 'auto' }}>{children}</pre>
}

function StepActions({ step, memberOpts, onDone }: { step: Step; memberOpts: { value: number; label: string }[]; onDone: () => void }) {
  const [to, setTo] = useState<number>()
  const { message } = App.useApp()
  const call = async (path: string, body?: unknown) => {
    try { await api(path, { body: body ?? {} }); message.success('已提交'); onDone() } catch (e: any) { message.error(e.message) }
  }
  const reassignable = ['pending', 'waiting_approval', 'failed', 'skipped', 'cancelled'].includes(step.status)
  return (
    <Space>
      {['failed', 'cancelled', 'skipped'].includes(step.status) && <Button size="small" icon={<RedoOutlined />} onClick={() => call(`/api/collab/steps/${step.id}/retry`)}>重试</Button>}
      {['pending', 'waiting_approval'].includes(step.status) && <Button size="small" onClick={() => call(`/api/collab/steps/${step.id}/skip`)}>跳过</Button>}
      {reassignable && (
        <Space.Compact>
          <Select size="small" placeholder="改派给" style={{ width: 120 }} value={to} onChange={setTo} options={memberOpts.filter((o) => o.value !== step.agent_id)} />
          <Button size="small" disabled={!to} onClick={() => call(`/api/collab/steps/${step.id}/reassign`, { agent_id: to })}>改派</Button>
        </Space.Compact>
      )}
    </Space>
  )
}

function AddStep({ task, memberOpts, onDone }: { task: CollabTask; memberOpts: { value: number; label: string }[]; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { message } = App.useApp()
  return (
    <>
      <Button size="small" icon={<PlusOutlined />} onClick={() => setOpen(true)}>追加步骤</Button>
      <Modal open={open} title="追加步骤" onCancel={() => setOpen(false)} onOk={async () => {
        const v = await form.validateFields()
        try { await api(`/api/collab/tasks/${task.id}/steps`, { body: v }); setOpen(false); form.resetFields(); onDone() } catch (e: any) { message.error(e.message) }
      }}>
        <Form form={form} layout="vertical" initialValues={{ role: 'executor', depends_on: [] }}>
          <Form.Item name="agent_id" label="执行 Agent" rules={[{ required: true }]}><Select options={memberOpts} /></Form.Item>
          <Form.Item name="role" label="角色"><Select options={ROLE_OPTIONS} /></Form.Item>
          <Form.Item name="title" label="标题" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="instruction" label="指令" rules={[{ required: true }]}><Input.TextArea rows={4} /></Form.Item>
          <Form.Item name="depends_on" label="依赖步骤（完成后才执行，并接收其交接）">
            <Select mode="multiple" options={task.steps.map((s) => ({ value: s.step_key, label: `${s.step_key} ${s.title}` }))} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
