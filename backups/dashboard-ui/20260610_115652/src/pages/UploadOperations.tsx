/**
 * UploadOperations — Phase 3.5 / Phase 3.6
 *
 * Operational visibility into the uploader pipeline:
 *   - Live queue metrics (queued, active, done, failed, delayed)
 *   - Per-scanner summary cards with display names from fleet config
 *   - Estimated arrival times with overdue detection
 *   - Per-record upload timeline (queued → started → completed)
 *   - Filtering by status, scanner (display names), host and filename search
 */
import { useState } from 'react'
import {
  Activity, AlertTriangle, CheckCircle2, Clock,
  CloudUpload, Microscope, Search, XCircle, Zap,
} from 'lucide-react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { format, formatDistanceToNow, parseISO } from 'date-fns'
import { KpiCard } from '../components/ui/KpiCard'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { useUploadFilters, useUploadMetrics, useUploadQueue } from '../hooks/useUploadOperations'
import {
  buildScannerMap, resolveScanner,
  useScannerFleet, useScannerSummary,
} from '../hooks/useScannerFleet'
import { patchUploadPriority } from '../api/uploadTracking'
import type { ScannerMap, ScannerSummaryItem, UploadQueueItem, UploadStatus } from '../types/api'
import { fmtBytes, fmtDuration } from '../utils/formatters'

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
                  <span className="text-[9px] font-mono" style={{ color: 'var(--accent)' }}>
                    ↑{sc.active}
                  </span>
                )}
                {sc.queued > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--text-muted)' }}>
                    {sc.queued}q
                  </span>
                )}
                {sc.delayed > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-amber)' }}>
                    {sc.delayed}d
                  </span>
                )}
                {sc.failed > 0 && (
                  <span className="text-[9px] font-mono" style={{ color: 'var(--chart-rose)' }}>
                    {sc.failed}✗
                  </span>
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
      {effective === 'uploading' && <span className="animate-pulse inline-block w-1 h-1 rounded-full" style={{ background: 'currentColor' }} />}
      {STATUS_LABELS[effective]}
    </span>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Priority badge
// ---------------------------------------------------------------------------

const PRIORITY_UPLOAD_NEXT = 0
const PRIORITY_NORMAL       = 5
const PRIORITY_LOW          = 9
const _TERMINAL: UploadStatus[] = ['uploaded', 'failed']

function PriorityBadge({ priority }: { priority: number }) {
  if (priority === PRIORITY_UPLOAD_NEXT) {
    return (
      <span
        className="inline-flex items-center px-1.5 py-0.5 rounded text-[8px] font-bold uppercase tracking-wide"
        style={{
          color:      'var(--accent)',
          background: 'rgba(99,102,241,0.12)',
          border:     '1px solid rgba(99,102,241,0.30)',
          letterSpacing: '0.07em',
        }}
      >
        NEXT
      </span>
    )
  }
  if (priority === PRIORITY_LOW) {
    return (
      <span
        className="inline-flex items-center px-1.5 py-0.5 rounded text-[8px] font-semibold uppercase tracking-wide"
        style={{
          color:      'var(--chart-amber)',
          background: 'rgba(217,119,6,0.08)',
          border:     '1px solid rgba(217,119,6,0.22)',
          letterSpacing: '0.07em',
        }}
      >
        LOW
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
    <div className="flex items-center gap-0" style={{ paddingLeft: 0 }}>
      {steps.map((step, i) => (
        <div key={step.label} className="flex items-center">
          {i > 0 && (
            <div
              className="w-8 h-px"
              style={{ background: step.done ? 'var(--accent)' : 'var(--border-faint)' }}
            />
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
            <span
              className="text-[8px] whitespace-nowrap"
              style={{ color: step.done ? 'var(--text-secondary)' : 'var(--text-faint)' }}
            >
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
  )
}

// ---------------------------------------------------------------------------
// Sub-component: Queue row
// ---------------------------------------------------------------------------

function QueueRow({ item, scannerMap }: { item: UploadQueueItem; scannerMap: ScannerMap }) {
  const [expanded, setExpanded] = useState(false)
  const queryClient = useQueryClient()

  const isDelayed = item.is_delayed || (item.estimated_upload_at !== null &&
    new Date(item.estimated_upload_at) < new Date() &&
    item.upload_status !== 'uploaded' && item.upload_status !== 'failed')
  const isTerminal = (_TERMINAL as string[]).includes(item.upload_status)
  const isUploading = item.upload_status === 'uploading'

  const rowBg =
    !isTerminal && !isUploading && item.priority === PRIORITY_UPLOAD_NEXT
      ? 'rgba(99,102,241,0.05)'
      : isDelayed
      ? 'rgba(217,119,6,0.04)'
      : undefined

  const priorityMutation = useMutation({
    mutationFn: (priority: number) => patchUploadPriority(item.id, { priority }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['uploads', 'queue'] })
    },
  })

  const canChangePriority = !isTerminal && !priorityMutation.isPending

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
        {/* Filename + priority badge */}
        <td className="py-2.5 pl-4 pr-3">
          <div className="flex items-center gap-1.5">
            {isDelayed && (
              <AlertTriangle
                style={{ width: 10, height: 10, color: 'var(--chart-amber)', flexShrink: 0 }}
              />
            )}
            <span
              className="font-mono text-[10px] truncate"
              style={{ color: 'var(--text-primary)', maxWidth: 260 }}
              title={item.filename}
            >
              {item.filename}
            </span>
            {item.priority !== PRIORITY_NORMAL && (
              <PriorityBadge priority={item.priority} />
            )}
          </div>
          {item.failure_reason && (
            <p className="text-[9px] mt-0.5 truncate" style={{ color: 'var(--chart-rose)', maxWidth: 280 }}
              title={item.failure_reason}>
              {item.failure_reason}
            </p>
          )}
        </td>

        {/* Scanner — show display name from fleet config; scanner_id in tooltip */}
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

        {/* Priority actions */}
        <td className="py-2 pr-3 pl-1" onClick={e => e.stopPropagation()}>
          <div className="flex items-center gap-1">
            {/* Upload Next button — only shown when not already next and not terminal */}
            {!isTerminal && item.priority !== PRIORITY_UPLOAD_NEXT && (
              <button
                type="button"
                disabled={!canChangePriority || isUploading}
                onClick={() => priorityMutation.mutate(PRIORITY_UPLOAD_NEXT)}
                title={isUploading ? 'Cannot reprioritize active upload' : 'Move to front of queue'}
                className="text-[9px] px-1.5 py-0.5 rounded font-semibold"
                style={{
                  color:      isUploading ? 'var(--text-faint)' : 'var(--accent)',
                  background: isUploading ? 'transparent' : 'rgba(99,102,241,0.08)',
                  border:     `1px solid ${isUploading ? 'var(--border-faint)' : 'rgba(99,102,241,0.25)'}`,
                  cursor:     (!canChangePriority || isUploading) ? 'not-allowed' : 'pointer',
                  opacity:    (!canChangePriority || isUploading) ? 0.45 : 1,
                  whiteSpace: 'nowrap',
                }}
              >
                Upload Next
              </button>
            )}
            {/* Reset button — shown when priority is non-normal and not terminal */}
            {!isTerminal && item.priority !== PRIORITY_NORMAL && (
              <button
                type="button"
                disabled={!canChangePriority}
                onClick={() => priorityMutation.mutate(PRIORITY_NORMAL)}
                title="Reset to normal priority"
                className="text-[9px] px-1.5 py-0.5 rounded"
                style={{
                  color:      'var(--text-muted)',
                  background: 'transparent',
                  border:     '1px solid var(--border-faint)',
                  cursor:     !canChangePriority ? 'not-allowed' : 'pointer',
                  opacity:    !canChangePriority ? 0.45 : 1,
                  whiteSpace: 'nowrap',
                }}
              >
                Reset
              </button>
            )}
          </div>
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
// Main page
// ---------------------------------------------------------------------------

export function UploadOperations() {
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [scannerFilter, setScannerFilter] = useState('')
  const [hostFilter, setHostFilter]       = useState('')
  const [search, setSearch]               = useState('')
  const [page, setPage]                   = useState(1)
  const PAGE_SIZE = 50

  const queueParams = {
    status:        statusFilter !== 'all' ? statusFilter : undefined,
    scanner_id:    scannerFilter || undefined,
    uploader_host: hostFilter   || undefined,
    search:        search       || undefined,
    page,
    page_size: PAGE_SIZE,
  }

  const { data: queue,          isLoading: queueLoading }   = useUploadQueue(queueParams)
  const { data: metrics,        isLoading: metricsLoading }  = useUploadMetrics()
  const { data: filters }                                     = useUploadFilters()
  const { data: fleet }                                       = useScannerFleet()
  const { data: scannerSummary }                              = useScannerSummary()

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
        <KpiCard
          label="Queued"
          value={metricsLoading ? '—' : (metrics?.queued_count ?? 0)}
          icon={CloudUpload}
          accent="cyan"
          loading={metricsLoading}
        />
        <KpiCard
          label="Active"
          value={metricsLoading ? '—' : (metrics?.active_count ?? 0)}
          icon={Activity}
          accent="violet"
          loading={metricsLoading}
          subtext={metrics?.active_count ? 'uploading now' : undefined}
        />
        <KpiCard
          label="Done Today"
          value={metricsLoading ? '—' : (metrics?.completed_today ?? 0)}
          icon={CheckCircle2}
          accent="teal"
          loading={metricsLoading}
        />
        <KpiCard
          label="Failed"
          value={metricsLoading ? '—' : (metrics?.failed_count ?? 0)}
          icon={XCircle}
          accent="rose"
          loading={metricsLoading}
        />
        <KpiCard
          label="Delayed"
          value={metricsLoading ? '—' : (metrics?.delayed_count ?? 0)}
          icon={AlertTriangle}
          accent="amber"
          loading={metricsLoading}
          subtext={metrics?.delayed_count ? 'ETA exceeded' : undefined}
        />
        <KpiCard
          label="Avg Duration"
          value={metricsLoading ? '—' : (
            metrics?.avg_duration_seconds != null
              ? fmtDuration(metrics.avg_duration_seconds)
              : '—'
          )}
          icon={Clock}
          accent="emerald"
          loading={metricsLoading}
        />
        <KpiCard
          label="Avg Throughput"
          value={metricsLoading ? '—' : (
            metrics?.avg_throughput_mbps != null
              ? `${metrics.avg_throughput_mbps.toFixed(1)} Mb/s`
              : '—'
          )}
          icon={Zap}
          accent="cyan"
          loading={metricsLoading}
        />
        <KpiCard
          label="Scanners"
          value={fleet?.enabled_count ?? '—'}
          icon={Microscope}
          accent="violet"
          subtext={fleet ? `${fleet.total} configured` : undefined}
          loading={!fleet}
        />
      </div>

      {/* ── Scanner summary cards ───────────────────────────────────────── */}
      {scannerSummary && scannerSummary.scanners.length > 0 && (
        <ScannerSummaryCards
          scanners={scannerSummary.scanners}
          activeScannerFilter={scannerFilter}
          onSelectScanner={id => { setScannerFilter(id); resetPage() }}
        />
      )}

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

        {/* Scanner dropdown — options show display names, values remain scanner_id */}
        {filters?.scanners && filters.scanners.length > 0 && (
          <select
            value={scannerFilter}
            onChange={e => { setScannerFilter(e.target.value); resetPage() }}
            className="px-2 py-1 rounded text-[10px]"
            style={{
              background: 'var(--surface-inset)',
              border: '1px solid var(--border-default)',
              color: 'var(--text-secondary)',
            }}
          >
            <option value="">All scanners</option>
            {filters.scanners.map(sid => (
              <option key={sid} value={sid}>
                {resolveScanner(sid, scannerMap)}
              </option>
            ))}
          </select>
        )}

        {/* Host dropdown */}
        {filters?.hosts && filters.hosts.length > 0 && (
          <select
            value={hostFilter}
            onChange={e => { setHostFilter(e.target.value); resetPage() }}
            className="px-2 py-1 rounded text-[10px]"
            style={{
              background: 'var(--surface-inset)',
              border: '1px solid var(--border-default)',
              color: 'var(--text-secondary)',
            }}
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
                'ETA', 'Size', 'Speed / Duration', 'Retries', 'Age', '',
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
                  <CloudUpload
                    className="mx-auto mb-2 opacity-20"
                    style={{ width: 28, height: 28, color: 'var(--text-faint)' }}
                  />
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
