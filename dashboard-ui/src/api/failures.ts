import { apiFetch } from './client'
import type { FailuresResponse } from '../types/api'

export const fetchFailures = (limit = 100): Promise<FailuresResponse> =>
  apiFetch<FailuresResponse>('/failures', { limit })
