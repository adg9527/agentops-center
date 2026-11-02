import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Card, Col, Empty, List, Row, Segmented, Select, Space, Statistic, Table, Tag, Tooltip, Typography } from 'antd'
import { Bar, Column } from '@ant-design/charts'
import { api, type Agent } from '../api'
import { useLive } from '../live'
import { StatusTag, fmtCost, fmtMs, fmtNum, fmtTime } from '../labels'

// Validated (dataviz validate_palette, dark, surface #141a24): blue/orange pass CVD ΔE 26.8.
const C_DONE = '#3987e5'
const C_FAIL = '#d95926'
const C_OTHER = '#5b6170'
const SURFACE = '#141a24'
const SERIES = [
  { key: 'completed', name: '成功', color: C_DONE },
  { key: 'failed', name: '失败/超时', color: C_FAIL },
  { key: 'other', name: '其他（取消/进行中）', color: C_OTHER },
] as const
// Ant Design Charts (G2 v5) dark theme with a transparent plot so the card surface shows through.
const THEME = { type: 'classicDark', view: { viewFill: 'transparent' } }
const AXIS = { x: { labelFill: '#9aa3b5', line: true, lineStroke: '#2a3243' }, y: { labelFill: '#9aa3b5', gridStroke: '#1f2735', gridLineDash: [0, 0] } }

const PHASE_ITEMS: [string, string][] = [
  ['mcp_count', 'MCP 资源'],
  ['skill_count', 'Skill 资源'],
  ['prompt_count', 'Prompt 模板'],
  ['recent_evaluations', '最近评测'],
  ['recent_reports', '最近报告'],
]

