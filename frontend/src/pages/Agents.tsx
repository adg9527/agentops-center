import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, App, AutoComplete, Badge, Button, Card, Descriptions, Drawer, Empty, Form, Input, InputNumber, Popconfirm, Select, Space,
  Switch, Table, Tabs, Tag, Tooltip, Typography,
} from 'antd'
import { ApiOutlined, PlusOutlined, ReloadOutlined, WarningOutlined } from '@ant-design/icons'
import { api, type AdapterInfo, type Agent, type ConfigField, type Run } from '../api'
import { useAuth } from '../auth'
import { useLive } from '../live'
import { CAPABILITY_LABELS, HEALTH, LevelTag, StatusTag, fmtCost, fmtMs, fmtNum, fmtTime } from '../labels'

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [adapters, setAdapters] = useState<AdapterInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Agent | 'new' | null>(null)
  const [detail, setDetail] = useState<Agent | null>(null)
  const [testing, setTesting] = useState<number | null>(null)
  const { can } = useAuth()
  const { message, modal } = App.useApp()

  const load = useCallback(() => {
    api<Agent[]>('/api/agents').then((a) => {
      setAgents(a)
      setDetail((d) => (d ? a.find((x) => x.id === d.id) ?? null : null))
      setLoading(false)
    })
  }, [])
  useEffect(() => {
    load()
    api<AdapterInfo[]>('/api/adapters').then(setAdapters)
  }, [load])
  useLive((m) => m.topic === 'agent_updated' && load())

  const test = async (a: Agent) => {
    setTesting(a.id)
    try {
      const r = await api(`/api/agents/${a.id}/test`, { method: 'POST' })
      modal[r.ok ? 'success' : 'error']({
        title: r.ok ? `${a.name} 连接正常` : `${a.name} 连接失败`,
        content: (
          <Descriptions column={1} size="small">
            <Descriptions.Item label="结果">{r.detail}</Descriptions.Item>
            <Descriptions.Item label="版本">{r.version ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="耗时">{fmtMs(r.latency_ms)}</Descriptions.Item>
            {r.detected?.models && <Descriptions.Item label="可用模型">{r.detected.models.length} 个</Descriptions.Item>}
          </Descriptions>
        ),
      })
      load()
    } catch (e: any) {
      message.error(e.message)
    } finally {
      setTesting(null)
    }
  }

  const toggle = async (a: Agent, on: boolean) => {
    try {
      await api(`/api/agents/${a.id}/${on ? 'enable' : 'disable'}`, { method: 'POST' })
      message.success(on ? '已启用' : '已停用：新任务将不会分配给该 Agent')
      load()
    } catch (e: any) {
      message.error(e.message)
    }
  }

  const remove = async (a: Agent) => {
    try {
      await api(`/api/agents/${a.id}`, { method: 'DELETE' })
      message.success('已删除（执行历史保留）')
      setDetail(null)
      load()
    } catch (e: any) {
      message.error(e.message)
    }
  }

  return (
    <Card
      title="Agent 接入管理"
      extra={
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load} />
          {can('admin') && <Button type="primary" icon={<PlusOutlined />} onClick={() => setEditing('new')}>接入 Agent</Button>}
        </Space>
      }
    >
      <Table<Agent>
        rowKey="id" loading={loading} dataSource={agents} scroll={{ x: 1000 }}
        locale={{ emptyText: <Empty description="尚未接入 Agent。点击右上角「接入 Agent」，可接入本机 Claude Code、OpenCode 等。" /> }}
        onRow={(a) => ({ onClick: () => setDetail(a), style: { cursor: 'pointer' } })}
        columns={[
          { title: '名称', dataIndex: 'name', render: (v, a) => (
            <Space direction="vertical" size={0}>
              <b>{v}</b>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>{a.role || a.description?.slice(0, 40) || '—'}</Typography.Text>
            </Space>) },
          { title: '平台 / 接入方式', render: (_, a) => (
            <Space size={4} wrap>
              <Tag>{a.adapter_name}</Tag><Tag color="geekblue">{a.transport.toUpperCase()}</Tag>
              {!a.adapter_verified && <Tooltip title="该 Adapter 未在本环境经过端到端验证"><Tag color="purple">未验证</Tag></Tooltip>}
            </Space>) },
          { title: '模型', dataIndex: 'model', render: (v, a) => v || a.detected?.runtime?.model || <Typography.Text type="secondary">Agent 默认</Typography.Text> },
          { title: '状态', render: (_, a) => (
            <Space direction="vertical" size={0}>
              <Badge status={a.enabled ? (HEALTH[a.health].color as any) : 'default'} text={a.enabled ? HEALTH[a.health].text : '已停用'} />
              {a.risk_warnings.length > 0 && <Tooltip title={a.risk_warnings.join('；')}><Tag color="warning" icon={<WarningOutlined />}>高风险配置</Tag></Tooltip>}
            </Space>) },
          { title: '版本', render: (_, a) => <>{a.detected?.version ?? '—'}<br /><Typography.Text type="secondary" style={{ fontSize: 12 }}>配置 v{a.config_version}</Typography.Text></> },
          { title: '最后在线', dataIndex: 'last_seen_at', render: fmtTime },
          { title: '操作', fixed: 'right', width: 230, render: (_, a) => (
            <Space onClick={(e) => e.stopPropagation()}>
              <Button size="small" icon={<ApiOutlined />} loading={testing === a.id} onClick={() => test(a)} disabled={!can('operator')}>测试连接</Button>
              {can('admin') && <Switch size="small" checked={a.enabled} onChange={(on) => toggle(a, on)} checkedChildren="启用" unCheckedChildren="停用" />}
              {can('admin') && <Button size="small" type="link" onClick={() => setEditing(a)}>编辑</Button>}
            </Space>) },
        ]}
      />
      {editing && <AgentForm adapters={adapters} agent={editing === 'new' ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load() }} />}
      {detail && <AgentDetail agent={detail} adapters={adapters} onClose={() => setDetail(null)} onTest={() => test(detail)}
                              onEdit={() => setEditing(detail)} onDelete={() => remove(detail)} />}
    </Card>
  )
}

