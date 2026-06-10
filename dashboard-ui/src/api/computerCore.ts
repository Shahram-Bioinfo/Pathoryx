import { apiFetch } from './client'
import type {
  CoreOverviewResponse,
  RecoveryStatsResponse,
  ScannerActivityResponse,
  StainDistributionResponse,
  StorageStatsResponse,
  UploadVelocityResponse,
} from '../types/api'

export const fetchCoreOverview = (): Promise<CoreOverviewResponse> =>
  apiFetch<CoreOverviewResponse>('/core/overview')

export const fetchCoreScanners = (): Promise<ScannerActivityResponse> =>
  apiFetch<ScannerActivityResponse>('/core/scanners')

export const fetchCoreStains = (): Promise<StainDistributionResponse> =>
  apiFetch<StainDistributionResponse>('/core/stains')

export const fetchCoreRecovery = (): Promise<RecoveryStatsResponse> =>
  apiFetch<RecoveryStatsResponse>('/core/recovery')

export const fetchCoreStorage = (): Promise<StorageStatsResponse> =>
  apiFetch<StorageStatsResponse>('/core/storage')

export const fetchCoreUploads = (): Promise<UploadVelocityResponse> =>
  apiFetch<UploadVelocityResponse>('/core/uploads')
