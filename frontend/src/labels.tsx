import { Tag, Tooltip } from 'antd'
import dayjs from 'dayjs'
import type { Level } from './api'

export const TASK_STATUS: Record<string, { text: string; color: string }> = {
  pending: { text: '待执行', color: 'default' },
  running: { text: '执行中', color: 'processing' },
  waiting_approval: { text: '等待审批', color: 'gold' },
  waiting_handoff: { text: '等待交接', color: 'purple' },
  paused: { text: '已暂停', color: 'warning' },
  completed: { text: '已完成', color: 'success' },
  failed: { text: '失败', color: 'error' },
  cancelled: { text: '已取消', color: 'default' },
  timeout: { text: '超时', color: 'error' },
}

export function StatusTag({ status }: { status: string }) {
  const s = TASK_STATUS[status] ?? { text: status, color: 'default' }
  return <Tag color={s.color}>{s.text}</Tag>
}

export const HEALTH: Record<string, { text: string; color: string }> = {
  online: { text: '在线', color: 'success' },
  offline: { text: '离线', color: 'error' },
  unknown: { text: '未检测', color: 'default' },
}

export const CAPABILITY_LABELS: Record<string, string> = {
  connection_check: '连接检测',
  send_task: '任务发送',
  stream_events: '流式事件',
  tool_events: '工具调用事件',
  mcp_events: 'MCP 调用识别',
  token_usage: 'Token 统计',
  cost: '成本统计',
  cancel: '任务取消',
  permission_control: '权限控制',
  session_resume: '会话续接',
  model_select: '模型选择',
  mcp_config: 'MCP 配置注入',
}

const LEVEL: Record<Level, { text: string; color: string }> = {
  supported: { text: '支持', color: 'success' },
  partial: { text: '部分支持', color: 'warning' },
  unsupported: { text: '不支持', color: 'default' },
  unknown: { text: '未验证', color: 'purple' },
}

export function LevelTag({ level }: { level: Level }) {
  const l = LEVEL[level] ?? LEVEL.unknown
  return <Tag color={l.color}>{l.text}</Tag>
}

export const EVENT_TYPES: Record<string, { text: string; color: string }> = {
  'task.started': { text: '任务开始', color: 'blue' },
  'task.completed': { text: '任务完成', color: 'green' },
  'task.failed': { text: '任务失败', color: 'red' },
  'task.cancelled': { text: '任务取消', color: 'default' },
  'agent.run.started': { text: 'Agent 收到任务', color: 'cyan' },
  'agent.run.completed': { text: 'Agent 提交结果', color: 'green' },
  'agent.run.failed': { text: 'Agent 失败', color: 'red' },
  'agent.run.timeout': { text: 'Agent 超时', color: 'red' },
  'agent.run.cancelled': { text: 'Agent 取消', color: 'default' },
  'agent.run.retry': { text: '重试', color: 'orange' },
  'agent.message': { text: 'Agent 消息', color: 'geekblue' },
  'agent.error': { text: 'Agent 错误', color: 'red' },
  'session.started': { text: '会话建立', color: 'purple' },
  'model.call': { text: '模型调用', color: 'magenta' },
  'tool.call': { text: '工具调用', color: 'gold' },
  'tool.result': { text: '工具结果', color: 'lime' },
  'permission.denied': { text: '权限拒绝', color: 'volcano' },
  'file.change': { text: '文件变更', color: 'orange' },
  'handoff.created': { text: '任务交接', color: 'purple' },
  log: { text: '日志', color: 'default' },
}

export function EventTag({ type }: { type: string }) {
  const e = EVENT_TYPES[type] ?? { text: type, color: 'default' }
  return (
    <Tooltip title={type}>
      <Tag color={e.color}>{e.text}</Tag>
    </Tooltip>
  )
}

export const fmtTime = (s?: string | null) => (s ? dayjs(s).format('MM-DD HH:mm:ss') : '—')
export const fmtMs = (ms?: number | null) =>
  ms == null ? '—' : ms < 1000 ? `${ms} ms` : ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${(ms / 60000).toFixed(1)} min`
export const fmtNum = (n?: number | null) => (n == null ? '—' : n.toLocaleString())
export const fmtCost = (c?: number | null) => (c == null ? '—' : `$${c.toFixed(4)}`)
