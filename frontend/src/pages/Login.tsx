import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, App, Button, Card, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { api, type User } from '../api'
import { useAuth } from '../auth'

export default function Login() {
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const { login } = useAuth()
  const nav = useNavigate()
  const { message } = App.useApp()

  useEffect(() => {
    api<{ needs_setup: boolean }>('/api/auth/setup-status').then((r) => setNeedsSetup(r.needs_setup))
  }, [])

  const submit = async (v: { username: string; password: string }) => {
    setBusy(true)
    try {
      const r = await api<{ token: string; user: User }>(needsSetup ? '/api/auth/setup' : '/api/auth/login', { body: v })
      login(r.token, r.user)
      nav('/')
    } catch (e: any) {
      message.error(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <Card style={{ width: '100%', maxWidth: 380 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>AgentOps Center</Typography.Title>
        <Typography.Paragraph type="secondary">AI Agent 统一管理、协作与自动化评测平台</Typography.Paragraph>
        {needsSetup && (
          <Alert style={{ marginBottom: 16 }} type="info" showIcon
                 message="首次启动：请创建管理员账号" description="系统没有默认口令，此账号拥有全部权限。" />
        )}
        <Form layout="vertical" onFinish={submit} disabled={needsSetup === null}>
          <Form.Item name="username" label="用户名" rules={[{ required: true, min: 2 }]}>
            <Input prefix={<UserOutlined />} autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, min: 8, message: '至少 8 位' }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete={needsSetup ? 'new-password' : 'current-password'} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={busy}>
            {needsSetup ? '创建管理员并登录' : '登录'}
          </Button>
        </Form>
      </Card>
    </div>
  )
}
