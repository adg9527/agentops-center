import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Alert, Button, Card, DatePicker, Input, Select, Space, Switch, Table, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import type { Dayjs } from 'dayjs'
import { api, type Agent, type TraceEvent } from '../api'
import { useLive } from '../live'
import { EVENT_TYPES, EventTag, fmtMs, fmtTime } from '../labels'
import { EventDrawer } from './TaskDetail'

export default function Monitor() {
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [agentId, setAgentId] = useState<number>()
  const [type, setType] = useState<string>()
  const [taskId, setTaskId] = useState<string>('')
  const [range, setRange] = useState<[Dayjs | null, Dayjs | null] | null>(null)
  const [follow, setFollow] = useState(true)
  const [selected, setSelected] = useState<TraceEvent | null>(null)

  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: '300' })
    if (agentId) q.set('agent_id', String(agentId))
    if (type) q.set('event_type', type)
    if (taskId) q.set('task_id', taskId)
    if (range?.[0]) q.set('since', range[0].toISOString())
    if (range?.[1]) q.set('until', range[1].toISOString())
    api<TraceEvent[]>(`/api/events?${q}`).then(setEvents)
  }, [agentId, type, taskId, range])
  useEffect(load, [load])
  useEffect(() => { api<Agent[]>('/api/agents').then(setAgents) }, [])

  useLive((m) => {
    if (!follow || m.topic !== 'trace_event') return
    const e: TraceEvent = m.data
    if ((agentId && e.agent_id !== agentId) || (type && !e.event_type.startsWith(type)) || (taskId && String(e.task_id) !== taskId) || range) return
    setEvents((ev) => [e, ...ev].slice(0, 500))
  })

  const names = Object.fromEntries(agents.map((a) => [a.id, a.name]))
  return (
    <Card title="执行监控" extra={<Space wrap>
      <Input allowClear placeholder="任务 ID" style={{ width: 100 }} value={taskId} onChange={(e) => setTaskId(e.target.value.replace(/\D/g, ''))} />
      <Select allowClear placeholder="Agent" style={{ width: 150 }} value={agentId} onChange={setAgentId} options={agents.map((a) => ({ value: a.id, label: a.name }))} />
      <Select allowClear placeholder="事件类型" style={{ width: 150 }} value={type} onChange={setType}
              options={[{ value: 'task.', label: '任务生命周期' }, { value: 'agent.run', label: 'Agent 执行' }, ...Object.entries(EVENT_TYPES).filter(([k]) => !k.startsWith('task.') && !k.startsWith('agent.run')).map(([k, v]) => ({ value: k, label: v.text }))]} />
      <DatePicker.RangePicker showTime value={range as any} onChange={(v) => setRange(v as any)} />
      <Space size={4}><Switch size="small" checked={follow} onChange={setFollow} /><Typography.Text type="secondary">实时</Typography.Text></Space>
      <Button icon={<ReloadOutlined />} onClick={load} />
    </Space>}>
      <Alert type="info" showIcon style={{ marginBottom: 12 }}
             message="第一阶段：实时事件流与多维筛选已可用。Trace 瀑布图、单 Agent 轨迹视图、任务回放与日志全文检索在第三阶段实现。不采集模型内部思维链。" />
      <Table<TraceEvent> size="small" rowKey="event_id" dataSource={events} scroll={{ x: 1000 }} pagination={{ pageSize: 50 }}
                         onRow={(e) => ({ onClick: () => setSelected(e), style: { cursor: 'pointer' } })}
                         columns={[
                           { title: '时间', dataIndex: 'timestamp', width: 130, render: fmtTime },
                           { title: '类型', dataIndex: 'event_type', width: 130, render: (t) => <EventTag type={t} /> },
                           { title: '任务', dataIndex: 'task_id', width: 70, render: (t) => t && <Link to={`/tasks/${t}`} onClick={(e) => e.stopPropagation()}>#{t}</Link> },
                           { title: 'Agent', dataIndex: 'agent_id', width: 130, render: (a) => (a ? names[a] ?? `#${a}` : '—') },
                           { title: '名称', dataIndex: 'name', width: 160, ellipsis: true },
                           { title: '状态', dataIndex: 'status', width: 70 },
                           { title: '耗时', dataIndex: 'duration_ms', width: 80, render: fmtMs },
                           { title: '摘要', ellipsis: true, render: (_, e) => e.output_summary || e.input_summary },
                         ]} />
      <EventDrawer event={selected} onClose={() => setSelected(null)} />
    </Card>
  )
}
