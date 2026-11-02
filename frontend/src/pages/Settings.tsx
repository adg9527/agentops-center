import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Form, Input, Modal, Result, Select, Switch, Table, Tabs, Typography } from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { api, type User } from '../api'
import { useAuth } from '../auth'
import { fmtTime } from '../labels'

const ROLES = [
  { value: 'admin', label: 'admin（管理员：接入/编辑 Agent、用户管理）' },
  { value: 'operator', label: 'operator（操作员：发送/终止任务、测试连接）' },
  { value: 'viewer', label: 'viewer（只读）' },
]

export default function Settings() {
  const { can } = useAuth()
  if (!can('admin')) return <Card><Result status="403" title="需要管理员权限" /></Card>
  return (
    <Card title="系统设置">
      <Tabs items={[
        { key: 'users', label: '用户与权限', children: <Users /> },
        { key: 'projects', label: '项目', children: <Projects /> },
        { key: 'audit', label: '审计日志', children: <Audit /> },
      ]} />
    </Card>
  )
}

function Users() {
  const [users, setUsers] = useState<User[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()
  const { user: me } = useAuth()
  const { message } = App.useApp()
  const load = useCallback(() => { api<User[]>('/api/users').then(setUsers) }, [])
  useEffect(load, [load])

  const patch = async (u: User, body: Partial<User>) => {
    try { await api(`/api/users/${u.id}`, { method: 'PATCH', body }); load() } catch (e: any) { message.error(e.message) }
  }

  return (
    <>
      <Button icon={<PlusOutlined />} style={{ marginBottom: 12 }} onClick={() => setOpen(true)}>新建用户</Button>
      <Table<User> size="small" rowKey="id" dataSource={users} pagination={false} columns={[
        { title: '用户名', dataIndex: 'username' },
        { title: '角色', render: (_, u) => <Select size="small" style={{ width: 120 }} value={u.role} disabled={u.id === me?.id}
                                                   options={ROLES.map((r) => ({ value: r.value, label: r.value }))} onChange={(role) => patch(u, { role })} /> },
        { title: '启用', render: (_, u) => <Switch size="small" checked={u.is_active} disabled={u.id === me?.id} onChange={(is_active) => patch(u, { is_active })} /> },
      ]} />
      <Modal open={open} title="新建用户" onCancel={() => setOpen(false)} onOk={async () => {
        const v = await form.validateFields()
        try { await api('/api/users', { body: v }); setOpen(false); form.resetFields(); load() } catch (e: any) { message.error(e.message) }
      }}>
        <Form form={form} layout="vertical" initialValues={{ role: 'operator' }}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}><Input /></Form.Item>
          <Form.Item name="password" label="初始密码" rules={[{ required: true, min: 8 }]}><Input.Password autoComplete="new-password" /></Form.Item>
          <Form.Item name="role" label="角色"><Select options={ROLES} /></Form.Item>
        </Form>
      </Modal>
    </>
  )
}

function Projects() {
  const [items, setItems] = useState<any[]>([])
  const [form] = Form.useForm()
  const { message } = App.useApp()
  const load = useCallback(() => { api('/api/projects').then(setItems) }, [])
  useEffect(load, [load])
  return (
    <>
      <Form form={form} layout="inline" style={{ marginBottom: 12 }} onFinish={async (v) => {
        try { await api('/api/projects', { body: v }); form.resetFields(); load() } catch (e: any) { message.error(e.message) }
      }}>
        <Form.Item name="name" rules={[{ required: true }]}><Input placeholder="项目名称" /></Form.Item>
        <Form.Item name="description"><Input placeholder="描述" /></Form.Item>
        <Button htmlType="submit" icon={<PlusOutlined />}>新建项目</Button>
      </Form>
      <Table size="small" rowKey="id" dataSource={items} pagination={false}
             columns={[{ title: 'ID', dataIndex: 'id', width: 60 }, { title: '名称', dataIndex: 'name' }, { title: '描述', dataIndex: 'description' }]} />
      <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>项目级隔离（独立群聊、资产、评测与报告）随对应模块在后续阶段落地。</Typography.Paragraph>
    </>
  )
}

function Audit() {
  const [data, setData] = useState<{ total: number; items: any[] }>({ total: 0, items: [] })
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')
  useEffect(() => {
    const q = new URLSearchParams({ limit: '50', offset: String((page - 1) * 50) })
    if (action) q.set('action', action)
    api(`/api/audit-logs?${q}`).then(setData)
  }, [page, action])
  return (
    <>
      <Input.Search allowClear placeholder="按操作前缀筛选，如 agent. / auth. / task." style={{ width: 320, marginBottom: 12 }}
                    onSearch={(v) => { setAction(v); setPage(1) }} />
      <Table size="small" rowKey="id" dataSource={data.items} scroll={{ x: 800 }}
             pagination={{ current: page, pageSize: 50, total: data.total, onChange: setPage, showSizeChanger: false }}
             columns={[
               { title: '时间', dataIndex: 'created_at', width: 130, render: fmtTime },
               { title: '用户', dataIndex: 'username', width: 100 },
               { title: '操作', dataIndex: 'action', width: 150 },
               { title: '对象', width: 100, render: (_, r) => r.target_type && `${r.target_type}#${r.target_id}` },
               { title: 'IP', dataIndex: 'ip', width: 110 },
               { title: '详情', dataIndex: 'detail', ellipsis: true, render: (d) => <Typography.Text style={{ fontSize: 12 }}>{JSON.stringify(d)}</Typography.Text> },
             ]} />
    </>
  )
}
