/**
 * UploadOperations — Phase 4.6D
 *
 * Simplified priority model:
 *   UPLOAD_NEXT (0) — operator-flagged "jump the queue"
 *   HIGH        (1) — urgent / watch-folder inherited / manual operator flag
 *   NORMAL      (5) — default for all files
 *
 * Queue ordering: UPLOAD_NEXT → HIGH → NORMAL, FIFO within each group.
 */
import { useState } from 'react'
import {
  Activity, AlertTriangle, CheckCircle2, Clock,
  CloudUpload, Folder, ListOrdered, Microscope, Search, XCircle, Zap,
} from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format, formatDistanceToNow, parseISO } from 'date-fns'
import { KpiCard } from '../components/ui/KpiCard'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { useUploadFilters, useUploadMetrics, useUploadQueue } from '../hooks/useUploadOperations'
import {
  buildScannerMap, resolveScanner,
  useScannerFleet, useScannerSummary,
} from '../hooks/useScannerFleet'
import { fetchNextUploads, fetchUploadPriorities, patchUploadPriority } from '../api/uploadTracking'
import type { ScannerMap, ScannerSummaryItem, UploadQueueItem, UploadStatus } from '../types/api'
import { fmtBytes, fmtDuration } from '../utils/formatters'

// ---------------------------------------------------------------------------
// Priority model
// ---------------------------------------------------------------------------

const PRIORITY_UPLOAD_NEXT = 0
const PRIORITY_HIGH        = 1
const PRIORITY_NORMAL      = 5

type PriorityFilter = 'all' | 0 | 1 | 5

const PRIORITY_FILTER_OPTIONS: PriorityFilter[] = ['all', 0, 1, 5]
const PRIORITY_FILTER_LABELS: Record<PriorityFilter, string> = {
  all: 'All',
  0:   'Upload Next',
  1:   'High',
  5:   'Normal',
}

const _TERMINAL: UploadStatus[] = ['uploaded', 'failed']

// ---------------------------------------------------------------------------
// Status styling
// ---------------------------------------------------------------------------

const STATUS_COLOR: Record<UploadStatus, string> = {
  queued:     'var(--text-muted)',
  estimating: 'var(--chart-amber)',
  uploading:  'var(--accent)',
  uploaded:   'var(--chart-teal)',
  delayed:    'var(--chart-rose)',
  failed:     'var(--chart-rose)',
}

const STATUS_BG: Record<UploadStatus, string> = {
  queued:     'var(--surface-inset)',
  estimating: 'rgba(217,119,6,0.10)',
  uploading:  'rgba(99,102,241,0.10)',
  uploaded:   'rgba(52,211,153,0.10)',
  delayed:    'rgba(225,29,72,0.10)',
  failed:     'rgba(225,29,72,0.10)',
}

const STATUS_LABELS: Record<UploadStatus | 'all', string> = {
  all:        'All',
  queued:     'Queued',
  estimating: 'Estimating',
  uploading:  'Uploading',
  uploaded:   'Uploaded',
  delayed:    'Delayed',
  failed:     'Failed',
}

const FILTER_STATUSES: Array<UploadStatus | 'all'> = [
  'all', 'queued', 'estimating', 'uploading', 'uploaded', 'delayed', 'failed',
]

// ---------------------------------------------------------------------------
// Utility formatters
// ---------------------------------------------------------------------------

function fmtEta(eta: string | null, status: UploadStatus): string {
  if (!eta) return '—'
  if (status === 'uploaded' || status === 'failed') return '—'
  try {
    const diff = parseISO(eta).getTime() - Date.now()
    if (diff <= 0) return 'Overdue'
    const mins = Math.floor(diff / 60_000)
    if (mins < 1) return '< 1 min'
    if (mins < 60) return `~ ${mins} min`
    const h = Math.floor(mins / 60)
    return `~ ${h}h ${mins % 60}m`
  } catch {
    return eta
  }
}

function fmtUtcTime(iso: string | null): string {
  if (!iso) return '—'
  try { return format(parseISO(iso), 'HH:mm') + ' UTC' } catch { return iso }
}

function fmtAge(iso: string | null): string {
  if (!iso) return '—'
  try { return formatDistanceToNow(parseISO(iso), { addSuffix: true }) } catch { return iso }
}

function calcDuration(start: string | null, end: string | null): string {
  if (!start || !end) return '—'
  try {
    const secs = (parseISO(end).getTime() - parseISO(start).getTime()) / 1000
    return fmtDuration(secs)
  } catch {
    return '—'
  }
}

// ---------------------------------------------------------------------------
// Sub-component: Per-scanner summary cards
// ---------------------------------------------------------------------------

