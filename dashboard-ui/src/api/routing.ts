import { apiFetch, ApiError } from './client'
import type {
  CreateOverrideRequest,
  DecisionChainResponse,
  RoutingDecisionsResponse,
  RoutingOverrideItem,
  RoutingOverridesResponse,
  RoutingPreviewResponse,
  RoutingStatusResponse,
} from '../types/api'

const BASE = import.meta.env.VITE_API_BASE_URL
  ? `${import.meta.env.VITE_API_BASE_URL}/dashboard/api`
  : '/dashboard/api'

export const fetchRoutingStatus = (): Promise<RoutingStatusResponse> =>
  apiFetch<RoutingStatusResponse>('/routing/status')

export const fetchRoutingPreview = (limit = 100): Promise<RoutingPreviewResponse> =>
  apiFetch<RoutingPreviewResponse>('/routing/preview', { limit })

export const fetchRoutingOverrides = (): Promise<RoutingOverridesResponse> =>
  apiFetch<RoutingOverridesResponse>('/routing/overrides')

export const fetchRoutingDecisions = (limit = 100): Promise<RoutingDecisionsResponse> =>
  apiFetch<RoutingDecisionsResponse>('/routing/decisions', { limit })

export async function createRoutingOverride(
  body: CreateOverrideRequest
): Promise<RoutingOverrideItem> {
  const url = `${BASE}/routing/override`
  const res = await fetch(url.startsWith('http') ? url : new URL(url, window.location.origin).toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
  }
  return res.json() as Promise<RoutingOverrideItem>
}

export const fetchDecisionChain = (decisionId: number): Promise<DecisionChainResponse> =>
  apiFetch<DecisionChainResponse>(`/routing/decision/${decisionId}/chain`)

export async function deleteRoutingOverride(id: number): Promise<void> {
  const url = `${BASE}/routing/override/${id}`
  const res = await fetch(url.startsWith('http') ? url : new URL(url, window.location.origin).toString(), {
    method: 'DELETE',
    headers: { Accept: 'application/json' },
  })
  if (!res.ok && res.status !== 204) {
    const text = await res.text().catch(() => '')
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
  }
}
