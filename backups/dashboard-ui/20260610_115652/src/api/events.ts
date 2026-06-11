import { apiFetch } from './client'
import type { EventListResponse } from '../types/api'

export const fetchRecentEvents = (limit = 100): Promise<EventListResponse> =>
  apiFetch<EventListResponse>('/events/recent', { limit })