function ScannerSummaryCards({
  scanners,
  activeScannerFilter,
  onSelectScanner,
}: {
  scanners: ScannerSummaryItem[]
  activeScannerFilter: string
  onSelectScanner: (scannerId: string) => void
}) {
  if (scanners.length === 0) return null

  return (
    <div className="mb-5">
      <p className="text-[9px] uppercase tracking-widest mb-2" style={{ color: 'var(--text-faint)', letterSpacing: '0.14em' }}>
        Scanner Fleet
      </p>
      <div className="flex flex-wrap gap-2">
        {scanners.map(sc => {
          const isActive = activeScannerFilter === sc.scanner_id
          const hasProblems = sc.delayed > 0 || sc.failed > 0
          const borderColor = hasProblems
            ? (sc.failed > 0 ? 'rgba(225,29,72,0.30)' : 'rgba(217,119,6,0.30)')
            : (isActive ? 'var(--accent)' : 'var(--border-default)')
          const bgColor = isActive
            ? 'var(--accent-faint)'
            : hasProblems && sc.failed > 0
            ? 'rgba(225,29,72,0.04)'
            : hasProblems
            ? 'rgba(217,119,6,0.04)'
            : 'var(--surface-inset)'

          return (
            <button
              key={sc.scanner_id}
              type="button"
              title={`ID: ${sc.scanner_id}${sc.location ? ` · ${sc.location}` : ''}`}
              onClick={() => onSelectScanner(isActive ? '' : sc.scanner_id)}
              className="flex flex-col gap-0.5 px-3 py-2 rounded-lg text-left"
              style={{
                background: bgColor,
                border: `1px solid ${borderColor}`,
                minWidth: 100,
                cursor: 'pointer',
                transition: 'all 120ms ease',
              }}
            >
              <span
                className="text-[11px] font-semibold"
                style={{ color: isActive ? 'var(--accent)' : 'var(--text-primary)' }}
              >
                {sc.display_name}
              </span>
              <div className="flex items-center gap-2 mt-0.5">
                {sc.active > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--accent)' }}>↑{sc.active}</span>
                )}
                {sc.queued > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--text-muted)' }}>{sc.queued}q</span>
                )}
                {sc.delayed > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-amber)' }}>{sc.delayed}d</span>
                )}
                {sc.failed > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-rose)' }}>{sc.failed}✗</span>
                )}
                {sc.active === 0 && sc.queued === 0 && sc.delayed === 0 && sc.failed === 0 && (
                  <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>idle</span>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Status badge
// ---------------------------------------------------------------------------

function StatusBadge({ status, delayed }: { status: UploadStatus; delayed: boolean }) {
  const effective = delayed && status !== 'uploaded' && status !== 'failed' ? 'delayed' : status
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide"
      style={{
        color:      STATUS_COLOR[effective],
        background: STATUS_BG[effective],
        border:     `1px solid ${STATUS_COLOR[effective]}33`,
        letterSpacing: '0.06em',
      }}
    >
      {effective === 'uploading' && (
        <span className="animate-pulse inline-block w-1 h-1 rounded-full" style={{ background: 'currentColor' }} />
      )}
      {STATUS_LABELS[effective]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Priority source badge
// ---------------------------------------------------------------------------

function PrioritySourceBadge({ item }: { item: UploadQueueItem }) {
  // Hide badge for plain default-normal files
  if (item.priority === PRIORITY_NORMAL && item.priority_source === 'default') return null

  if (item.priority === PRIORITY_UPLOAD_NEXT) {
    return (
      <span
        className="text-[8px] font-bold uppercase px-1.5 py-0.5 rounded"
        style={{
          color:      '#fff',
          background: 'rgba(99,102,241,0.75)',
          border:     '1px solid rgba(99,102,241,0.55)',
          letterSpacing: '0.07em',
        }}
      >
        UPLOAD NEXT
      </span>
    )
  }

  if (item.priority === PRIORITY_HIGH) {
    const isWatchFolder = item.priority_source === 'watch_folder'
    const label = isWatchFolder ? (item.watch_folder_label ?? 'Watch Folder') : 'Manual'
    const color = isWatchFolder ? 'var(--chart-amber)' : 'var(--chart-teal)'
    const bg    = isWatchFolder ? 'rgba(217,119,6,0.12)' : 'rgba(0,204,170,0.10)'
    const bd    = isWatchFolder ? 'rgba(217,119,6,0.35)' : 'rgba(0,204,170,0.30)'

    return (
      <span
        className="text-[8px] font-semibold uppercase px-1.5 py-0.5 rounded truncate"
        style={{ color, background: bg, border: `1px solid ${bd}`, letterSpacing: '0.07em', maxWidth: 100 }}
        title={item.watch_folder_path ?? undefined}
      >
        {label}
      </span>
    )
  }

  return null
}

// ---------------------------------------------------------------------------
// Sub-component: Upload timeline (expandable)
// ---------------------------------------------------------------------------

function UploadTimeline({ item }: { item: UploadQueueItem }) {
  type Step = { label: string; ts: string | null; done: boolean }
  const steps: Step[] = [
    { label: 'Queued',    ts: item.queued_at,           done: true },
    { label: 'Estimating', ts: null,                    done: !!item.estimated_upload_at },
    { label: 'Uploading', ts: item.upload_started_at,   done: !!item.upload_started_at },
    { label: 'Completed', ts: item.upload_completed_at, done: !!item.upload_completed_at },
  ]

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-0">
        {steps.map((step, i) => (
          <div key={step.label} className="flex items-center">
            {i > 0 && (
              <div className="w-8 h-px" style={{ background: step.done ? 'var(--accent)' : 'var(--border-faint)' }} />
            )}
            <div className="flex flex-col items-center gap-0.5">
              <div
                className="w-2 h-2 rounded-full"
                style={{
                  background: step.done
                    ? (i === steps.length - 1 && item.upload_status === 'uploaded'
                       ? 'var(--chart-teal)' : 'var(--accent)')
                    : 'var(--border-default)',
                  border: step.done ? 'none' : '1px solid var(--border-default)',
                }}
              />
              <span className="text-[8px] whitespace-nowrap" style={{ color: step.done ? 'var(--text-secondary)' : 'var(--text-faint)' }}>
                {step.label}
              </span>
              {step.ts && (
                <span className="text-[7px]" style={{ color: 'var(--text-faint)' }}>
                  {fmtUtcTime(step.ts)}
                </span>
              )}
            </div>
          </div>
        ))}
        {item.estimated_upload_at && (
          <div className="flex items-center ml-4 gap-1" style={{ color: 'var(--text-faint)' }}>
            <Clock style={{ width: 9, height: 9 }} />
            <span className="text-[9px]">ETA {fmtUtcTime(item.estimated_upload_at)}</span>
          </div>
        )}
      </div>
      {item.priority_updated_at && (
        <div className="flex items-center gap-2 text-[8px]" style={{ color: 'var(--text-faint)' }}>
          <span>Priority set {fmtAge(item.priority_updated_at)}</span>
          {item.priority_updated_by && <span>by {item.priority_updated_by}</span>}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Priority checkboxes in queue row
// ---------------------------------------------------------------------------

function PriorityCheckboxes({ item }: { item: UploadQueueItem }) {
  const queryClient = useQueryClient()

  const isTerminal  = (_TERMINAL as string[]).includes(item.upload_status)
  const isUploading = item.upload_status === 'uploading'
  const canChange   = !isTerminal

  const mutation = useMutation({
    mutationFn: (mode: 'upload_next' | 'high' | 'normal' | 'clear_upload_next') =>
      patchUploadPriority(item.id, { mode }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['uploads', 'queue'] })
      void queryClient.invalidateQueries({ queryKey: ['uploads', 'priorities'] })
      void queryClient.invalidateQueries({ queryKey: ['uploads', 'next'] })
    },
  })

  const isHigh           = item.priority <= PRIORITY_HIGH          // 0 or 1
  const isUploadNext     = item.priority === PRIORITY_UPLOAD_NEXT  // 0
  const isWatchFolder    = item.priority_source === 'watch_folder'
  const isHighLocked     = isHigh && isWatchFolder                 // cannot downgrade inherited HIGH
  const isPending        = mutation.isPending

  function handleHighChange() {
    if (!canChange || isPending) return
    if (isHigh && !isHighLocked && !isUploadNext) {
      mutation.mutate('normal')
    } else if (!isHigh) {
      mutation.mutate('high')
    }
    // Locked watch_folder HIGH: no-op (checkbox is visually disabled)
  }

  function handleUploadNextChange() {
    if (!canChange || isPending) return
    if (isUploadNext) {
      mutation.mutate('clear_upload_next')
    } else {
      // auto-promotes to HIGH+UPLOAD_NEXT if currently NORMAL
      mutation.mutate('upload_next')
    }
  }

  const highDisabled   = !canChange || isPending || isHighLocked || isUploadNext
  const nextDisabled   = !canChange || isPending

  return (
    <div className="flex items-center gap-3" onClick={e => e.stopPropagation()}>
      {/* High checkbox */}
      <label
        className="flex items-center gap-1 cursor-pointer select-none"
        title={
          isHighLocked    ? 'Inherited from watch folder — cannot downgrade' :
          isUploading     ? 'Active upload — priority applies after current slide' :
          isTerminal      ? 'Completed — priority locked' :
                            (isHigh ? 'Unset high priority' : 'Set high priority')
        }
        style={{ opacity: highDisabled && !isHighLocked ? 0.45 : 1 }}
      >
        <input
          type="checkbox"
          checked={isHigh}
          disabled={highDisabled}
          onChange={handleHighChange}
          className="rounded"
          style={{
            width: 12, height: 12,
            accentColor: 'var(--chart-amber)',
            cursor: highDisabled ? (isHighLocked ? 'not-allowed' : 'default') : 'pointer',
          }}
        />
        <span
          className="text-[9px] font-medium"
          style={{
            color: isHigh
              ? (isHighLocked ? 'var(--chart-amber)' : 'var(--chart-amber)')
              : 'var(--text-faint)',
          }}
        >
          High
        </span>
        {isHighLocked && (
          <span className="text-[7px]" style={{ color: 'var(--text-faint)' }} title="Inherited from watch folder">
            🔒
          </span>
        )}
      </label>

      {/* Upload Next checkbox */}
      <label
        className="flex items-center gap-1 cursor-pointer select-none"
        title={
          isTerminal  ? 'Completed — cannot change' :
          isUploading ? 'Active upload — will take effect after current slide' :
          isUploadNext ? 'Clear Upload Next flag' :
                         'Mark as Upload Next — jumps to front of queue'
        }
        style={{ opacity: nextDisabled && !isUploadNext ? 0.45 : 1 }}
      >
        <input
          type="checkbox"
          checked={isUploadNext}
          disabled={nextDisabled}
          onChange={handleUploadNextChange}
          className="rounded"
          style={{
            width: 12, height: 12,
            accentColor: 'rgba(99,102,241,0.90)',
            cursor: nextDisabled ? 'default' : 'pointer',
          }}
        />
        <span
          className="text-[9px] font-medium"
          style={{ color: isUploadNext ? 'rgba(139,92,246,0.90)' : 'var(--text-faint)' }}
        >
          Upload Next
        </span>
      </label>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Queue row
// ---------------------------------------------------------------------------

function QueueRow({ item, scannerMap }: { item: UploadQueueItem; scannerMap: ScannerMap }) {
  const [expanded, setExpanded] = useState(false)

  const isDelayed = item.is_delayed || (item.estimated_upload_at !== null &&
    new Date(item.estimated_upload_at) < new Date() &&
    item.upload_status !== 'uploaded' && item.upload_status !== 'failed')

  const rowBg =
    item.priority === PRIORITY_UPLOAD_NEXT && item.upload_status !== 'uploaded' && item.upload_status !== 'failed'
      ? 'rgba(99,102,241,0.05)'
      : item.priority === PRIORITY_HIGH && item.upload_status !== 'uploaded' && item.upload_status !== 'failed'
      ? 'rgba(217,119,6,0.04)'
      : isDelayed
      ? 'rgba(225,29,72,0.03)'
      : undefined

  return (
    <>
      <tr
        className="group"
        style={{
          background:  rowBg,
          borderBottom: '1px solid var(--border-faint)',
          cursor: 'pointer',
        }}
        onClick={() => setExpanded(e => !e)}
      >
        {/* Filename + priority source badge */}
        <td className="py-2.5 pl-4 pr-3">
          <div className="flex items-center gap-1.5 flex-wrap">
            {isDelayed && (
              <AlertTriangle style={{ width: 10, height: 10, color: 'var(--chart-amber)', flexShrink: 0 }} />
            )}
            <span
              className="font-mono text-[10px] truncate"
              style={{ color: 'var(--text-primary)', maxWidth: 240 }}
              title={item.filename}
            >
              {item.filename}
            </span>
            <PrioritySourceBadge item={item} />
          </div>
          {item.failure_reason && (
            <p className="text-[9px] mt-0.5 truncate" style={{ color: 'var(--chart-rose)', maxWidth: 280 }}
              title={item.failure_reason}>
              {item.failure_reason}
            </p>
          )}
        </td>

        {/* Scanner */}
        <td
          className="py-2.5 px-3 text-[10px]"
          style={{ color: 'var(--text-muted)' }}
          title={item.scanner_id ? `ID: ${item.scanner_id}` : undefined}
        >
          {resolveScanner(item.scanner_id, scannerMap)}
        </td>

        {/* Host */}
        <td className="py-2.5 px-3 text-[10px]" style={{ color: 'var(--text-faint)' }}>
          {item.uploader_host ?? '—'}
        </td>

        {/* Status */}
        <td className="py-2.5 px-3">
          <StatusBadge status={item.upload_status} delayed={isDelayed} />
        </td>

        {/* ETA */}
        <td className="py-2.5 px-3 text-[10px] font-mono tabular-nums"
          style={{
            color: isDelayed ? 'var(--chart-rose)'
                  : item.upload_status === 'uploading' ? 'var(--accent)'
                  : 'var(--text-muted)',
          }}>
          {fmtEta(item.estimated_upload_at, item.upload_status)}
        </td>

        {/* Size */}
        <td className="py-2.5 px-3 text-[10px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
          {fmtBytes(item.file_size_bytes)}
        </td>

        {/* Speed / Duration */}
        <td className="py-2.5 px-3 text-[10px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
          {item.upload_status === 'uploading' && item.upload_speed_mbps
            ? `${item.upload_speed_mbps.toFixed(1)} Mbps`
            : item.upload_status === 'uploaded'
            ? calcDuration(item.upload_started_at, item.upload_completed_at)
            : '—'}
        </td>

        {/* Retry */}
        <td className="py-2.5 px-3 text-[10px] tabular-nums" style={{ color: item.retry_count > 0 ? 'var(--chart-amber)' : 'var(--text-faint)' }}>
          {item.retry_count > 0 ? `×${item.retry_count}` : '—'}
        </td>

        {/* Age */}
        <td className="py-2.5 pl-3 pr-2 text-[10px]" style={{ color: 'var(--text-faint)' }}>
          {fmtAge(item.queued_at)}
        </td>

        {/* Priority checkboxes */}
        <td className="py-2 pr-4 pl-2">
          <PriorityCheckboxes item={item} />
        </td>
      </tr>

      {/* Expanded timeline row */}
      {expanded && (
        <tr style={{ background: 'var(--surface-inset)', borderBottom: '1px solid var(--border-faint)' }}>
          <td colSpan={10} className="px-6 py-3">
            <UploadTimeline item={item} />
          </td>
        </tr>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Watch Folder Priority Summary (HIGH folders only)
// ---------------------------------------------------------------------------

function WatchFolderPrioritySummary() {
  const { data } = useQuery({
    queryKey: ['uploads', 'priorities'],
    queryFn: fetchUploadPriorities,
    refetchInterval: 30_000,
  })

  if (!data) return null
  const hasHighFolders = data.watch_folders.length > 0
  const hasPriorityData = (data.by_priority.upload_next + data.by_priority.high) > 0
  if (!hasHighFolders && !hasPriorityData) return null

  return (
    <div
      className="rounded-lg p-4 mb-4"
      style={{ border: '1px solid var(--border-default)', background: 'var(--surface-1)' }}
    >
      <div className="flex items-center gap-2 mb-3">
        <Folder style={{ width: 12, height: 12, color: 'var(--accent)' }} />
        <p className="section-label" style={{ margin: 0 }}>Priority Queue</p>
      </div>

      {/* Distribution pills */}
      <div className="flex items-center gap-4 mb-3 flex-wrap">
        {data.by_priority.upload_next > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-[8px] font-bold uppercase" style={{ color: 'rgba(139,92,246,0.90)', letterSpacing: '0.08em' }}>UPLOAD NEXT</span>
            <span className="text-[11px] font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>{data.by_priority.upload_next}</span>
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>pending</span>
          </div>
        )}
        {data.by_priority.high > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-[8px] font-bold uppercase" style={{ color: 'var(--chart-amber)', letterSpacing: '0.08em' }}>HIGH</span>
            <span className="text-[11px] font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>{data.by_priority.high}</span>
            <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>pending</span>
          </div>
        )}
        {data.by_priority.normal > 0 && (
          <div className="flex items-center gap-1.5">
            <span className="text-[8px] uppercase" style={{ color: 'var(--text-faint)', letterSpacing: '0.08em' }}>NORMAL</span>
            <span className="text-[11px] tabular-nums" style={{ color: 'var(--text-muted)' }}>{data.by_priority.normal}</span>
          </div>
        )}
        <div className="ml-auto text-[9px]" style={{ color: 'var(--text-faint)' }}>
          Manual: {data.by_source.manual} · Watch Folder: {data.by_source.watch_folder} · Upload Next: {data.by_source.upload_next}
        </div>
      </div>

      {/* HIGH watch folder rows */}
      {hasHighFolders && (
        <div className="flex flex-col gap-1.5">
          {data.watch_folders.map(wf => (
            <div
              key={wf.watch_folder_path}
              className="flex items-center gap-3 px-3 py-2 rounded"
              style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
            >
              <span
                className="text-[8px] font-bold uppercase px-1.5 py-0.5 rounded"
                style={{ color: 'var(--chart-amber)', background: 'rgba(217,119,6,0.10)', letterSpacing: '0.07em', flexShrink: 0 }}
              >
                HIGH
              </span>
              <span className="text-[11px] font-medium" style={{ color: 'var(--text-primary)' }}>
                {wf.watch_folder_label}
              </span>
              <span className="text-[9px] font-mono truncate flex-1" style={{ color: 'var(--text-faint)' }}>
                {wf.watch_folder_path}
              </span>
              <span className="text-[10px] tabular-nums font-semibold" style={{ color: 'var(--text-secondary)' }}>
                {wf.queued_count} queued
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Scheduler mode banner
// ---------------------------------------------------------------------------

function SchedulerModeBanner() {
  return (
    <div
      className="flex items-center gap-4 px-5 py-3 mb-5"
      style={{
        background: 'var(--surface-2)',
        border: '1px solid var(--border-default)',
        borderRadius: 8,
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Left accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
        background: 'linear-gradient(to bottom, var(--chart-cyan), var(--chart-teal))',
        borderRadius: '8px 0 0 8px',
      }} />

      {/* Engine label */}
      <div style={{ paddingLeft: 4, flexShrink: 0 }}>
        <div style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 13, fontWeight: 700, letterSpacing: '0.16em',
          textTransform: 'uppercase', color: 'var(--accent)',
          lineHeight: 1,
        }}>
          Queue Management Engine
        </div>
        <div style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 8, letterSpacing: '0.20em',
          textTransform: 'uppercase', color: 'var(--text-faint)',
          marginTop: 3,
        }}>
          Priority Scheduler · Active
        </div>
      </div>

      {/* Divider */}
      <div style={{ width: 1, height: 28, background: 'var(--border-default)', flexShrink: 0 }} aria-hidden />

      {/* Priority badges */}
      <div className="flex items-center gap-2 flex-wrap">
        {[
          { label: 'UPLOAD NEXT',      color: '#22D3EE', bg: 'rgba(34,211,238,0.10)', border: 'rgba(34,211,238,0.28)', order: '0' },
          { label: 'HIGH WATCH',       color: '#C084FC', bg: 'rgba(192,132,252,0.10)', border: 'rgba(192,132,252,0.28)', order: '1' },
          { label: 'NORMAL FIFO',      color: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.20)', order: '5' },
        ].map(({ label, color, bg, border, order }, i) => (
          <div key={label} className="flex items-center gap-1.5">
            {i > 0 && <span style={{ fontSize: 9, color: 'var(--text-faint)' }}>→</span>}
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 5,
              padding: '3px 9px', borderRadius: 4,
              background: bg, border: `1px solid ${border}`,
              fontFamily: '"JetBrains Mono", monospace',
              fontSize: 10, fontWeight: 700, letterSpacing: '0.06em',
              color,
              whiteSpace: 'nowrap',
            }}>
              <span style={{
                width: 5, height: 5, borderRadius: '50%', background: color,
                display: 'inline-block', flexShrink: 0,
              }} />
              {label}
              <span style={{ fontSize: 8, opacity: 0.6, marginLeft: 1 }}>p={order}</span>
            </span>
          </div>
        ))}
      </div>

      {/* Right live indicator */}
      <div style={{ marginLeft: 'auto', flexShrink: 0, display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          width: 7, height: 7, borderRadius: '50%',
          background: 'var(--chart-emerald)',
          display: 'inline-block',
          animation: 'lcBlink 2.4s ease-in-out infinite',
          boxShadow: '0 0 6px var(--chart-emerald)',
        }} />
        <span style={{
          fontFamily: '"JetBrains Mono", monospace',
          fontSize: 9, fontWeight: 600, letterSpacing: '0.12em',
          color: 'var(--chart-emerald)',
        }}>ONLINE</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Next Uploads preview panel
// ---------------------------------------------------------------------------

function NextUploadsPanel() {
  const { data: items, isLoading } = useQuery({
    queryKey: ['uploads', 'next'],
    queryFn: () => fetchNextUploads(8),
    refetchInterval: 15_000,
    staleTime: 10_000,
  })

  if (!isLoading && (!items || items.length === 0)) return null

  function priorityLabel(p: number): string | null {
    if (p === PRIORITY_UPLOAD_NEXT) return 'NEXT'
    if (p === PRIORITY_HIGH)        return 'HIGH'
    return null
  }

  function priorityColor(p: number): string {
    if (p === PRIORITY_UPLOAD_NEXT) return 'rgba(139,92,246,0.90)'
    if (p === PRIORITY_HIGH)        return 'var(--chart-amber)'
    return 'var(--text-faint)'
  }

  return (
    <div
      className="rounded-lg mb-4"
      style={{ border: '1px solid var(--border-default)', background: 'var(--surface-1)' }}
    >
      <div
        className="flex items-center gap-2 px-4 py-2.5"
        style={{ borderBottom: '1px solid var(--border-faint)' }}
      >
        <ListOrdered style={{ width: 12, height: 12, color: 'var(--accent)' }} />
        <p className="section-label" style={{ margin: 0 }}>Next Uploads</p>
        <span className="text-[9px] ml-auto" style={{ color: 'var(--text-faint)' }}>
          dequeue order preview
        </span>
      </div>

      <div className="divide-y" style={{ borderColor: 'var(--border-faint)' }}>
        {isLoading && Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="px-4 py-2"><SkeletonRow /></div>
        ))}

        {!isLoading && items?.map((item, idx) => {
          const lbl = priorityLabel(item.priority)
          return (
            <div
              key={item.id}
              className="flex items-center gap-3 px-4 py-2"
              style={{ background: idx === 0 ? 'rgba(99,102,241,0.04)' : undefined }}
            >
              <span
                className="text-[9px] font-mono w-5 text-right tabular-nums flex-shrink-0"
                style={{ color: idx === 0 ? 'var(--accent)' : 'var(--text-faint)' }}
              >
                {idx + 1}
              </span>
              {lbl ? (
                <span
                  className="text-[8px] font-bold uppercase px-1.5 py-0.5 rounded flex-shrink-0"
                  style={{
                    color: priorityColor(item.priority),
                    background: 'rgba(0,0,0,0.20)',
                    letterSpacing: '0.07em',
                    minWidth: 32,
                    textAlign: 'center',
                  }}
                >
                  {lbl}
                </span>
              ) : (
                <span className="flex-shrink-0" style={{ minWidth: 32 }} />
              )}
              <span
                className="font-mono text-[10px] truncate flex-1"
                style={{ color: 'var(--text-primary)' }}
                title={item.filename}
              >
                {item.filename}
              </span>
              {item.watch_folder_label && (
                <span className="text-[8px] truncate" style={{ color: 'var(--chart-amber)', maxWidth: 90, flexShrink: 0 }}>
                  {item.watch_folder_label}
                </span>
              )}
              <span className="text-[9px] tabular-nums flex-shrink-0" style={{ color: 'var(--text-faint)' }}>
                {item.queued_at ? formatDistanceToNow(parseISO(item.queued_at), { addSuffix: true }) : '—'}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function UploadOperations() {
  const [statusFilter,   setStatusFilter]   = useState<string>('all')
  const [priorityFilter, setPriorityFilter] = useState<PriorityFilter>('all')
  const [scannerFilter,  setScannerFilter]  = useState('')
  const [hostFilter,     setHostFilter]     = useState('')
  const [search,         setSearch]         = useState('')
  const [page,           setPage]           = useState(1)
  const PAGE_SIZE = 50

  const queueParams = {
    status:         statusFilter !== 'all' ? statusFilter : undefined,
    scanner_id:     scannerFilter || undefined,
    uploader_host:  hostFilter    || undefined,
    search:         search        || undefined,
    priority_filter: priorityFilter !== 'all' ? priorityFilter : undefined,
    page,
    page_size: PAGE_SIZE,
  }

  const { data: queue,          isLoading: queueLoading }  = useUploadQueue(queueParams)
  const { data: metrics,        isLoading: metricsLoading } = useUploadMetrics()
  const { data: filters }                                    = useUploadFilters()
  const { data: fleet }                                      = useScannerFleet()
  const { data: scannerSummary }                             = useScannerSummary()

  const scannerMap = buildScannerMap(fleet)
  const totalPages = queue ? Math.ceil(queue.total / PAGE_SIZE) : 1

  function resetPage() { setPage(1) }

  return (
    <div>
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <PageHeader
        tag="Operations"
        title="Upload Operations"
        subtitle={`${queue?.total ?? 0} entries in queue`}
        actions={
          <div className="flex items-center gap-3">
            <picture>
              <source media="(prefers-color-scheme: dark)" srcSet="/cph-logo-white.png" />
              <img src="/cph-logo-black.png" alt="CPH" style={{ height: 20, opacity: 0.7 }} />
            </picture>
          </div>
        }
      />

      {/* ── KPI metrics ─────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3 mb-6">
        <KpiCard label="Queued" value={metricsLoading ? '—' : (metrics?.queued_count ?? 0)} icon={CloudUpload} accent="cyan" loading={metricsLoading} />
        <KpiCard label="Active" value={metricsLoading ? '—' : (metrics?.active_count ?? 0)} icon={Activity} accent="violet" loading={metricsLoading} subtext={metrics?.active_count ? 'uploading now' : undefined} />
        <KpiCard label="Done Today" value={metricsLoading ? '—' : (metrics?.completed_today ?? 0)} icon={CheckCircle2} accent="teal" loading={metricsLoading} />
        <KpiCard label="Failed" value={metricsLoading ? '—' : (metrics?.failed_count ?? 0)} icon={XCircle} accent="rose" loading={metricsLoading} />
        <KpiCard label="Delayed" value={metricsLoading ? '—' : (metrics?.delayed_count ?? 0)} icon={AlertTriangle} accent="amber" loading={metricsLoading} subtext={metrics?.delayed_count ? 'ETA exceeded' : undefined} />
        <KpiCard
          label="Avg Duration"
          value={metricsLoading ? '—' : (metrics?.avg_duration_seconds != null ? fmtDuration(metrics.avg_duration_seconds) : '—')}
          icon={Clock} accent="emerald" loading={metricsLoading}
        />
        <KpiCard
          label="Avg Throughput"
          value={metricsLoading ? '—' : (metrics?.avg_throughput_mbps != null ? `${metrics.avg_throughput_mbps.toFixed(1)} Mb/s` : '—')}
          icon={Zap} accent="cyan" loading={metricsLoading}
        />
        <KpiCard label="Scanners" value={fleet?.enabled_count ?? '—'} icon={Microscope} accent="violet" subtext={fleet ? `${fleet.total} configured` : undefined} loading={!fleet} />
      </div>

      {/* ── Scanner summary cards ───────────────────────────────────────── */}
      {scannerSummary && scannerSummary.scanners.length > 0 && (
        <ScannerSummaryCards
          scanners={scannerSummary.scanners}
          activeScannerFilter={scannerFilter}
          onSelectScanner={id => { setScannerFilter(id); resetPage() }}
        />
      )}

      {/* ── Scheduler mode banner ───────────────────────────────────────── */}
      <SchedulerModeBanner />

      {/* ── Watch Folder Priority Summary ───────────────────────────────── */}
      <WatchFolderPrioritySummary />

      {/* ── Next Uploads preview ────────────────────────────────────────── */}
      <NextUploadsPanel />

      {/* ── Filter bar ──────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        {/* Status pills */}
        <div className="flex items-center gap-1 flex-wrap">
          {FILTER_STATUSES.map(s => (
            <button
              key={s}
              type="button"
              onClick={() => { setStatusFilter(s); resetPage() }}
              className="px-2.5 py-1 rounded text-[10px] font-medium"
              style={{
                background: statusFilter === s
                  ? (s === 'all' ? 'var(--accent)' : STATUS_COLOR[s as UploadStatus])
                  : 'var(--surface-inset)',
                color: statusFilter === s ? 'var(--surface-1)' : 'var(--text-muted)',
                border: '1px solid var(--border-default)',
                transition: 'all 120ms ease',
              }}
            >
              {STATUS_LABELS[s]}
            </button>
          ))}
        </div>

        <div className="h-4 w-px" style={{ background: 'var(--border-default)' }} />

        {/* Priority filter pills */}
        <div className="flex items-center gap-1">
          {PRIORITY_FILTER_OPTIONS.map(p => (
            <button
              key={p}
              type="button"
              onClick={() => { setPriorityFilter(p); resetPage() }}
              className="px-2.5 py-1 rounded text-[10px] font-medium"
              style={{
                background: priorityFilter === p ? 'var(--accent-medium)' : 'var(--surface-inset)',
                color: priorityFilter === p ? 'var(--accent)' : 'var(--text-muted)',
                border: `1px solid ${priorityFilter === p ? 'var(--accent)' : 'var(--border-default)'}`,
                transition: 'all 120ms ease',
              }}
            >
              {PRIORITY_FILTER_LABELS[p]}
            </button>
          ))}
        </div>

        <div className="h-4 w-px" style={{ background: 'var(--border-default)' }} />

        {/* Scanner dropdown */}
        {filters?.scanners && filters.scanners.length > 0 && (
          <select
            value={scannerFilter}
            onChange={e => { setScannerFilter(e.target.value); resetPage() }}
            className="px-2 py-1 rounded text-[10px]"
            style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)' }}
          >
            <option value="">All scanners</option>
            {filters.scanners.map(sid => (
              <option key={sid} value={sid}>{resolveScanner(sid, scannerMap)}</option>
            ))}
          </select>
        )}

        {/* Host dropdown */}
        {filters?.hosts && filters.hosts.length > 0 && (
          <select
            value={hostFilter}
            onChange={e => { setHostFilter(e.target.value); resetPage() }}
            className="px-2 py-1 rounded text-[10px]"
            style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-default)', color: 'var(--text-secondary)' }}
          >
            <option value="">All hosts</option>
            {filters.hosts.map(h => (
              <option key={h} value={h}>{h}</option>
            ))}
          </select>
        )}

        {/* Search */}
        <div className="flex items-center gap-1.5 rounded px-2 py-1"
          style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-default)' }}>
          <Search style={{ width: 11, height: 11, color: 'var(--text-faint)' }} />
          <input
            type="text"
            placeholder="Filename or slide ID…"
            value={search}
            onChange={e => { setSearch(e.target.value); resetPage() }}
            className="text-[10px] bg-transparent outline-none w-44"
            style={{ color: 'var(--text-primary)' }}
          />
        </div>
      </div>

      {/* ── Queue table ─────────────────────────────────────────────────── */}
      <div
        className="rounded-lg overflow-hidden"
        style={{ border: '1px solid var(--border-default)', background: 'var(--surface-1)' }}
      >
        <table className="w-full text-left border-collapse">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border-default)', background: 'var(--surface-inset)' }}>
              {[
                'Filename', 'Scanner', 'Host', 'Status',
                'ETA', 'Size', 'Speed / Duration', 'Retries', 'Age', 'Priority',
              ].map(col => (
                <th
                  key={col}
                  className="py-2.5 px-3 text-[9px] font-semibold uppercase tracking-wider"
                  style={{ color: 'var(--text-faint)', letterSpacing: '0.10em' }}
                >
                  {col}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {queueLoading && (
              Array.from({ length: 6 }).map((_, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                  <td colSpan={10} className="py-2.5 px-4">
                    <SkeletonRow />
                  </td>
                </tr>
              ))
            )}

            {!queueLoading && (!queue?.items || queue.items.length === 0) && (
              <tr>
                <td colSpan={10} className="py-12 text-center">
                  <CloudUpload className="mx-auto mb-2 opacity-20" style={{ width: 28, height: 28, color: 'var(--text-faint)' }} />
                  <p className="text-[11px]" style={{ color: 'var(--text-faint)' }}>
                    No upload records match the current filters
                  </p>
                </td>
              </tr>
            )}

            {!queueLoading && queue?.items?.map(item => (
              <QueueRow key={item.id} item={item} scannerMap={scannerMap} />
            ))}
          </tbody>
        </table>

        {/* Pagination */}
        {!queueLoading && totalPages > 1 && (
          <div
            className="flex items-center justify-between px-4 py-3"
            style={{ borderTop: '1px solid var(--border-faint)' }}
          >
            <span className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
              {queue?.total} total · page {page} of {totalPages}
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
                className="px-3 py-1 rounded text-[10px]"
                style={{
                  background: 'var(--surface-inset)',
                  border: '1px solid var(--border-default)',
                  color: page <= 1 ? 'var(--text-faint)' : 'var(--text-secondary)',
                  cursor: page <= 1 ? 'not-allowed' : 'pointer',
                }}
              >
                ‹ Prev
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage(p => p + 1)}
                className="px-3 py-1 rounded text-[10px]"
                style={{
                  background: 'var(--surface-inset)',
                  border: '1px solid var(--border-default)',
                  color: page >= totalPages ? 'var(--text-faint)' : 'var(--text-secondary)',
                  cursor: page >= totalPages ? 'not-allowed' : 'pointer',
                }}
              >
                Next ›
              </button>
            </div>
          </div>
        )}
      </div>

      <p
        className="mt-3 text-[9px] text-center"
        style={{ color: 'var(--text-faint)', letterSpacing: '0.04em' }}
      >
        Live · auto-refreshes every 15 s · click any row to expand upload timeline
      </p>
    </div>
  )
}
