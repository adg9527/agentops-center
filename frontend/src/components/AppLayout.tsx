import { useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Badge, Button, Dropdown, Layout, Menu, Space, Tag, Typography } from 'antd'
import {
  ApiOutlined, AppstoreOutlined, BarChartOutlined, BranchesOutlined, DashboardOutlined, ExperimentOutlined,
  FileTextOutlined, MessageOutlined, MonitorOutlined, ProfileOutlined, RobotOutlined, SettingOutlined,
  ThunderboltOutlined, UserOutlined,
} from '@ant-design/icons'
import { useAuth } from '../auth'
import { useLiveStatus } from '../live'

const { Sider, Header, Content } = Layout

const soon = (phase: number) => (
  <Tag style={{ marginInlineStart: 6, fontSize: 10, lineHeight: '16px', padding: '0 4px' }}>P{phase}</Tag>
)

const ITEMS = [
  { key: '/', icon: <DashboardOutlined />, label: '工作台' },
  { key: '/chat', icon: <MessageOutlined />, label: 'Agent 群聊' },
  { key: '/agents', icon: <RobotOutlined />, label: 'Agent 管理' },
  { key: '/mcp', icon: <ApiOutlined />, label: <>MCP 管理{soon(4)}</> },
  { key: '/skills', icon: <AppstoreOutlined />, label: <>Skill 管理{soon(4)}</> },
  { key: '/prompts', icon: <ProfileOutlined />, label: <>Prompt 管理{soon(4)}</> },
  { key: '/tasks', icon: <ThunderboltOutlined />, label: '任务中心' },
  { key: '/monitor', icon: <MonitorOutlined />, label: '执行监控' },
  { key: '/evals', icon: <ExperimentOutlined />, label: <>自动化评测{soon(5)}</> },
  { key: '/reports', icon: <FileTextOutlined />, label: <>评测报告{soon(6)}</> },
  { key: '/versions', icon: <BranchesOutlined />, label: <>版本管理{soon(4)}</> },
  { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
]

export default function AppLayout({ children }: { children: ReactNode }) {
  const nav = useNavigate()
  const loc = useLocation()
  const { user, logout } = useAuth()
  const live = useLiveStatus()
  const [collapsed, setCollapsed] = useState(false)
  const selected = ITEMS.map((i) => i.key).filter((k) => (k === '/' ? loc.pathname === '/' : loc.pathname.startsWith(k)))

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={208} breakpoint="lg"
             style={{ borderRight: '1px solid #1c2433' }}>
        <div style={{ height: 56, display: 'flex', alignItems: 'center', gap: 8, padding: '0 20px', color: '#e6ebf5' }}>
          <BarChartOutlined style={{ fontSize: 20, color: '#4f7cff' }} />
          {!collapsed && <b style={{ letterSpacing: 0.5 }}>AgentOps Center</b>}
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={selected} items={ITEMS} onClick={(e) => nav(e.key)} />
      </Sider>
      <Layout>
        <Header style={{ height: 56, padding: '0 20px', display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                         borderBottom: '1px solid #1c2433' }}>
          <Space size="large">
            <Badge status={live ? 'success' : 'error'} text={<Typography.Text type="secondary">{live ? '实时连接' : '实时连接断开'}</Typography.Text>} />
            <Dropdown menu={{ items: [{ key: 'logout', label: '退出登录', onClick: () => { logout(); nav('/login') } }] }}>
              <Button type="text" icon={<UserOutlined />}>
                {user?.username} <Tag style={{ marginLeft: 6 }}>{user?.role}</Tag>
              </Button>
            </Dropdown>
          </Space>
        </Header>
        <Content style={{ padding: 20, minWidth: 0 }}>{children}</Content>
      </Layout>
    </Layout>
  )
}
