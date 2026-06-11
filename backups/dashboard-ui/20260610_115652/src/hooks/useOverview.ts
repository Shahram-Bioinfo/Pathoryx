import { useQuery } from '@tanstack/react-query'
import { fetchOverview } from '../api/overview'

export function useOverview(refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['overview'],
    queryFn: fetchOverview,
    refetchInterval,
    staleTime: 15_000,
  })
}
