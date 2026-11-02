const TOKEN_KEY = 'agentops_token'

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* storage unavailable */
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = {}
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, {
    method: opts.method ?? (opts.body !== undefined ? 'POST' : 'GET'),
    headers,
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    setToken(null)
    window.location.href = '/login'
  }
  const text = await res.text()
  const ctype = res.headers.get('content-type') ?? ''
  let data: any = null
  if (text) {
    if (ctype.includes('application/json') || text.startsWith('{') || text.startsWith('[')) {
      try {
        data = JSON.parse(text)
      } catch {
        data = null
      }
    }
  }
  if (!res.ok) {
    const detail = data?.detail
    const snippet = text && !data ? `：${text.replace(/\s+/g, ' ').slice(0, 120)}` : ''
    const msg =
      typeof detail === 'string'
        ? detail
        : Array.isArray(detail)
          ? detail.map((d: any) => d.msg).join('; ')
          : `HTTP ${res.status} ${res.statusText}${snippet}`
    throw new ApiError(res.status, msg)
  }
  if (text && data === null) {
    // e.g. an old backend without this route answers with the SPA shell
    throw new ApiError(res.status, `响应不是 JSON（HTTP ${res.status}）：${text.replace(/\s+/g, ' ').slice(0, 120)}`)
  }
  return data as T
}

// ---- types ----
export type Level = 'supported' | 'partial' | 'unsupported' | 'unknown'

export interface ConfigField {
  key: string
  label: string
  type: 'string' | 'text' | 'number' | 'bool' | 'string_list' | 'select' | 'json'
  required: boolean
  default: any
  secret: boolean
  help: string
  options: string[] | null
}

export interface AdapterInfo {
  type: string
  display_name: string
  platform: string
  transport: string
  verified: boolean
  description: string
  config_fields: ConfigField[]
  capabilities: Record<string, Level>
}

export interface Agent {
  id: number
  project_id: number
  name: string
  adapter_type: string
  adapter_name: string
  adapter_verified: boolean
  platform: string
  transport: string
  description: string
  role: string
  capability_tags: string[]
  config: Record<string, any>
  secret_keys_set: string[]
  model: string
  detected: Record<string, any>
  capabilities: Record<string, Level>
  enabled: boolean
  health: 'online' | 'offline' | 'unknown'
  last_seen_at: string | null
  last_check_at: string | null
  max_concurrency: number
  timeout_sec: number
  max_retries: number
  config_version: number
  risk_warnings: string[]
  created_at: string
  updated_at: string
}

export interface Run {
  id: number
  task_id: number
  agent_id: number
  agent_name?: string
  adapter_type?: string
  task_title?: string
  agent_config_version: number
  attempt: number
  status: string
  span_id: string
  external_session_id: string | null
  output_text: string | null
  error: string | null
  input_tokens: number | null
  output_tokens: number | null
  cost_usd: number | null
  model_used: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface Task {
  id: number
  project_id: number
  title: string
  prompt: string
  mode?: string
  parent_task_id?: number | null
  conversation_id?: number | null
  status: string
  trace_id: string
  workspace_dir: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  error: string | null
  runs: Run[]
  active?: boolean
}

export interface TraceEvent {
  event_id: string
  trace_id: string
  span_id: string
  parent_span_id: string | null
  task_id: number | null
  run_id: number | null
  agent_id: number | null
  event_type: string
  name: string
  timestamp: string
  status: string
  duration_ms: number | null
  input_summary: string | null
  output_summary: string | null
  metadata: Record<string, any>
}

export interface User {
  id: number
  username: string
  role: 'admin' | 'operator' | 'viewer'
  is_active: boolean
}
