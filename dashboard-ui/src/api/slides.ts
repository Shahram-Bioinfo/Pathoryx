import { apiFetch } from './client'
import type { ArtifactInvestigationResponse, SlideDetailResponse, SlideListResponse } from '../types/api'

export interface SlideListParams {
  page?: number
  page_size?: number
  status?: string
}

export const fetchSlides = (params: SlideListParams = {}): Promise<SlideListResponse> =>
  apiFetch<SlideListResponse>('/slides', {
    page: params.page ?? 1,
    page_size: params.page_size ?? 50,
    ...(params.status ? { status: params.status } : {}),
  })

export const fetchSlideDetail = (globalArtifactId: string): Promise<SlideDetailResponse> =>
  apiFetch<SlideDetailResponse>(`/slides/${encodeURIComponent(globalArtifactId)}`)

export const fetchArtifactInvestigation = (
  globalArtifactId: string,
  eventsLimit = 100,
): Promise<ArtifactInvestigationResponse> =>
  apiFetch<ArtifactInvestigationResponse>(
    `/artifacts/${encodeURIComponent(globalArtifactId)}/investigation`,
    { events_limit: eventsLimit },
  )
