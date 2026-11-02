import { Navigate, Route, Routes } from 'react-router-dom'
import { Spin } from 'antd'
import { AuthProvider, useAuth } from './auth'
import AppLayout from './components/AppLayout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Agents from './pages/Agents'
import Tasks from './pages/Tasks'
import TaskDetail from './pages/TaskDetail'
import Monitor from './pages/Monitor'
import Settings from './pages/Settings'
import Placeholder from './pages/Placeholder'
import Chat from './pages/Chat'

function Guarded() {
  const { user, loading } = useAuth()
  if (loading) return <Spin style={{ marginTop: '40vh', width: '100%' }} />
  if (!user) return <Navigate to="/login" replace />
  return (
    <AppLayout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/tasks" element={<Tasks />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
        <Route path="/monitor" element={<Monitor />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/chat" element={<Chat />} />
        <Route path="/mcp" element={<Placeholder phase={4} title="MCP 管理" items={['MCP Server 增删改', '连接测试与 tools/list 工具清单', '启停（通过运行时 --mcp-config 真正生效）', '调用记录与关联 Agent', '版本与评测记录']} />} />
        <Route path="/skills" element={<Placeholder phase={4} title="Skill 管理" items={['多文件 Skill 导入/编辑', 'Skill Adapter 格式转换', '版本 / 对比 / 回滚', '使用记录与评测']} />} />
        <Route path="/prompts" element={<Placeholder phase={4} title="Prompt 管理" items={['模板变量', '版本 / 发布 / 回滚 / 对比', '测试与评测追溯到准确版本']} />} />
        <Route path="/evals" element={<Placeholder phase={5} title="自动化评测" items={['数据集 (CSV/JSON 导入、版本)', '10 类评分器（确定性优先）', '多 Agent 横向 / 版本纵向对比', '评测可视化']} />} />
        <Route path="/reports" element={<Placeholder phase={6} title="评测报告" items={['自动生成报告（历史快照）', 'PDF / Excel / JSON 导出', '报告版本、对比、检索与归档']} />} />
        <Route path="/versions" element={<Placeholder phase={4} title="版本管理" items={['MCP / Skill / Agent / Prompt 统一版本体系', '版本快照、对比、发布、回滚、启停']} />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppLayout>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<Guarded />} />
      </Routes>
    </AuthProvider>
  )
}
