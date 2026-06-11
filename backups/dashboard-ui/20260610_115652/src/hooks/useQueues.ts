import { useQuery } from '@tanstack/react-query'
import { fetchQueues } from '../api/queues'

export function useQueues(refetchInterval = 20_000) {
  return useQuery({
    queryKey: ['queues'],
    queryFn: fetchQueues,
    refetchInterval,
    staleTime: 10_000,
  })
}
