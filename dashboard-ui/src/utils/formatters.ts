import { format, formatDistanceToNow, parseISO } from 'date-fns'

export function fmtDatetime(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return format(parseISO(iso), 'MMM d, yyyy HH:mm')
  } catch {
    return iso
  }
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return format(parseISO(iso), 'MMM d, yyyy')
  } catch {
    return iso
  }
}

export function fmtRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    return formatDistanceToNow(parseISO(iso), { addSuffix: true })
  } catch {
    return iso
  }
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—'
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s`
}

export function fmtNumber(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toLocaleString()
}

export function fmtPercent(value: number, total: number): string {
  if (total === 0) return '0%'
  return `${((value / total) * 100).toFixed(1)}%`
}

/*
 * fmtServiceName — canonical display name for internal service IDs.
 * Call this everywhere a service ID is shown to the user. Never show raw IDs.
 * Value strings are intentionally brand-cased (Babel-Shark, RecoverySentry).
 */
export function fmtServiceName(name: string): string {
  const map: Record<string, string> = {
    babelshark:      'Babel-Shark Service',
    qc_service:      'QC Service',
    qc:              'QC Service',
    dicom_service:   'DICOM Service',
    dicom:           'DICOM Service',
    dicomizer:       'DICOM Service',
    upload_service:  'Upload Service',
    uploader:        'Upload Service',
    recovery_sentry: 'RecoverySentry',
    failed_watcher:  'RecoverySentry Legacy Watcher',
  }
  return map[name.toLowerCase()] ?? name
}

/*
 * fmtStageName — display name for pipeline stage keys (qc, dicom, upload, intake).
 * Falls back to space-replacing underscore for unknown stage keys.
 */
export function fmtStageName(name: string): string {
  const map: Record<string, string> = {
    qc:       'QC',
    dicom:    'DICOM',
    upload:   'Upload',
    intake:   'Intake',
    recovery: 'Recovery',
  }
  return map[name.toLowerCase()] ?? name.replace(/_/g, ' ')
}

/*
 * fmtStatusLabel — human display for status/event-type tokens like "qc_pending",
 * "dicom_done", "upload_failed". Stage prefixes are uppercased correctly; remaining
 * parts are title-cased. Falls back to generic title-casing for unknown prefixes.
 *
 * Examples:
 *   qc_pending   → "QC Pending"
 *   dicom_done   → "DICOM Done"
 *   upload_failed → "Upload Failed"
 *   intake_running → "Intake Running"
 *   detected     → "Detected"
 *   uploaded     → "Uploaded"
 */
export function fmtStatusLabel(status: string): string {
  const STAGE: Record<string, string> = {
    qc: 'QC', dicom: 'DICOM', upload: 'Upload', intake: 'Intake', recovery: 'Recovery',
  }
  const parts     = status.split('_')
  const stageLabel = STAGE[parts[0].toLowerCase()]
  if (stageLabel) {
    const rest = parts.slice(1).map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ')
    return rest ? `${stageLabel} ${rest}` : stageLabel
  }
  return parts.map(p => p.charAt(0).toUpperCase() + p.slice(1)).join(' ')
}

/*
 * fmtEventType — formats "namespace.event_name" event types for display.
 * The namespace part is kept as-is; the event name is run through fmtStatusLabel
 * so tokens like "qc_passed" render as "QC Passed".
 */
export function fmtEventType(eventType: string): string {
  const parts = eventType.split('.')
  if (parts.length === 1) return fmtStatusLabel(parts[0])
  return `${parts[0]} › ${parts.slice(1).map(fmtStatusLabel).join(' › ')}`
}
