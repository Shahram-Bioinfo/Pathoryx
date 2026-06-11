import { apiFetch } from './client'
import type {
  DbHealthResponse,
  EnvironmentConfig,
  OperationalIncidentsResponse,
  ServiceHealthExtendedResponse,
  StuckTriggersResponse,
} from '../types/api'

export const fetchOperationsHealth = (): Promise<ServiceHealthExtendedResponse> =>
  apiFetch<ServiceHealthExtendedResponse>('/operations/health')

export const fetchStuckTriggers = (
  params?: { pending_threshold_minutes?: number; running_threshold_minutes?: number }
): Promise<StuckTriggersResponse> =>
  apiFetch<StuckTriggersResponse>('/operations/stuck-triggers', {
    ...(params?.pending_threshold_minutes != null
      ? { pending_threshold_minutes: params.pending_threshold_minutes }
      : {}),
    ...(params?.running_threshold_minutes != null
      ? { running_threshold_minutes: params.running_threshold_minutes }
      : {}),
  })

export const fetchOperationalIncidents = (): Promise<OperationalIncidentsResponse> =>
  apiFetch<OperationalIncidentsResponse>('/operations/incidents')

export const fetchEnvironmentConfig = (): Promise<EnvironmentConfig> =>
  apiFetch<EnvironmentConfig>('/operations/environment')

export const fetchDbHealth = (): Promise<DbHealthResponse> =>
  apiFetch<DbHealthResponse>('/operations/db-health')
