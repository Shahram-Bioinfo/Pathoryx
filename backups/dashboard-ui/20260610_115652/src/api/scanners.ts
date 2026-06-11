import { apiFetch } from './client'
import type { ScannerFleetResponse, ScannerSummaryResponse } from '../types/api'

export const fetchScannerFleet = (includeDisabled = false): Promise<ScannerFleetResponse> =>
  apiFetch<ScannerFleetResponse>('/scanners', includeDisabled ? { include_disabled: 'true' } : {})

export const fetchScannerSummary = (): Promise<ScannerSummaryResponse> =>
  apiFetch<ScannerSummaryResponse>('/scanners/summary')
