import { apiFetch } from './client'
import type { RecoveryResponse } from '../types/api'

export interface RecoveryParams {
  review_status?: string
  limit?: number
}

export const fetchRecovery = (params: RecoveryParams = {}): Promise<RecoveryResponse> =>
  apiFetch<RecoveryResponse>('/recovery', {
    limit: params.limit ?? 100,
    ...(params.review_status ? { review_status: params.review_status } : {}),
  })
