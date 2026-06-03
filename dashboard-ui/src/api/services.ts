import { apiFetch } from './client'
import type { ServicesHealthResponse } from '../types/api'

export const fetchServicesHealth = (): Promise<ServicesHealthResponse> =>
  apiFetch<ServicesHealthResponse>('/services/health')
