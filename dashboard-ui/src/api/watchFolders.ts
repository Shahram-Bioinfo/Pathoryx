import { apiFetch, apiPatch, apiPost } from './client'
import type {
  AuditTrailResponse,
  FilenameValidationResponse,
  LabelPreviewResponse,
  MonitoredFilesResponse,
  OpenFolderResponse,
  ReviewStateUpdateRequest,
  ReviewStateUpdateResponse,
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

export const fetchAuditTrail = (fileId: number): Promise<AuditTrailResponse> =>
  apiFetch<AuditTrailResponse>(`/recovery/files/${fileId}/audit-trail`)

export const postTechnicianRename = (
  fileId: number,
  body: TechnicianRenameRequest,
): Promise<TechnicianRenameResponse> =>
  apiPost<TechnicianRenameResponse>(`/recovery/files/${fileId}/technician-rename`, body)

export const postValidateFilename = (
  filename: string,
  originalExtension?: string,
): Promise<FilenameValidationResponse> =>
  apiPost<FilenameValidationResponse>('/recovery/validate-filename', {
    filename,
    ...(originalExtension ? { original_extension: originalExtension } : {}),
  })

export const patchReviewState = (
  changeId: number,
  body: ReviewStateUpdateRequest,
): Promise<ReviewStateUpdateResponse> =>
  apiPatch<ReviewStateUpdateResponse>(`/recovery/changes/${changeId}/review-state`, body)

export const postOpenFolder = (fileId: number): Promise<OpenFolderResponse> =>
  apiPost<OpenFolderResponse>(`/recovery/files/${fileId}/open-folder`, {})
