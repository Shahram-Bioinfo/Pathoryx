import { useQuery } from '@tanstack/react-query'
import { fetchRecentEvents } from '../api/events'

export function useRecentEvents(limit = 100, refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['events', limit],
    queryFn: () => fetchRecentEvents(limit),
    refetchInterval,
    staleTime: 15_000,
  })
}
