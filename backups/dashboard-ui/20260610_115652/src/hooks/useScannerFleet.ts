/**
 * useScannerFleet — scanner fleet hooks and display name utilities.
 *
 * resolveScanner()   — maps scanner_id → display name (fallback: raw id)
 * buildScannerMap()  — builds a lookup map from a ScannerFleetResponse
 */
import { useQuery } from '@tanstack/react-query'
import { fetchScannerFleet, fetchScannerSummary } from '../api/scanners'
import type { ScannerFleetResponse, ScannerMap } from '../types/api'

export function useScannerFleet(includeDisabled = false) {
  return useQuery({
    queryKey:        ['scanners', 'fleet', includeDisabled],
    queryFn:         () => fetchScannerFleet(includeDisabled),
    // Scanner config changes rarely — cache for 5 minutes
    staleTime:       300_000,
    refetchInterval: 300_000,
  })
}

export function useScannerSummary() {
  return useQuery({
    queryKey:        ['scanners', 'summary'],
    queryFn:         fetchScannerSummary,
    refetchInterval: 15_000,
    staleTime:       10_000,
    placeholderData: prev => prev,
  })
}

/** Build a scanner_id → display_name lookup from the fleet response. */
export function buildScannerMap(fleet: ScannerFleetResponse | undefined): ScannerMap {
  if (!fleet) return {}
  return Object.fromEntries(fleet.scanners.map(s => [s.scanner_id, s.display_name]))
}

/**
 * Resolve a scanner_id to its display name.
 * Falls back to the raw scanner_id when not found in the map.
 * Returns '—' when scanner_id is null/undefined.
 */
export function resolveScanner(
  scannerId: string | null | undefined,
  map: ScannerMap,
): string {
  if (!scannerId) return '—'
  return map[scannerId] ?? scannerId
}
