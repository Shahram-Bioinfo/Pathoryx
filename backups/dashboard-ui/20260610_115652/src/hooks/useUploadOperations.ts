import { useQuery } from '@tanstack/react-query'
import {
  fetchUploadFilters,
  fetchUploadMetrics,
  fetchUploadQueue,
  type UploadQueueParams,
} from '../api/uploadTracking'

export function useUploadQueue(params: UploadQueueParams = {}) {
  return useQuery({
    queryKey: ['uploads', 'queue', params],
    queryFn:  () => fetchUploadQueue(params),
    refetchInterval: 15_000,
    staleTime:       10_000,
    placeholderData: prev => prev,
  })
}

export function useUploadMetrics() {
  return useQuery({
    queryKey: ['uploads', 'metrics'],
    queryFn:  fetchUploadMetrics,
    refetchInterval: 10_000,
    staleTime:       5_000,
  })
}

export function useUploadFilters() {
  return useQuery({
    queryKey: ['uploads', 'filters'],
    queryFn:  fetchUploadFilters,
    refetchInterval: 120_000,
    staleTime:       90_000,
  })
}