export default function Dashboard() {
  const nav = useNavigate()
  const [days, setDays] = useState(7)
  const [agentId, setAgentId] = useState<number | undefined>()
  const [projectId, setProjectId] = useState<number | undefined>()
  const [agents, setAgents] = useState<Agent[]>([])
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([])
  const [d, setD] = useState<any>(null)
  const [view, setView] = useState<'chart' | 'table'>('chart')

  const load = useCallback(() => {
    const q = new URLSearchParams({ days: String(days) })
    if (agentId) q.set('agent_id', String(agentId))
    if (projectId) q.set('project_id', String(projectId))
    api(`/api/dashboard/summary?${q}`).then(setD)
  }, [days, agentId, projectId])

  useEffect(load, [load])
  useEffect(() => {
    api<Agent[]>('/api/agents').then(setAgents)
    api('/api/projects').then(setProjects)
  }, [])
  useLive((m) => {
    if (m.topic === 'task_updated' || m.topic === 'agent_updated') load()
  })

  if (!d) return <Card loading />

  const daily = d.daily_runs as { date: string; completed: number; failed: number; other: number }[]
  const dailyLong = daily.flatMap((r) => SERIES.map((s) => ({ date: r.date, type: s.name, count: r[s.key] })))

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Space wrap>
        <Typography.Title level={4} style={{ margin: 0 }}>工作台</Typography.Title>
        <Segmented value={days} onChange={(v) => setDays(v as number)}
                   options={[{ label: '24 小时', value: 1 }, { label: '7 天', value: 7 }, { label: '30 天', value: 30 }]} />
        <Select allowClear placeholder="全部项目" style={{ width: 150 }} value={projectId} onChange={setProjectId}
                options={projects.map((p) => ({ label: p.name, value: p.id }))} />
        <Select allowClear placeholder="全部 Agent" style={{ width: 180 }} value={agentId} onChange={setAgentId}
                options={agents.map((a) => ({ label: a.name, value: a.id }))} />
      </Space>

      <Row gutter={[16, 16]}>
        {[
          ['已接入 Agent', d.agents_total, `启用 ${d.agents_enabled}`],
          ['在线 Agent', d.agents_online, '启用且最近检测/执行成功'],
          ['执行中任务', d.tasks_running, '含排队'],
          ['已完成任务', d.tasks_completed, `${d.range_days} 天内`],
          ['失败任务', d.tasks_failed, `${d.range_days} 天内`],
          ['Agent 执行成功率', d.run_success_rate == null ? '—' : `${(d.run_success_rate * 100).toFixed(1)}%`, `共 ${d.runs_total} 次执行`],
          ['平均执行耗时', fmtMs(d.avg_duration_ms), '已结束的执行'],
          ['Token (入 / 出)', `${fmtNum(d.input_tokens)} / ${fmtNum(d.output_tokens)}`, 'Agent 上报'],
          ['成本', fmtCost(d.cost_usd), `${d.cost_known_runs}/${d.runs_total} 次执行可获取成本`],
        ].map(([title, value, hint]) => (
          <Col xs={12} md={8} xl={6} xxl={4} key={title as string}>
            <Card size="small">
              <Statistic title={title} value={value as any} valueStyle={{ fontSize: 22 }} />
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{hint}</Typography.Text>
            </Card>
          </Col>
        ))}
        {PHASE_ITEMS.map(([k, label]) => (
          <Col xs={12} md={8} xl={6} xxl={4} key={k}>
            <Card size="small">
              <Statistic title={label} value="—" valueStyle={{ fontSize: 22, color: '#5b6170' }} />
              <Tooltip title="该模块尚未实现，不显示虚构数字">
                <Tag>第 {d.not_implemented[k].phase} 阶段实现</Tag>
              </Tooltip>
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card size="small" title="每日 Agent 执行"
                extra={<Segmented size="small" value={view} onChange={(v) => setView(v as any)} options={[{ label: '图表', value: 'chart' }, { label: '表格', value: 'table' }]} />}>
            {!daily.length ? <Empty description="该时间范围内暂无执行记录" /> : view === 'chart' ? (
              <Column height={260} autoFit theme={THEME} data={dailyLong} xField="date" yField="count" colorField="type" stack
                      scale={{ color: { domain: SERIES.map((s) => s.name), range: SERIES.map((s) => s.color) }, y: { nice: true } }}
                      style={{ maxWidth: 28, stroke: SURFACE, lineWidth: 2 }}
                      axis={{ ...AXIS, y: { ...AXIS.y, labelFormatter: (v: number) => (Number.isInteger(v) ? String(v) : '') } }}
                      legend={{ color: { position: 'top', itemLabelFill: '#c3c8d4' } }}
                      tooltip={{ title: 'date', items: [{ channel: 'y', name: '执行次数' }] }}
                      interaction={{ elementHighlightByColor: true }} />
            ) : (
              <Table size="small" rowKey="date" pagination={false} dataSource={daily}
                     columns={[{ title: '日期', dataIndex: 'date' }, { title: '成功', dataIndex: 'completed' }, { title: '失败/超时', dataIndex: 'failed' }, { title: '其他', dataIndex: 'other' }]} />
            )}
          </Card>
        </Col>
        <Col xs={24} xl={10}>
          <Card size="small" title="各 Agent 执行次数">
            {!d.per_agent.length ? <Empty description="暂无数据" /> : (
              <Bar height={260} autoFit theme={THEME} data={d.per_agent} xField="name" yField="total"
                   style={{ fill: C_DONE, maxWidth: 18 }} axis={AXIS}
                   label={{ text: 'total', position: 'right', fill: '#c3c8d4', dx: 4 }}
                   tooltip={{ title: 'name', items: [{ field: 'total', name: '执行次数' }, { field: 'completed', name: '成功次数' }] }} />
            )}
          </Card>
        </Col>
      </Row>

      <Card size="small" title="最近任务">
        <List dataSource={d.recent_tasks} locale={{ emptyText: '暂无任务' }}
              renderItem={(t: any) => (
                <List.Item style={{ cursor: 'pointer' }} onClick={() => nav(`/tasks/${t.id}`)}
                           extra={<Typography.Text type="secondary">{fmtTime(t.created_at)}</Typography.Text>}>
                  <Space><StatusTag status={t.status} /><span>#{t.id} {t.title}</span></Space>
                </List.Item>
              )} />
      </Card>
    </Space>
  )
}
