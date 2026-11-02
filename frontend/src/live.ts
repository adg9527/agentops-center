import { useEffect, useRef, useState } from 'react'
import { getToken } from './api'

type Msg = { topic: string; data: any }
type Listener = (m: Msg) => void

// One shared WebSocket for the whole app; components subscribe by topic.
const listeners = new Set<Listener>()
let socket: WebSocket | null = null
let retry: number | undefined
const statusListeners = new Set<(ok: boolean) => void>()

function connect() {
  const token = getToken()
  if (!token || socket) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(`${proto}://${location.host}/api/ws?token=${encodeURIComponent(token)}`)
  socket = ws
  ws.onopen = () => statusListeners.forEach((f) => f(true))
  ws.onmessage = (e) => {
    const msg: Msg = JSON.parse(e.data)
    if (msg.topic !== 'ping') listeners.forEach((l) => l(msg))
  }
  ws.onclose = () => {
    socket = null
    statusListeners.forEach((f) => f(false))
    window.clearTimeout(retry)
    if (listeners.size) retry = window.setTimeout(connect, 2000)
  }
}

export function useLive(handler: (m: Msg) => void) {
  const ref = useRef(handler)
  ref.current = handler
  useEffect(() => {
    const l: Listener = (m) => ref.current(m)
    listeners.add(l)
    connect()
    return () => {
      listeners.delete(l)
    }
  }, [])
}

export function useLiveStatus() {
  const [ok, setOk] = useState(socket?.readyState === WebSocket.OPEN)
  useEffect(() => {
    statusListeners.add(setOk)
    connect()
    return () => {
      statusListeners.delete(setOk)
    }
  }, [])
  return ok
}
