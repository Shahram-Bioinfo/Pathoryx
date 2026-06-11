import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createRoutingOverride,
  deleteRoutingOverride,
  fetchDecisionChain,
  fetchRoutingDecisions,
  fetchRoutingOverrides,
  fetchRoutingPreview,
  fetchRoutingStatus,
} from '../api/routing'
import type { CreateOverrideRequest } from '../types/api'

export function useRoutingStatus() {
  return useQuery({
    queryKey: ['routing', 'status'],
    queryFn: fetchRoutingStatus,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export function useRoutingPreview(limit = 100) {
  return useQuery({
    queryKey: ['routing', 'preview', limit],
    queryFn: () => fetchRoutingPreview(limit),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export function useRoutingOverrides() {
  return useQuery({
    queryKey: ['routing', 'overrides'],
    queryFn: fetchRoutingOverrides,
    refetchInterval: 15_000,
    staleTime: 10_000,
  })
}

export function useRoutingDecisions(limit = 100) {
  return useQuery({
    queryKey: ['routing', 'decisions', limit],
    queryFn: () => fetchRoutingDecisions(limit),
    refetchInterval: 30_000,
    staleTime: 20_000,
  })
}

export function useDecisionChain(decisionId: number | null) {
  return useQuery({
    queryKey: ['routing', 'chain', decisionId],
    queryFn: () => fetchDecisionChain(decisionId!),
    enabled: decisionId !== null,
    staleTime: 60_000,
  })
}

export function useCreateOverride() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateOverrideRequest) => createRoutingOverride(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing', 'overrides'] })
      qc.invalidateQueries({ queryKey: ['routing', 'preview'] })
    },
  })
}

export function useDeleteOverride() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => deleteRoutingOverride(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing', 'overrides'] })
      qc.invalidateQueries({ queryKey: ['routing', 'preview'] })
    },
  })
}
