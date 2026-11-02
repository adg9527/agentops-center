import { onMounted, onUnmounted, ref, type Ref } from 'vue'
import { getToken } from './api'

export type LiveMsg = { topic: string; data: any }
type Listener = (m: LiveMsg) => void

// One shared WebSocket for the whole app; components subscribe by topic.
const listeners = new Set<Listener>()
const statusListeners = new Set<(ok: boolean) => void>()
const connected = ref(false)

let socket: WebSocket | null = null
let retry: number | undefined

function notifyStatus(ok: boolean) {
  connected.value = ok
  statusListeners.forEach((f) => f(ok))
}

function connect() {
  const token = getToken()
  if (!token || socket) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`)
  socket = ws
  ws.onopen = () => notifyStatus(true)
  ws.onmessage = (e) => {
    const msg: LiveMsg = JSON.parse(e.data)
    if (msg.topic !== 'ping') listeners.forEach((l) => l(msg))
  }
  ws.onclose = () => {
    socket = null
    notifyStatus(false)
    window.clearTimeout(retry)
    if (listeners.size) retry = window.setTimeout(connect, 2000)
  }
}

/** Subscribe to the shared realtime socket for the lifetime of the component. */
export function useLive(handler: (m: LiveMsg) => void) {
  const listener: Listener = (m) => handler(m)
  onMounted(() => {
    listeners.add(listener)
    connect()
  })
  onUnmounted(() => {
    listeners.delete(listener)
  })
}

/** Reactive connection flag, shared by every subscriber. */
export function useLiveStatus(): Ref<boolean> {
  onMounted(() => {
    connect()
  })
  return connected
}

export { connected as liveConnected }
