import { useEffect, useRef, useState } from 'react'

export type SyncSocketStatus = 'idle' | 'connecting' | 'connected' | 'disconnected'

export interface UseSyncSocketOptions {
  /** JWT access token. Supervisor pauses while null. */
  token: string | null
  /** Base WebSocket URL builder. */
  buildUrl: (token: string) => string
  /** Fires when the server pushes a sync_change or backup_restore event. */
  onEvent?: (payload: unknown) => void
  /** Fires when the supervisor (re)connects — caller should pull-drain. */
  onOpen?: () => void
  /** Fires when the supervisor loses the socket and starts backing off. */
  onDisconnect?: () => void
}

export interface SyncSocketState {
  status: SyncSocketStatus
}

const HEARTBEAT_INTERVAL_MS = 25_000
const HEARTBEAT_TIMEOUT_MS = 45_000
const BACKOFF_BASE_MS = 500
const BACKOFF_MAX_MS = 30_000

function backoffDelay(attempt: number): number {
  const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** attempt)
  const jitter = Math.floor(Math.random() * 500)
  return exp + jitter
}

/**
 * Supervised WebSocket connection with exponential-backoff reconnect,
 * application-level heartbeat, and visibility/network resumption.
 *
 * Replaces the naive ``new WebSocket`` inline usage which leaks a dead socket
 * whenever the network blips, the tab sleeps, or a proxy kills idle
 * connections — at which point mobile ↔ web sync silently stops.
 */
export function useSyncSocket({
  token,
  buildUrl,
  onEvent,
  onOpen,
  onDisconnect
}: UseSyncSocketOptions): SyncSocketState {
  const [status, setStatus] = useState<SyncSocketStatus>('idle')
  const socketRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const heartbeatIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const heartbeatTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const destroyedRef = useRef(false)
  const handlersRef = useRef({ onEvent, onOpen, onDisconnect })
  handlersRef.current = { onEvent, onOpen, onDisconnect }

  useEffect(() => {
    destroyedRef.current = false
    if (!token) {
      setStatus('idle')
      return () => {
        destroyedRef.current = true
        teardown()
      }
    }

    function teardown() {
      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current)
        heartbeatIntervalRef.current = null
      }
      if (heartbeatTimeoutRef.current) {
        clearTimeout(heartbeatTimeoutRef.current)
        heartbeatTimeoutRef.current = null
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
        reconnectTimeoutRef.current = null
      }
      const socket = socketRef.current
      socketRef.current = null
      if (socket) {
        try {
          socket.close()
        } catch (_) {
          // Socket may already be in closing state — ignore.
        }
      }
    }

    function scheduleReconnect() {
      if (destroyedRef.current) return
      handlersRef.current.onDisconnect?.()
      setStatus('disconnected')
      const delay = backoffDelay(attemptRef.current)
      attemptRef.current += 1
      reconnectTimeoutRef.current = setTimeout(() => {
        if (destroyedRef.current) return
        connect()
      }, delay)
    }

    function armHeartbeatTimeout() {
      if (heartbeatTimeoutRef.current) clearTimeout(heartbeatTimeoutRef.current)
      heartbeatTimeoutRef.current = setTimeout(() => {
        // No frames received in time — assume dead socket, force reconnect.
        const socket = socketRef.current
        socketRef.current = null
        if (socket) {
          try {
            socket.close()
          } catch (_) {
            // ignore
          }
        }
        scheduleReconnect()
      }, HEARTBEAT_TIMEOUT_MS)
    }

    function connect() {
      if (destroyedRef.current) return
      setStatus('connecting')
      let socket: WebSocket
      try {
        socket = new WebSocket(buildUrl(token!))
      } catch (_) {
        scheduleReconnect()
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        if (destroyedRef.current) return
        attemptRef.current = 0
        setStatus('connected')
        handlersRef.current.onOpen?.()
        armHeartbeatTimeout()
        heartbeatIntervalRef.current = setInterval(() => {
          if (socket.readyState !== WebSocket.OPEN) return
          try {
            socket.send(JSON.stringify({ type: 'ping' }))
          } catch (_) {
            // will trigger onclose
          }
        }, HEARTBEAT_INTERVAL_MS)
      }

      socket.onmessage = (event) => {
        armHeartbeatTimeout()
        let payload: unknown = null
        try {
          payload = JSON.parse(event.data)
        } catch (_) {
          return
        }
        if (payload && typeof payload === 'object' && (payload as any).type === 'pong') {
          return
        }
        handlersRef.current.onEvent?.(payload)
      }

      socket.onerror = () => {
        // onclose will follow; let that drive the backoff.
      }

      socket.onclose = () => {
        if (destroyedRef.current) return
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current)
          heartbeatIntervalRef.current = null
        }
        if (heartbeatTimeoutRef.current) {
          clearTimeout(heartbeatTimeoutRef.current)
          heartbeatTimeoutRef.current = null
        }
        socketRef.current = null
        scheduleReconnect()
      }
    }

    function handleOnline() {
      if (destroyedRef.current) return
      if (socketRef.current?.readyState !== WebSocket.OPEN) {
        if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current)
        attemptRef.current = 0
        connect()
      }
    }
    function handleVisibility() {
      if (typeof document === 'undefined') return
      if (document.hidden) return
      handleOnline()
      // If the socket is up, also trigger a pull to fill gaps created while tab was hidden.
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        handlersRef.current.onOpen?.()
      }
    }

    window.addEventListener('online', handleOnline)
    document.addEventListener('visibilitychange', handleVisibility)
    connect()

    return () => {
      destroyedRef.current = true
      window.removeEventListener('online', handleOnline)
      document.removeEventListener('visibilitychange', handleVisibility)
      teardown()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, buildUrl])

  return { status }
}
