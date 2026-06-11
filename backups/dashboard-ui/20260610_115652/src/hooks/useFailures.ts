import { useQuery } from '@tanstack/react-query'
import { fetchFailures } from '../api/failures'

export function useFailures(limit = 100) {
  return useQuery({
    queryKey: ['failures', limit],
    queryFn: () => fetchFailures(limit),
    staleTime: 20_000,
  })
}
