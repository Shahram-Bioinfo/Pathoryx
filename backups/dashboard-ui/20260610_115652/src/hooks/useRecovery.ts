import { useQuery } from '@tanstack/react-query'
import { fetchRecovery, type RecoveryParams } from '../api/recovery'

export function useRecovery(params: RecoveryParams = {}) {
  return useQuery({
    queryKey: ['recovery', params],
    queryFn: () => fetchRecovery(params),
    staleTime: 20_000,
    placeholderData: (prev) => prev,
  })
}
