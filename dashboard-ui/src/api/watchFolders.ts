import { apiFetch, apiPost } from './client'
import type {
  LabelPreviewResponse,
  MonitoredFilesResponse,
  TechnicianRenameRequest,
  TechnicianRenameResponse,
  WatchFoldersResponse,
} from '../types/api'

export const fetchWatchFolders = (): Promise<WatchFoldersResponse> =>
  apiFetch<WatchFoldersResponse>('/recovery/watch-folders')

export interface MonitoredFilesParams {
  folder_type?: string
  review_status?: string
  recovery_status?: string
  search?: string
  limit?: number
}

export const fetchMonitoredFiles = (params: MonitoredFilesParams = {}): Promise<MonitoredFilesResponse> =>
  apiFetch<MonitoredFilesResponse>('/recovery/files', {
    ...(params.folder_type     ? { folder_type:     params.folder_type }     : {}),
    ...(params.review_status   ? { review_status:   params.review_status }   : {}),
    ...(params.recovery_status ? { recovery_status: params.recovery_status } : {}),
    ...(params.search          ? { search:          params.search }          : {}),
    limit: params.limit ?? 100,
  })

export const fetchLabelPreview = (fileId: number): Promise<LabelPreviewResponse> =>
  apiFetch<LabelPreviewResponse>(`/recovery/files/${fileId}/label-preview`)

export const postTechnicianRename = (
  fileId: number,
  body: TechnicianRenameRequest,
): Promise<TechnicianRenameResponse> =>
  apiPost<TechnicianRenameResponse>(`/recovery/files/${fileId}/technician-rename`, body)
