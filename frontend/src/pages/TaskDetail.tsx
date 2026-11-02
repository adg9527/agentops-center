import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { CollabPanel, type CollabTask } from './Chat'
import { Alert, App, Button, Card, Col, Descriptions, Drawer, Empty, Popconfirm, Row, Segmented, Space, Tabs, Tag, Timeline, Typography } from 'antd'
import { ArrowLeftOutlined, StopOutlined } from '@ant-design/icons'
import { api, type Task, type TraceEvent } from '../api'
import { useAuth } from '../auth'
import { useLive } from '../live'
import { EVENT_TYPES, EventTag, StatusTag, fmtCost, fmtMs, fmtNum, fmtTime } from '../labels'

const pre = { whiteSpace: 'pre-wrap' as const, wordBreak: 'break-word' as const, fontSize: 12, margin: 0, maxHeight: 420, overflow: 'auto' }

export default function TaskDetail() {
  const { id } = useParams()
  const nav = useNavigate()
  const { can } = useAuth()
  const { message } = App.useApp()
  const [task, setTask] = useState<Task | null>(null)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [selected, setSelected] = useState<TraceEvent | null>(null)

  const load = useCallback(() => {
    api<Task>(`/api/tasks/${id}`).then(setTask).catch((e) => message.error(e.message))
    api<TraceEvent[]>(`/api/tasks/${id}/events`).then(setEvents)
  }, [id, message])
  useEffect(load, [load])

  useLive((m) => {
    if (m.topic === 'trace_event' && String(m.data.task_id) === id) {
      setEvents((ev) => (ev.some((e) => e.event_id === m.data.event_id) ? ev : [...ev, m.data]))
    } else if ((m.topic === 'task_updated' && String(m.data.id) === id) || (m.topic === 'run_updated' && String(m.data.task_id) === id)) {
      api<Task>(`/api/tasks/${id}`).then(setTask)
    }
  })

  const agentName = useMemo(() => Object.fromEntries((task?.runs ?? []).map((r) => [r.agent_id, r.agent_name])), [task])
  const shown = events.filter((e) => filter === 'all' || (filter === 'tools' ? e.event_type.startsWith('tool.') || e.event_type === 'permission.denied'
    : filter === 'messages' ? e.event_type === 'agent.message' : e.event_type.startsWith('task.') || e.event_type.startsWith('agent.run')))

  if (!task) return <Card loading />
  const active = task.status === 'running' || task.status === 'pending'

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card size="small" title={<Space><Button type="text" icon={<ArrowLeftOutlined />} onClick={() => nav('/tasks')} />任务 #{task.id} {task.title}<StatusTag status={task.status} /></Space>}
            extra={can('operator') && active && (
              <Popconfirm title="终止任务？" description="将结束所有正在执行的 Agent 进程。" onConfirm={async () => {
                try { await api(`/api/tasks/${task.id}/cancel`, { method: 'POST' }); message.success('已发送终止信号') } catch (e: any) { message.error(e.message) }
              }}><Button danger icon={<StopOutlined />}>终止任务</Button></Popconfirm>)}>
        <Descriptions size="small" column={{ xs: 1, md: 3 }}>
          <Descriptions.Item label="创建">{fmtTime(task.created_at)}</Descriptions.Item>
          <Descriptions.Item label="开始">{fmtTime(task.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束">{fmtTime(task.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="Trace ID"><Typography.Text code copyable>{task.trace_id}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="工作目录" span={2}><Typography.Text code copyable style={{ fontSize: 12 }}>{task.workspace_dir}</Typography.Text></Descriptions.Item>
        </Descriptions>
        {task.error && <Alert type="error" showIcon message={task.error} style={{ marginTop: 8 }} />}
        <Typography.Paragraph style={{ marginTop: 12, marginBottom: 0 }}><Typography.Text type="secondary">任务内容：</Typography.Text></Typography.Paragraph>
        <pre style={{ ...pre, maxHeight: 160, background: '#0e1420', padding: 10, borderRadius: 6 }}>{task.prompt}</pre>
      </Card>

      {task.parent_task_id && (
        <Alert type="info" showIcon message={<>这是协作任务 <Link to={`/tasks/${task.parent_task_id}`}>#{task.parent_task_id}</Link> 的一个步骤（子任务），与其共享 Trace 与工作目录。</>} />
      )}
      {task.conversation_id && !task.parent_task_id && <CollabCard taskId={task.id} conversationId={task.conversation_id} />}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card size="small" title="Agent 结果">
            <Tabs items={task.runs.map((r) => ({
              key: String(r.id),
              label: <Space size={4}>{r.agent_name}{r.attempt > 1 && <Tag>第{r.attempt}次</Tag>}<StatusTag status={r.status} /></Space>,
              children: (
                <>
                  <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
                    <Descriptions.Item label="模型">{r.model_used ?? '—'}</Descriptions.Item>
                    <Descriptions.Item label="耗时">{fmtMs(r.duration_ms)}</Descriptions.Item>
                    <Descriptions.Item label="Token 入 / 出">{fmtNum(r.input_tokens)} / {fmtNum(r.output_tokens)}</Descriptions.Item>
                    <Descriptions.Item label="成本">{fmtCost(r.cost_usd)}</Descriptions.Item>
                    <Descriptions.Item label="会话 ID" span={2}><Typography.Text style={{ fontSize: 12 }} copyable={!!r.external_session_id}>{r.external_session_id ?? '—'}</Typography.Text></Descriptions.Item>
                    <Descriptions.Item label="配置版本">v{r.agent_config_version}</Descriptions.Item>
                    <Descriptions.Item label="Span">{r.span_id}</Descriptions.Item>
                  </Descriptions>
                  {r.error && <Alert type="error" showIcon message={r.error} style={{ marginBottom: 12 }} />}
                  {r.output_text ? <pre style={{ ...pre, background: '#0e1420', padding: 12, borderRadius: 6 }}>{r.output_text}</pre>
                    : <Empty description={r.status === 'running' || r.status === 'pending' ? '执行中，等待结果…' : '无输出'} />}
                </>
              ),
            }))} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card size="small" title={`执行时间线（${events.length} 个事件）`}
                extra={<Segmented size="small" value={filter} onChange={(v) => setFilter(v as string)}
                                  options={[{ label: '全部', value: 'all' }, { label: '工具', value: 'tools' }, { label: '消息', value: 'messages' }, { label: '生命周期', value: 'life' }]} />}>
            <div style={{ maxHeight: 640, overflow: 'auto', paddingTop: 8 }}>
              {shown.length ? (
                <Timeline items={shown.map((e) => ({
                  color: e.status === 'error' || e.status === 'denied' ? 'red' : e.event_type.startsWith('tool.') ? 'gold' : 'blue',
                  children: (
                    <div style={{ cursor: 'pointer', marginLeft: (e.event_type.startsWith('tool.') ? 16 : 0) }} onClick={() => setSelected(e)}>
                      <Space size={4} wrap>
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>{fmtTime(e.timestamp)}</Typography.Text>
                        <EventTag type={e.event_type} />
                        {e.agent_id && <Tag>{agentName[e.agent_id] ?? `Agent#${e.agent_id}`}</Tag>}
                        <b>{e.name}</b>
                        {e.duration_ms != null && <Typography.Text type="secondary">{fmtMs(e.duration_ms)}</Typography.Text>}
                      </Space>
                      {(e.input_summary || e.output_summary) && (
                        <Typography.Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ fontSize: 12, margin: '2px 0 0' }}>
                          {e.output_summary || e.input_summary}
                        </Typography.Paragraph>
                      )}
                    </div>
                  ),
                }))} />
              ) : <Empty description="暂无事件" />}
            </div>
          </Card>
        </Col>
      </Row>
      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </Space>
  )
}

function CollabCard({ taskId, conversationId }: { taskId: number; conversationId: number }) {
  const [task, setTask] = useState<CollabTask | null>(null)
  const [members, setMembers] = useState<any[]>([])
  const load = useCallback(() => { api<CollabTask>(`/api/collab/tasks/${taskId}`).then(setTask) }, [taskId])
  useEffect(() => {
    load()
    api<any>(`/api/conversations/${conversationId}`).then((c) => setMembers(c.members ?? [])).catch(() => setMembers([]))
  }, [load, conversationId])
  useLive((m) => { if (m.topic === 'collab_updated' && m.data.root_task_id === taskId) load() })
  if (!task) return null
  return (
    <Card size="small" title={<>协作过程 <Link to="/chat" style={{ fontSize: 12, marginLeft: 8 }}>在群聊中查看</Link></>}>
      <CollabPanel task={task} members={members} onChange={load} />
    </Card>
  )
}

export function EventDrawer({ event, onClose }: { event: TraceEvent | null; onClose: () => void }) {
  return (
    <Drawer open={!!event} width={600} title={event && (EVENT_TYPES[event.event_type]?.text ?? event.event_type)} onClose={onClose}>
      {event && (
        <Descriptions column={1} size="small" bordered>
          <Descriptions.Item label="事件类型">{event.event_type}</Descriptions.Item>
          <Descriptions.Item label="名称">{event.name}</Descriptions.Item>
          <Descriptions.Item label="时间">{event.timestamp}</Descriptions.Item>
          <Descriptions.Item label="状态">{event.status}</Descriptions.Item>
          <Descriptions.Item label="耗时">{fmtMs(event.duration_ms)}</Descriptions.Item>
          <Descriptions.Item label="trace_id">{event.trace_id}</Descriptions.Item>
          <Descriptions.Item label="span_id / parent">{event.span_id} / {event.parent_span_id ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="task / run / agent">{event.task_id} / {event.run_id ?? '—'} / {event.agent_id ?? '—'}</Descriptions.Item>
          <Descriptions.Item label="输入摘要"><pre style={pre}>{event.input_summary ?? '—'}</pre></Descriptions.Item>
          <Descriptions.Item label="输出摘要"><pre style={pre}>{event.output_summary ?? '—'}</pre></Descriptions.Item>
          <Descriptions.Item label="metadata"><pre style={pre}>{JSON.stringify(event.metadata, null, 2)}</pre></Descriptions.Item>
        </Descriptions>
      )}
    </Drawer>
  )
}
