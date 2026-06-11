import { useQuery } from '@tanstack/react-query'
import {
  fetchCoreOverview,
  fetchCoreRecovery,
  fetchCoreScanners,
  fetchCoreStains,
  fetchCoreStorage,
  fetchCoreUploads,
} from '../api/computerCore'

const STALE = 30_000   // 30 s — operational data refreshes every 30s
const RETRY = 1

export function useCoreOverview() {
  return useQuery({
    queryKey: ['core', 'overview'],
    queryFn: fetchCoreOverview,
    staleTime: STALE,
    retry: RETRY,
  })
}

export function useCoreScanners() {
  return useQuery({
    queryKey: ['core', 'scanners'],
    queryFn: fetchCoreScanners,
    staleTime: STALE,
    retry: RETRY,
  })
}

export function useCoreStains() {
  return useQuery({
    queryKey: ['core', 'stains'],
    queryFn: fetchCoreStains,
    staleTime: STALE * 2,
    retry: RETRY,
  })
}

export function useCoreRecovery() {
  return useQuery({
    queryKey: ['core', 'recovery'],
    queryFn: fetchCoreRecovery,
    staleTime: STALE,
    retry: RETRY,
  })
}

export function useCoreStorage() {
  return useQuery({
    queryKey: ['core', 'storage'],
    queryFn: fetchCoreStorage,
    staleTime: STALE * 4,
    retry: RETRY,
  })
}

export function useCoreUploads() {
  return useQuery({
    queryKey: ['core', 'uploads'],
    queryFn: fetchCoreUploads,
    staleTime: STALE,
    retry: RETRY,
  })
}
