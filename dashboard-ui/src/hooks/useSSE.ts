/**
 * useSSE — Server-Sent Events hook for live dashboard telemetry.
 *
 * Connects to GET /dashboard/api/stream and listens for named events.
 * When an event arrives, the corresponding React Query caches are invalidated
 * with a short debounce so rapid successive events don't each trigger a
 * separate network round-trip.
 *
 * Connection lifecycle:
 *   The browser's EventSource API reconnects automatically when the
 *   connection drops (built-in exponential backoff starting at ~3 s).
 *   We track the connection state ('live' | 'reconnecting' | 'offline')
 *   and surface it for the UI status indicator.
 *
 *   'offline' is set after OFFLINE_TIMEOUT_MS of no server activity
 *   (events or heartbeats) — meaning the server is unreachable and
 *   EventSource's auto-retry has not yet succeeded.
 *
 * Fallback:
 *   Existing useQuery refetchInterval polling is NOT removed.
 *   SSE accelerates updates; polling provides resilience if SSE is
 *   unavailable (firewalls, HTTP/1.1 proxy buffering, etc.).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'

export type SseStatus = 'live' | 'reconnecting' | 'offline'

const SSE_URL = '/dashboard/api/stream'

/**
 * Maps each SSE event type to the React Query key prefixes that should be
 * invalidated when that event arrives.
 *
 * invalidateQueries({ queryKey: ['queues'] }) matches ALL queries whose key
 * starts with 'queues' — including ['queues', params].  This is correct
 * because we want every cached variant to refresh.
 */
const EVENT_INVALIDATIONS: Readonly<Record<string, readonly string[][]>> = {
  queue_updated:           [['queues'], ['overview'], ['operations']],
  // 'slide' prefix matches all ['slide', artifactId] keys (React Query prefix semantics)
  // 'artifactInvestigation' refreshes the Phase 9 investigation page
  file_record_updated:     [['overview'], ['slides'], ['slide'], ['artifactInvestigation']],
  pipeline_event_created:  [['events'], ['overview'], ['slide'], ['artifactInvestigation']],
  recovery_event_created:  [['recovery'], ['failures'], ['slide'], ['artifactInvestigation'], ['auditTrail']],
  // service_health_updated refreshes service health AND operations center (Phase 10)
  service_health_updated:  [['services-health'], ['operations']],
}

/** Debounce window.  Multiple events for the same key within this window
 *  collapse into a single invalidation call. */
const DEBOUNCE_MS = 350

/**
 * If no SSE activity is received for this many ms, status flips to 'offline'.
 * Avoids showing 'reconnecting' forever when the server is permanently down.
 * EventSource's own retry attempts continue independently.
 */
const OFFLINE_TIMEOUT_MS = 20_000

// ---------------------------------------------------------------------------

export function useSSE(): { status: SseStatus } {
  const qc = useQueryClient()
  const [status, setStatus] = useState<SseStatus>('reconnecting')

  // Pending per-key invalidation timers.  Keyed by JSON.stringify(queryKey).
  const pending = useRef(new Map<string, ReturnType<typeof setTimeout>>())

  // Timer that flips status → 'offline' when the server goes silent.
  const offlineTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // ── Debounced query invalidation ──────────────────────────────────────────
  const scheduleInvalidation = useCallback(
    (queryKey: string[]) => {
      const k = JSON.stringify(queryKey)
      const existing = pending.current.get(k)
      if (existing) clearTimeout(existing)

      pending.current.set(
        k,
        setTimeout(() => {
          qc.invalidateQueries({ queryKey })
          pending.current.delete(k)
        }, DEBOUNCE_MS),
      )
    },
    [qc],
  )

  // ── Activity heartbeat — resets the offline timer ─────────────────────────
  const onActivity = useCallback(() => {
    setStatus('live')
    if (offlineTimer.current) clearTimeout(offlineTimer.current)
    offlineTimer.current = setTimeout(() => setStatus('offline'), OFFLINE_TIMEOUT_MS)
  }, [])

  // ── EventSource lifecycle ─────────────────────────────────────────────────
  useEffect(() => {
    let es: EventSource | null = null
    let destroyed = false

    function connect() {
      if (destroyed) return
      setStatus('reconnecting')

      es = new EventSource(SSE_URL)

      es.onopen = () => {
        if (destroyed) { es?.close(); return }
        onActivity()
      }

      es.onerror = () => {
        if (destroyed) return
        // EventSource handles reconnection internally — we just reflect state.
        setStatus('reconnecting')
      }

      // Register a listener for each known event type.
      for (const [eventType, keys] of Object.entries(EVENT_INVALIDATIONS)) {
        es.addEventListener(eventType, () => {
          if (destroyed) return
          onActivity()
          for (const key of keys) {
            scheduleInvalidation([...key])
          }
        })
      }
    }

    connect()

    return () => {
      destroyed = true
      es?.close()
      if (offlineTimer.current) clearTimeout(offlineTimer.current)
      for (const t of pending.current.values()) clearTimeout(t)
      pending.current.clear()
    }
  }, [onActivity, scheduleInvalidation])

  return { status }
}
