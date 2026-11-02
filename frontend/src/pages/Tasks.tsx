import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, App, Button, Card, Form, Input, Modal, Select, Space, Table, Tag, Typography } from 'antd'
import { PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { api, type Agent, type Task } from '../api'
import { useAuth } from '../auth'
import { useLive } from '../live'
import { StatusTag, TASK_STATUS, fmtCost, fmtTime } from '../labels'

export default function Tasks() {
  const [data, setData] = useState<{ total: number; items: Task[] }>({ total: 0, items: [] })
  const [status, setStatus] = useState<string>()
  const [agentId, setAgentId] = useState<number>()
  const [page, setPage] = useState(1)
  const [agents, setAgents] = useState<Agent[]>([])
  const [open, setOpen] = useState(false)
  const { can } = useAuth()
  const nav = useNavigate()

  const load = useCallback(() => {
    const q = new URLSearchParams({ limit: '20', offset: String((page - 1) * 20) })
    if (status) q.set('status', status)
    if (agentId) q.set('agent_id', String(agentId))
    api(`/api/tasks?${q}`).then(setData)
  }, [status, agentId, page])
  useEffect(load, [load])
  useEffect(() => { api<Agent[]>('/api/agents').then(setAgents) }, [])
  useLive((m) => (m.topic === 'task_updated' || m.topic === 'run_updated') && load())

  return (
    <Card title="任务中心" extra={<Space wrap>
      <Select allowClear placeholder="状态" style={{ width: 120 }} value={status} onChange={(v) => { setStatus(v); setPage(1) }}
              options={Object.entries(TASK_STATUS).filter(([k]) => k !== 'timeout').map(([k, v]) => ({ value: k, label: v.text }))} />
      <Select allowClear placeholder="Agent" style={{ width: 160 }} value={agentId} onChange={(v) => { setAgentId(v); setPage(1) }}
              options={agents.map((a) => ({ value: a.id, label: a.name }))} />
      <Button icon={<ReloadOutlined />} onClick={load} />
      {can('operator') && <Button type="primary" icon={<PlusOutlined />} onClick={() => setOpen(true)}>发送任务</Button>}
    </Space>}>
      <Table<Task> rowKey="id" dataSource={data.items} scroll={{ x: 800 }}
                   pagination={{ current: page, pageSize: 20, total: data.total, onChange: setPage, showSizeChanger: false }}
                   onRow={(t) => ({ onClick: () => nav(`/tasks/${t.id}`), style: { cursor: 'pointer' } })}
                   columns={[
                     { title: 'ID', dataIndex: 'id', width: 70, render: (v) => `#${v}` },
                     { title: '标题', dataIndex: 'title', ellipsis: true, render: (v, t) => <>{t.mode === 'auto' ? <Tag color="gold">自动协作</Tag> : t.mode === 'manual' ? <Tag color="cyan">群聊分工</Tag> : null}{v}</> },
                     { title: '状态', dataIndex: 'status', width: 100, render: (s) => <StatusTag status={s} /> },
                     { title: 'Agent', render: (_, t) => <Space size={4} wrap>{t.runs.filter((r, i, all) => all.findIndex((x) => x.agent_id === r.agent_id) === i)
                         .map((r) => <Tag key={r.agent_id}>{r.agent_name}</Tag>)}</Space> },
                     { title: '成本', width: 100, render: (_, t) => {
                         const known = t.runs.filter((r) => r.cost_usd != null)
                         return known.length ? fmtCost(known.reduce((s, r) => s + (r.cost_usd ?? 0), 0)) : '—'
                       } },
                     { title: '创建时间', dataIndex: 'created_at', width: 140, render: fmtTime },
                   ]} />
      {open && <NewTask agents={agents} onClose={() => setOpen(false)} onCreated={(id) => nav(`/tasks/${id}`)} />}
    </Card>
  )
}

export function NewTask({ agents, onClose, onCreated }: { agents: Agent[]; onClose: () => void; onCreated: (id: number) => void }) {
  const [form] = Form.useForm()
  const [busy, setBusy] = useState(false)
  const { message } = App.useApp()
  const usable = agents.filter((a) => a.enabled)

  const submit = async () => {
    const v = await form.validateFields()
    setBusy(true)
    try {
      const t = await api<Task>('/api/tasks', { body: v })
      message.success(`任务 #${t.id} 已提交`)
      onCreated(t.id)
    } catch (e: any) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open title="发送任务" onCancel={onClose} onOk={submit} okText="发送" confirmLoading={busy} width={640}>
      <Alert type="info" showIcon style={{ marginBottom: 16 }}
             message="任务在平台托管的独立工作目录中执行（data/workspaces/task-<id>），不会访问你的其他文件。选择多个 Agent 时各自独立执行同一任务，结果在同一 Trace 中串联。" />
      <Form form={form} layout="vertical">
        <Form.Item name="agent_ids" label="执行 Agent" rules={[{ required: true, message: '至少选择一个 Agent' }]}>
          <Select mode="multiple" options={usable.map((a) => ({ value: a.id, label: `${a.name}（${a.adapter_name}${a.model ? ' · ' + a.model : ''}）` }))}
                  notFoundContent={<Typography.Text type="secondary">没有已启用的 Agent，请先到「Agent 管理」接入</Typography.Text>} />
        </Form.Item>
        <Form.Item name="title" label="标题（可选）"><Input placeholder="默认取任务内容首行" /></Form.Item>
        <Form.Item name="prompt" label="任务内容" rules={[{ required: true }]}>
          <Input.TextArea rows={8} placeholder="描述你希望 Agent 完成的任务……" />
        </Form.Item>
      </Form>
    </Modal>
  )
}
