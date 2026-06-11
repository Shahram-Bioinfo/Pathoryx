import { apiFetch } from './client'
import type { OverviewResponse } from '../types/api'

export const fetchOverview = (): Promise<OverviewResponse> =>
  apiFetch<OverviewResponse>('/overview')
