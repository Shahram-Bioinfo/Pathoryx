import { useQuery } from '@tanstack/react-query'
import { fetchServicesHealth } from '../api/services'

export function useServicesHealth(refetchInterval = 30_000) {
  return useQuery({
    queryKey: ['services-health'],
    queryFn: fetchServicesHealth,
    refetchInterval,
    staleTime: 15_000,
  })
}
