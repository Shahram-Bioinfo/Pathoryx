import { useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchArtifactInvestigation } from '../api/slides'

/**
 * Fetches the full investigation bundle for one artifact.
 *
 * Live updates: when the SSE stream emits file_record_updated or
 * pipeline_event_created the query is invalidated so the page refreshes
 * within ~5 s without any WebSocket infrastructure.
 *
 * The SSE hook (useSSE) runs in Shell.tsx for the whole app.  We subscribe
 * to the BroadcastChannel it uses to fanout invalidation events so this
 * hook stays decoupled.  Fallback: refetchInterval: 30_000 for resilience.
 */
export function useArtifactInvestigation(
  globalArtifactId: string | undefined,
  eventsLimit = 100,
) {
  const queryClient = useQueryClient()
  const key = ['artifactInvestigation', globalArtifactId] as const

  const query = useQuery({
    queryKey: key,
    queryFn: () => fetchArtifactInvestigation(globalArtifactId!, eventsLimit),
    enabled: !!globalArtifactId,
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  // When any relevant SSE event fires, invalidate this specific query.
  // We listen on the same EventSource path the useSSE hook uses.
  useEffect(() => {
    if (!globalArtifactId) return

    const RELEVANT = ['file_record_updated', 'pipeline_event_created', 'recovery_event_created']
    const es = new EventSource('/dashboard/api/stream')

    for (const evType of RELEVANT) {
      es.addEventListener(evType, () => {
        queryClient.invalidateQueries({ queryKey: key })
      })
    }

    return () => { es.close() }
  }, [globalArtifactId, queryClient])  // eslint-disable-line react-hooks/exhaustive-deps

  return query
}