function FieldInput({ f, secretSet }: { f: ConfigField; secretSet: boolean }) {
  if (f.secret) return <Input.Password placeholder={secretSet ? '已设置（留空保持不变）' : '未设置'} autoComplete="new-password" />
  switch (f.type) {
    case 'text': return <Input.TextArea rows={3} />
    case 'number': return <InputNumber style={{ width: '100%' }} />
    case 'string_list': return <Select mode="tags" tokenSeparators={[',', ' ']} />
    case 'select': return <Select options={(f.options ?? []).map((o) => ({ label: o, value: o }))} />
    case 'json': return <Input.TextArea rows={3} style={{ fontFamily: 'monospace' }} />
    default: return <Input />
  }
}

function AgentForm({ adapters, agent, onClose, onSaved }: { adapters: AdapterInfo[]; agent: Agent | null; onClose: () => void; onSaved: () => void }) {
  const [form] = Form.useForm()
  const [type, setType] = useState(agent?.adapter_type ?? adapters.find((a) => a.verified)?.type)
  const [saving, setSaving] = useState(false)
  const [clearSecrets, setClearSecrets] = useState<string[]>([])
  const [models, setModels] = useState<string[]>([])
  useEffect(() => {
    setModels([])
    if (type) api<{ models: string[] }>(`/api/adapters/${type}/models`).then((r) => setModels(r.models)).catch(() => {})
  }, [type])
  const adapter = adapters.find((a) => a.type === type)
  const { message } = App.useApp()

  const initial = useMemo(() => {
    const cfg: Record<string, any> = {}
    for (const f of adapter?.config_fields ?? []) {
      if (f.secret) continue
      const v = agent?.adapter_type === type ? agent?.config[f.key] ?? f.default : f.default
      cfg[f.key] = f.type === 'json' ? JSON.stringify(v ?? {}, null, 0) : v
    }
    return {
      name: agent?.name ?? adapter?.display_name, description: agent?.description ?? '', role: agent?.role ?? '',
      capability_tags: agent?.capability_tags ?? [], model: agent?.model ?? '', max_concurrency: agent?.max_concurrency ?? 1,
      timeout_sec: agent?.timeout_sec ?? 600, max_retries: agent?.max_retries ?? 0, config: cfg,
    }
  }, [adapter, agent, type])

  useEffect(() => form.setFieldsValue(initial), [form, initial])

  const save = async () => {
    const v = await form.validateFields()
    const config: Record<string, any> = {}
    const secrets: Record<string, string | null> = {}
    for (const f of adapter!.config_fields) {
      const raw = v.config?.[f.key]
      if (f.secret) {
        if (raw) secrets[f.key] = raw
        else if (clearSecrets.includes(f.key)) secrets[f.key] = null
        continue
      }
      if (f.type === 'json') {
        try { config[f.key] = raw ? JSON.parse(raw) : {} } catch { message.error(`${f.label} 不是合法 JSON`); return }
      } else config[f.key] = raw
    }
    const body = { ...v, config, secrets }
    setSaving(true)
    try {
      if (agent) await api(`/api/agents/${agent.id}`, { method: 'PATCH', body })
      else await api('/api/agents', { body: { ...body, adapter_type: type } })
      message.success('已保存')
      onSaved()
    } catch (e: any) {
      message.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer open width={560} title={agent ? `编辑 ${agent.name}` : '接入 Agent'} onClose={onClose}
            extra={<Button type="primary" loading={saving} onClick={save}>保存</Button>}>
      {!agent && (
        <Form.Item label="接入类型" required>
          <Select value={type} onChange={setType}
                  options={adapters.map((a) => ({ value: a.type, label: <Space>{a.display_name}<Tag>{a.transport.toUpperCase()}</Tag>{!a.verified && <Tag color="purple">未验证</Tag>}</Space> }))} />
        </Form.Item>
      )}
      {adapter && <Alert style={{ marginBottom: 16 }} type={adapter.verified ? 'info' : 'warning'} showIcon message={adapter.platform} description={adapter.description} />}
      <Form form={form} layout="vertical" initialValues={initial}>
        <Form.Item name="name" label="名称" rules={[{ required: true }]}><Input /></Form.Item>
        <Form.Item name="role" label="角色"><Input placeholder="如：代码实现 / 代码审查 / 需求分析" /></Form.Item>
        <Form.Item name="description" label="能力描述"><Input.TextArea rows={2} /></Form.Item>
        <Form.Item name="capability_tags" label="能力标签"><Select mode="tags" /></Form.Item>
        <Form.Item name="model" label="模型" extra={type === 'opencode' ? `必须是「provider/模型名」格式，如 opencode/big-pickle（本机检测到 ${models.length} 个可选模型）` : '留空使用 Agent 默认模型。Claude Code 例如 haiku / sonnet'}>
          <AutoComplete allowClear filterOption={(input, o) => String(o?.value).toLowerCase().includes(input.toLowerCase())}
                        options={(models.length ? models : agent?.detected?.check?.models ?? []).map((m: string) => ({ value: m }))} />
        </Form.Item>
        <Typography.Title level={5}>连接与启动配置</Typography.Title>
        {adapter?.config_fields.map((f) => (
          <Form.Item key={f.key} name={['config', f.key]} label={<Space>{f.label}{f.secret && <Tag color="gold">加密存储</Tag>}</Space>}
                     extra={<>{f.help}{f.secret && agent?.secret_keys_set.includes(f.key) && (
                       <> · <a onClick={() => { setClearSecrets((s) => [...s, f.key]); message.info('保存后将清除该密钥') }}>清除已设置的值</a></>)}</>}
                     valuePropName={f.type === 'bool' ? 'checked' : 'value'} rules={[{ required: f.required && !f.secret }]}>
            {f.type === 'bool' ? <Switch /> : <FieldInput f={f} secretSet={!!agent?.secret_keys_set.includes(f.key)} />}
          </Form.Item>
        ))}
        <Typography.Title level={5}>执行控制</Typography.Title>
        <Space wrap>
          <Form.Item name="max_concurrency" label="最大并发"><InputNumber min={1} max={16} /></Form.Item>
          <Form.Item name="timeout_sec" label="超时（秒）"><InputNumber min={10} max={86400} /></Form.Item>
          <Form.Item name="max_retries" label="失败重试次数"><InputNumber min={0} max={5} /></Form.Item>
        </Space>
      </Form>
    </Drawer>
  )
}

function AgentDetail({ agent, adapters, onClose, onTest, onEdit, onDelete }: {
  agent: Agent; adapters: AdapterInfo[]; onClose: () => void; onTest: () => void; onEdit: () => void; onDelete: () => void
}) {
  const [runs, setRuns] = useState<Run[]>([])
  const [stats, setStats] = useState<any>(null)
  const { can } = useAuth()
  const nav = useNavigate()
  const adapter = adapters.find((a) => a.type === agent.adapter_type)

  useEffect(() => {
    api<Run[]>(`/api/agents/${agent.id}/runs`).then(setRuns)
    api(`/api/agents/${agent.id}/stats`).then(setStats)
  }, [agent.id, agent.updated_at])

  const rt = agent.detected?.runtime
  return (
    <Drawer open width={760} title={agent.name} onClose={onClose}
            extra={<Space>
              <Button onClick={onTest} disabled={!can('operator')}>测试连接</Button>
              {can('admin') && <Button onClick={onEdit}>编辑</Button>}
              {can('admin') && <Popconfirm title="删除该 Agent？" description="执行历史与 Trace 会保留。" onConfirm={onDelete}><Button danger>删除</Button></Popconfirm>}
            </Space>}>
      {agent.risk_warnings.length > 0 && <Alert type="warning" showIcon style={{ marginBottom: 12 }} message="高风险配置" description={agent.risk_warnings.join('；')} />}
      <Tabs items={[
        { key: 'info', label: '概览', children: (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="接入类型">{agent.adapter_name}</Descriptions.Item>
            <Descriptions.Item label="接入方式">{agent.transport.toUpperCase()}</Descriptions.Item>
            <Descriptions.Item label="来源平台">{agent.platform}</Descriptions.Item>
            <Descriptions.Item label="Adapter 验证">{agent.adapter_verified ? <Tag color="success">本环境已验证</Tag> : <Tag color="purple">未验证</Tag>}</Descriptions.Item>
            <Descriptions.Item label="状态">{agent.enabled ? HEALTH[agent.health].text : '已停用'}</Descriptions.Item>
            <Descriptions.Item label="Agent 版本">{agent.detected?.version ?? '未检测'}</Descriptions.Item>
            <Descriptions.Item label="配置模型">{agent.model || 'Agent 默认'}</Descriptions.Item>
            <Descriptions.Item label="实际模型">{rt?.model ?? '首次执行后检测'}</Descriptions.Item>
            <Descriptions.Item label="配置版本">v{agent.config_version}</Descriptions.Item>
            <Descriptions.Item label="并发 / 超时 / 重试">{agent.max_concurrency} / {agent.timeout_sec}s / {agent.max_retries}</Descriptions.Item>
            <Descriptions.Item label="最后在线">{fmtTime(agent.last_seen_at)}</Descriptions.Item>
            <Descriptions.Item label="最后检测">{fmtTime(agent.last_check_at)}</Descriptions.Item>
            <Descriptions.Item label="已设置密钥" span={2}>{agent.secret_keys_set.length ? agent.secret_keys_set.map((k) => <Tag key={k}>{k}: ******</Tag>) : '无'}</Descriptions.Item>
            <Descriptions.Item label="实测可用工具" span={2}>
              {rt?.tools ? rt.tools.map((t: string) => <Tag key={t}>{t}</Tag>) : <Typography.Text type="secondary">{agent.adapter_type === 'claude_code' ? '首次执行任务后由 init 事件检测' : '该平台不上报工具清单'}</Typography.Text>}
            </Descriptions.Item>
            <Descriptions.Item label="实测 MCP" span={2}>
              {rt?.mcp_servers ? (rt.mcp_servers.length ? rt.mcp_servers.map((m: any) => <Tag key={m.name}>{m.name} ({m.status})</Tag>) : '无（已隔离）') : <Typography.Text type="secondary">不支持或未检测</Typography.Text>}
            </Descriptions.Item>
            <Descriptions.Item label="配置" span={2}><pre style={{ margin: 0, whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(agent.config, null, 2)}</pre></Descriptions.Item>
          </Descriptions>) },
        { key: 'caps', label: '能力矩阵', children: (
          <Table size="small" pagination={false} rowKey="k"
                 dataSource={Object.entries(adapter?.capabilities ?? agent.capabilities).map(([k, v]) => ({ k, v }))}
                 columns={[{ title: '能力', dataIndex: 'k', render: (k) => CAPABILITY_LABELS[k] ?? k }, { title: '支持程度', dataIndex: 'v', render: (v) => <LevelTag level={v} /> }]} />) },
        { key: 'runs', label: `执行历史 (${runs.length})`, children: (
          <Table<Run> size="small" rowKey="id" dataSource={runs} pagination={{ pageSize: 10 }}
                      onRow={(r) => ({ onClick: () => nav(`/tasks/${r.task_id}`), style: { cursor: 'pointer' } })}
                      columns={[
                        { title: '任务', render: (_, r) => `#${r.task_id} ${r.task_title}` },
                        { title: '状态', dataIndex: 'status', render: (s) => <StatusTag status={s} /> },
                        { title: '耗时', dataIndex: 'duration_ms', render: fmtMs },
                        { title: 'Token', render: (_, r) => `${fmtNum(r.input_tokens)} / ${fmtNum(r.output_tokens)}` },
                        { title: '成本', dataIndex: 'cost_usd', render: fmtCost },
                        { title: '时间', dataIndex: 'created_at', render: fmtTime },
                      ]} />) },
        { key: 'stats', label: '调用统计', children: stats && (
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="统计范围">近 {stats.days} 天</Descriptions.Item>
            <Descriptions.Item label="执行次数">{stats.total}</Descriptions.Item>
            <Descriptions.Item label="成功率">{stats.success_rate == null ? '—' : `${(stats.success_rate * 100).toFixed(1)}%`}</Descriptions.Item>
            <Descriptions.Item label="平均耗时">{fmtMs(stats.avg_duration_ms)}</Descriptions.Item>
            <Descriptions.Item label="Token 入 / 出">{fmtNum(stats.input_tokens)} / {fmtNum(stats.output_tokens)}</Descriptions.Item>
            <Descriptions.Item label="成本">{fmtCost(stats.cost_usd)}（{stats.cost_known_runs}/{stats.total} 次可获取）</Descriptions.Item>
            <Descriptions.Item label="状态分布" span={2}>{Object.entries(stats.by_status).map(([k, v]) => <span key={k} style={{ marginRight: 8 }}><StatusTag status={k} />{v as number}</span>)}</Descriptions.Item>
          </Descriptions>) },
        { key: 'evals', label: '评测历史', children: <Empty description="自动化评测在第 5 阶段实现" /> },
      ]} />
    </Drawer>
  )
}
