import { useQuery } from '@tanstack/react-query'
import {
  fetchDbHealth,
  fetchEnvironmentConfig,
  fetchOperationalIncidents,
  fetchOperationsHealth,
  fetchStuckTriggers,
} from '../api/operations'

export function useOperationsHealth() {
  return useQuery({
    queryKey: ['operations', 'health'],
    queryFn: fetchOperationsHealth,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export function useStuckTriggers() {
  return useQuery({
    queryKey: ['operations', 'stuck-triggers'],
    queryFn: () => fetchStuckTriggers(),
    refetchInterval: 60_000,
    staleTime: 30_000,
  })
}

export function useOperationalIncidents() {
  return useQuery({
    queryKey: ['operations', 'incidents'],
    queryFn: fetchOperationalIncidents,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export function useEnvironmentConfig() {
  return useQuery({
    queryKey: ['operations', 'environment'],
    queryFn: fetchEnvironmentConfig,
    // Environment config changes rarely — refresh every 5 minutes
    refetchInterval: 300_000,
    staleTime: 240_000,
  })
}

export function useDbHealth() {
  return useQuery({
    queryKey: ['operations', 'db-health'],
    queryFn: fetchDbHealth,
    refetchInterval: 60_000,
    staleTime: 45_000,
  })
}
