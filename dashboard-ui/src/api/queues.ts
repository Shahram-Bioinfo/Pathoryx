import { apiFetch } from './client'
import type { QueueStatusResponse } from '../types/api'

export const fetchQueues = (): Promise<QueueStatusResponse> =>
  apiFetch<QueueStatusResponse>('/queues')
