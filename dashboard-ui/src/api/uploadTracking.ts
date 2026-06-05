import { apiFetch, apiPost, apiPatch } from './client'
import type {
  UploadFilterOptions,
  UploadIngestRequest,
  UploadIngestResponse,
  UploadMetrics,
  UploadQueueItem,
  UploadQueueResponse,
} from '../types/api'

export interface UploadQueueParams {
  status?: string
  scanner_id?: string
  uploader_host?: string
  search?: string
  from_date?: string
  to_date?: string
  page?: number
  page_size?: number
}

export const fetchUploadQueue = (params: UploadQueueParams = {}): Promise<UploadQueueResponse> =>
  apiFetch<UploadQueueResponse>('/uploads/queue', {
    ...(params.status        ? { status:        params.status }        : {}),
    ...(params.scanner_id    ? { scanner_id:    params.scanner_id }    : {}),
    ...(params.uploader_host ? { uploader_host: params.uploader_host } : {}),
    ...(params.search        ? { search:        params.search }        : {}),
    ...(params.from_date     ? { from_date:     params.from_date }     : {}),
    ...(params.to_date       ? { to_date:       params.to_date }       : {}),
    page:      params.page      ?? 1,
    page_size: params.page_size ?? 50,
  })

export const fetchUploadMetrics = (): Promise<UploadMetrics> =>
  apiFetch<UploadMetrics>('/uploads/metrics')

export const fetchUploadFilters = (): Promise<UploadFilterOptions> =>
  apiFetch<UploadFilterOptions>('/uploads/filters')

export const postUploadIngest = (body: UploadIngestRequest): Promise<UploadIngestResponse> =>
  apiPost<UploadIngestResponse>('/uploads/ingest', body)

export const putUploadRecord = (
  recordId: number,
  updates: Partial<Pick<UploadQueueItem, 'upload_status' | 'estimated_upload_at' | 'upload_started_at' | 'upload_completed_at' | 'upload_speed_mbps' | 'failure_reason' | 'retry_count'>>,
): Promise<UploadQueueItem> =>
  apiPatch<UploadQueueItem>(`/uploads/queue/${recordId}`, updates)
