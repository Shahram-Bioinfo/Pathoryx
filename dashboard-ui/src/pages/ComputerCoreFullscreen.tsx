/**
 * ComputerCoreFullscreen — Phase 4.5
 * True LCARS immersive operations console.
 * Route: /computer-core/fullscreen  (rendered outside Shell)
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  useCoreOverview,
  useCoreRecovery,
  useCoreScanners,
  useCoreStains,
  useCoreStorage,
  useCoreUploads,
} from '../hooks/useComputerCore'
import { fetchUploadQueue, patchUploadPriority } from '../api/uploadTracking'
import type {
  ScannerActivityItem,
  StorageScannerItem,
  UploadQueueItem,
} from '../types/api'

// ── Formatters ───────────────────────────────────────────────────────────────

function fmtNum(n: number | undefined | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)} B`
  if (n >= 1_000_000)     return `${(n / 1_000_000).toFixed(1)} M`
  if (n >= 1_000)         return `${(n / 1_000).toFixed(1)} K`
  return String(n)
}

function fmtBytes(b: number | undefined | null): string {
  if (b == null || b === 0) return '—'
  if (b >= 1_099_511_627_776) return `${(b / 1_099_511_627_776).toFixed(1)} TB`
  if (b >= 1_073_741_824)     return `${(b / 1_073_741_824).toFixed(1)} GB`
  if (b >= 1_048_576)         return `${(b / 1_048_576).toFixed(1)} MB`
  return `${(b / 1024).toFixed(0)} KB`
}

function fmtPct(n: number | undefined | null): string {
  if (n == null) return '—'
  return `${n.toFixed(1)} %`
}

function fmtDuration(secs: number | null | undefined): string {
  if (secs == null) return '—'
  if (secs < 60) return `${Math.round(secs)} s`
  return `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`
}

function fmtDay(iso: string | null): string {
  if (!iso) return '?'
  try { return iso.slice(5, 10) } catch { return '?' }
}

// ── Clock hook ───────────────────────────────────────────────────────────────

function useClock() {
  const [time, setTime] = useState(() =>
    new Date().toLocaleTimeString('en-US', { hour12: false })
  )
  useEffect(() => {
    const id = setInterval(
      () => setTime(new Date().toLocaleTimeString('en-US', { hour12: false })),
      1000
    )
    return () => clearInterval(id)
  }, [])
  return time
}

// ── Priority helpers ─────────────────────────────────────────────────────────

const PRIO_NEXT   = 0
const PRIO_NORMAL = 5
const PRIO_LOW    = 9
const TERMINAL_STATUSES = new Set(['uploaded', 'failed'])

function prioBadge(item: UploadQueueItem): { label: string; cls: string } {
  if (item.upload_status === 'uploading') return { label: 'UPLOADING', cls: 'fs-badge-active'  }
  if (item.upload_status === 'failed')    return { label: 'FAILED',    cls: 'fs-badge-failed'  }
  if (item.upload_status === 'uploaded')  return { label: 'DONE',      cls: 'fs-badge-done'    }
  if (item.is_delayed || item.upload_status === 'delayed')
                                          return { label: 'DELAYED',   cls: 'fs-badge-delayed' }
  if (item.priority === PRIO_NEXT)        return { label: 'NEXT',      cls: 'fs-badge-next'    }
  if (item.priority >= PRIO_LOW)          return { label: 'LOW',       cls: 'fs-badge-low'     }
  return { label: 'QUEUED', cls: 'fs-badge-normal' }
}

// ── Rail section definitions ─────────────────────────────────────────────────

const RAIL_SECTIONS = [
  { id: 'overview',  label: 'OVERVIEW',  color: 'var(--fs-orange)'  },
  { id: 'scanners',  label: 'SCANNERS',  color: 'var(--fs-lt-blue)' },
  { id: 'queue',     label: 'QUEUE',     color: 'var(--fs-orange)'  },
  { id: 'velocity',  label: 'VELOCITY',  color: 'var(--fs-teal)'    },
  { id: 'stains',    label: 'STAINS',    color: 'var(--fs-purple)'  },
  { id: 'recovery',  label: 'RECOVERY',  color: 'var(--fs-red)'     },
  { id: 'storage',   label: 'STORAGE',   color: 'var(--fs-cyan)'    },
]

// ── Shared UI primitives ─────────────────────────────────────────────────────

function SectionHeader({
  label, color, meta,
}: {
  label: string
  color: string
  meta?: string
}) {
  return (
    <div className="fs-section-header">
      <div className="fs-section-pill" style={{ background: color }}>
        <span className="fs-section-title" style={{ color: 'var(--fs-bg)' }}>{label}</span>
      </div>
      {meta && <div className="fs-section-meta">{meta}</div>}
    </div>
  )
}

function Metric({
  value, label, color, size = 'md',
}: {
  value: string | number
  label: string
  color?: string
  size?: 'xl' | 'lg' | 'md' | 'sm'
}) {
  return (
    <div className="fs-metric">
      <span className={`fs-metric-${size}`} style={color ? { color } : undefined}>
        {value ?? '—'}
      </span>
      <span className="fs-metric-label">{label}</span>
    </div>
  )
}

function DataRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="fs-data-row">
      <span className="fs-data-key">{label}</span>
      <span className="fs-data-val" style={color ? { color } : undefined}>{value}</span>
    </div>
  )
}

function DistBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  return (
    <div className="fs-dist-bar">
      <div className="fs-dist-bar-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  )
}

function MiniBarChart({ data, color }: { data: Array<{ day: string | null; count: number }>; color: string }) {
  const max = Math.max(...data.map(d => d.count), 1)
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 40 }}>
      {data.map((d, i) => (
        <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
          <div
            style={{
              width: '100%',
              height: `${Math.max((d.count / max) * 34, 2)}px`,
              background: color,
              borderRadius: 2,
              opacity: .60 + (i / data.length) * .40,
            }}
            title={`${fmtDay(d.day)}: ${d.count}`}
          />
          <span style={{ fontSize: 7, color: 'var(--fs-text-faint)', letterSpacing: '.08em' }}>
            {fmtDay(d.day)}
          </span>
        </div>
      ))}
    </div>
  )
}

function FsLoading({ label }: { label: string }) {
  return (
    <div style={{ padding: '8px 0', fontSize: 9, letterSpacing: '.16em', color: 'var(--fs-text-faint)' }}>
      {label}...
    </div>
  )
}

function FsEmpty({ label }: { label: string }) {
  return (
    <div style={{ padding: '8px 0', fontSize: 9, letterSpacing: '.16em', color: 'var(--fs-text-faint)' }}>
      {label}
    </div>
  )
}

// ── Section: Overview ────────────────────────────────────────────────────────

function OverviewSection() {
  const { data, isLoading } = useCoreOverview()
  const dash = data

  return (
    <div className="fs-section">
      <SectionHeader
        label="COMPUTER CORE STATUS"
        color="var(--fs-orange)"
        meta={dash?.as_of ? `AS OF ${new Date(dash.as_of).toLocaleTimeString()}` : undefined}
      />
      <div style={{ padding: '16px 20px' }}>
        {/* Primary metrics */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 16, marginBottom: 14 }}>
          <Metric value={isLoading ? '—' : fmtNum(dash?.total_slides)}    label="TOTAL SLIDES"    size="xl" color="var(--fs-orange)" />
          <Metric value={isLoading ? '—' : fmtNum(dash?.uploaded_today)}  label="UPLOADED TODAY"  size="xl" color="var(--fs-teal)"   />
          <Metric
            value={isLoading ? '—' : fmtNum(dash?.active_uploads)}
            label="ACTIVE UPLOADS"
            size="xl"
            color={dash?.active_uploads ? 'var(--fs-cyan)' : 'var(--fs-text-faint)'}
          />
          <Metric value={isLoading ? '—' : fmtNum(dash?.queued_uploads)} label="QUEUE DEPTH" size="xl" color="var(--fs-lt-blue)" />
        </div>

        <div className="fs-divider" />

        {/* Secondary metrics */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 10 }}>
          <Metric value={isLoading ? '—' : fmtNum(dash?.slides_today)} label="SLIDES TODAY" size="sm" />
          <Metric
            value={isLoading ? '—' : fmtNum(dash?.failed_slides)}
            label="PIPELINE FAILURES"
            size="sm"
            color={dash?.failed_slides ? 'var(--fs-red)' : 'var(--fs-text-faint)'}
          />
          <Metric
            value={isLoading ? '—' : fmtNum(dash?.delayed_uploads)}
            label="DELAYED UPLOADS"
            size="sm"
            color={dash?.delayed_uploads ? 'var(--fs-orange)' : 'var(--fs-text-faint)'}
          />
          <Metric value={isLoading ? '—' : fmtNum(dash?.recovery_backlog)}   label="RECOVERY BACKLOG"   size="sm" />
          <Metric
            value={isLoading ? '—' : fmtNum(dash?.unreviewed_changes)}
            label="UNREVIEWED CHANGES"
            size="sm"
            color={dash?.unreviewed_changes ? 'var(--fs-yellow)' : 'var(--fs-text-faint)'}
          />
        </div>

        {/* Pipeline distribution band */}
        {!isLoading && dash && Object.keys(dash.status_counts).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div className="fs-divider" />
            <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 6 }}>
              PIPELINE STATUS DISTRIBUTION
            </div>
            <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', gap: 1 }}>
              {Object.entries(dash.status_counts).map(([status, count]) => {
                const pct = dash.total_slides > 0 ? (count / dash.total_slides) * 100 : 0
                if (pct === 0) return null
                const bg =
                  status.includes('upload') || status === 'uploaded' ? 'var(--fs-teal)'  :
                  status.includes('fail')                            ? 'var(--fs-red)'   :
                  status.includes('qc')                              ? 'var(--fs-purple)':
                  status.includes('dicom')                           ? 'var(--fs-blue)'  :
                  status.includes('intake')                          ? 'var(--fs-cyan)'  :
                  'var(--fs-text-faint)'
                return (
                  <div
                    key={status}
                    title={`${status}: ${count}`}
                    style={{ flex: `${pct} 0 0`, background: bg, minWidth: 2 }}
                  />
                )
              })}
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 16px', marginTop: 5 }}>
              {Object.entries(dash.status_counts).slice(0, 8).map(([status, count]) => (
                <span key={status} className="fs-data-key">
                  {status.replace(/_/g, ' ')}: <span className="fs-data-val" style={{ fontSize: 10 }}>{count}</span>
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Section: Scanner Fleet ───────────────────────────────────────────────────

function ScannerFleetSection() {
  const { data, isLoading } = useCoreScanners()
  const scanners = data?.scanners ?? []

  return (
    <div className="fs-section">
      <SectionHeader
        label="SCANNER FLEET COMMAND"
        color="var(--fs-lt-blue)"
        meta={`${scanners.length} UNITS CONFIGURED`}
      />
      <div style={{ padding: '12px 16px' }}>
        {isLoading ? (
          <FsLoading label="ACCESSING SCANNER DATABASE" />
        ) : scanners.length === 0 ? (
          <FsEmpty label="NO SCANNER DATA AVAILABLE" />
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 10 }}>
            {scanners.map(sc => <FsScannerCard key={sc.scanner_id} scanner={sc} />)}
          </div>
        )}
      </div>
    </div>
  )
}

function FsScannerCard({ scanner }: { scanner: ScannerActivityItem }) {
  const state = scanner.operational_state
  const dotCls = state === 'active' ? 'fs-dot-active' : state === 'idle' ? 'fs-dot-idle' : 'fs-dot-off'
  const stateLabel = state === 'active' ? 'ACTIVE' : state === 'idle' ? 'IDLE' : 'OFFLINE'
  const stateColor =
    state === 'active' ? 'var(--fs-teal)' :
    state === 'idle'   ? 'var(--fs-yellow)' :
    'var(--fs-text-faint)'

  return (
    <div
      className="fs-scanner-card"
      data-state={state === 'no_recent_activity' ? 'inactive' : state}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 12, letterSpacing: '.08em', color: 'var(--fs-text)' }}>
          {scanner.display_name}
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span className={`fs-dot ${dotCls}`} />
          <span style={{ fontSize: 8, letterSpacing: '.16em', color: stateColor }}>{stateLabel}</span>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1 }}>
        <DataRow label="SLIDES"    value={fmtNum(scanner.total_slides)}  />
        <DataRow label="UPLOADED"  value={fmtNum(scanner.uploaded_count)}/>
        <DataRow
          label="FAILURES"
          value={fmtNum(scanner.failed_count)}
          color={scanner.failed_count > 0 ? 'var(--fs-red)' : undefined}
        />
        <DataRow label="AVG SIZE"  value={fmtBytes(scanner.avg_file_size)}/>
      </div>
      {scanner.total_slides > 0 && (
        <div style={{ marginTop: 8 }}>
          <DistBar
            value={scanner.uploaded_count}
            max={scanner.total_slides}
            color={state === 'active' ? 'var(--fs-teal)' : 'var(--fs-lt-blue)'}
          />
          <div style={{ fontSize: 8, letterSpacing: '.12em', color: 'var(--fs-text-faint)', marginTop: 3 }}>
            {((scanner.uploaded_count / scanner.total_slides) * 100).toFixed(0)}% UPLOADED
          </div>
        </div>
      )}
    </div>
  )
}

// ── Section: Upload Priority Queue ───────────────────────────────────────────

function UploadQueueSection() {
  const qc = useQueryClient()
  const { data: qData, isLoading } = useQuery({
    queryKey: ['uploads', 'queue', 'fs-view'],
    queryFn: () => fetchUploadQueue({ page_size: 20 }),
    staleTime: 15_000,
    retry: 1,
  })
  const pm = useMutation({
    mutationFn: ({ id, priority }: { id: number; priority: number }) =>
      patchUploadPriority(id, { priority }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['uploads', 'queue'] }),
  })

  const allItems = qData?.items ?? []
  const activeItems = allItems.filter(i => i.upload_status === 'uploading')
  const pendingItems = allItems.filter(
    i => !TERMINAL_STATUSES.has(i.upload_status) && i.upload_status !== 'uploading'
  )
  const visibleItems = [...activeItems, ...pendingItems].slice(0, 15)
  const totalCount = qData?.total ?? 0

  return (
    <div className="fs-section">
      <SectionHeader
        label="UPLOAD PRIORITY QUEUE"
        color="var(--fs-orange)"
        meta={`${totalCount} TOTAL — ${activeItems.length} UPLOADING`}
      />
      <div style={{ paddingBottom: 4 }}>
        {isLoading ? (
          <div style={{ padding: '12px 16px' }}>
            <FsLoading label="ACCESSING UPLOAD QUEUE" />
          </div>
        ) : visibleItems.length === 0 ? (
          <div style={{ padding: '12px 16px' }}>
            <FsEmpty label="UPLOAD QUEUE IS EMPTY" />
          </div>
        ) : (
          visibleItems.map(item => {
            const badge   = prioBadge(item)
            const isTerminal  = TERMINAL_STATUSES.has(item.upload_status)
            const isUploading = item.upload_status === 'uploading'
            const isPending   = pm.isPending && (pm.variables as { id: number } | undefined)?.id === item.id
            const rowCls =
              isUploading           ? 'fs-queue-row prio-uploading' :
              item.priority === PRIO_NEXT && !isTerminal ? 'fs-queue-row prio-next' :
              'fs-queue-row'

            return (
              <div key={item.id} className={rowCls}>
                {/* Priority stripe */}
                <div style={{
                  width: 3, height: 30, borderRadius: 2, flexShrink: 0,
                  background:
                    isUploading              ? 'var(--fs-teal)' :
                    item.priority === PRIO_NEXT ? 'var(--fs-orange)' :
                    item.priority >= PRIO_LOW   ? 'var(--fs-text-faint)' :
                    'var(--fs-lt-blue)',
                }} />

                {/* File info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 11, color: 'var(--fs-text)', letterSpacing: '.04em',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {item.filename.length > 32 ? `…${item.filename.slice(-29)}` : item.filename}
                  </div>
                  <div style={{ fontSize: 9, color: 'var(--fs-text-faint)', letterSpacing: '.11em', marginTop: 1 }}>
                    {item.scanner_id ?? '—'}
                    {item.file_size_bytes ? ` · ${fmtBytes(item.file_size_bytes)}` : ''}
                  </div>
                </div>

                {/* Badge */}
                <span className={`fs-badge ${badge.cls}`}>{badge.label}</span>

                {/* Actions */}
                {!isTerminal && (
                  <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                    {isPending ? (
                      <span style={{ fontSize: 9, color: 'var(--fs-text-faint)', letterSpacing: '.12em' }}>
                        UPDATING…
                      </span>
                    ) : (
                      <>
                        {!isUploading && item.priority !== PRIO_NEXT && (
                          <button
                            className="fs-btn fs-btn-orange"
                            onClick={() => pm.mutate({ id: item.id, priority: PRIO_NEXT })}
                            title="Mark as Upload Next"
                          >
                            NEXT
                          </button>
                        )}
                        {!isUploading && item.priority !== PRIO_NORMAL && (
                          <button
                            className="fs-btn fs-btn-blue"
                            onClick={() => pm.mutate({ id: item.id, priority: PRIO_NORMAL })}
                            title="Reset to Normal Priority"
                          >
                            RESET
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

// ── Section: Upload Velocity ─────────────────────────────────────────────────

function VelocitySection() {
  const { data, isLoading } = useCoreUploads()

  return (
    <div className="fs-section">
      <SectionHeader label="UPLOAD VELOCITY" color="var(--fs-teal)" />
      <div style={{ padding: '12px 16px' }}>
        {isLoading ? <FsLoading label="QUERYING UPLOAD SUBSYSTEMS" /> : (
          <>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
              <Metric
                value={data?.avg_speed_mbps != null ? `${data.avg_speed_mbps} Mb/s` : '—'}
                label="AVG UPLOAD SPEED"
                color="var(--fs-teal)"
                size="lg"
              />
              <Metric
                value={fmtDuration(data?.avg_duration_seconds)}
                label="AVG DURATION"
                color="var(--fs-cyan)"
                size="lg"
              />
            </div>
            <div className="fs-divider" />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginBottom: 12 }}>
              <Metric value={fmtNum(data?.completed_total)} label="COMPLETED"    color="var(--fs-teal)" size="sm" />
              <Metric
                value={fmtNum(data?.failed_total)}
                label="FAILED"
                color={data?.failed_total ? 'var(--fs-red)' : 'var(--fs-text-faint)'}
                size="sm"
              />
              <Metric value={fmtNum(data?.total_retries)} label="RETRIES" size="sm" />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 10 }}>
              <DataRow label="QUEUE DEPTH" value={fmtNum(data?.queue_depth)} />
              <DataRow
                label="DELAYED"
                value={fmtNum(data?.delayed_count)}
                color={data?.delayed_count ? 'var(--fs-orange)' : undefined}
              />
            </div>
            {data?.daily_uploads_7d && data.daily_uploads_7d.length > 0 && (
              <>
                <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 6 }}>
                  7-DAY UPLOAD ACTIVITY
                </div>
                <MiniBarChart data={data.daily_uploads_7d} color="var(--fs-teal)" />
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

// ── Section: Stain Analytics ─────────────────────────────────────────────────

const STAIN_COLORS: Record<string, string> = {
  'H&E':    'var(--fs-orange)',
  'HE':     'var(--fs-orange)',
  'IHC':    'var(--fs-purple)',
  'PAS':    'var(--fs-cyan)',
  'EVG':    'var(--fs-teal)',
  'Masson': 'var(--fs-blue)',
  'Unknown':'var(--fs-text-faint)',
}

function stainColor(stain: string): string {
  for (const [key, c] of Object.entries(STAIN_COLORS)) {
    if (stain.toUpperCase().includes(key.toUpperCase())) return c
  }
  return 'var(--fs-lt-blue)'
}

function StainsSection() {
  const { data, isLoading } = useCoreStains()
  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="fs-section">
      <SectionHeader
        label="STAIN ANALYTICS MATRIX"
        color="var(--fs-purple)"
        meta={total ? `${fmtNum(total)} SLIDES` : undefined}
      />
      <div style={{ padding: '12px 16px' }}>
        {isLoading ? <FsLoading label="ANALYZING STAIN DATABASE" /> :
         items.length === 0 ? <FsEmpty label="NO STAIN DATA AVAILABLE" /> : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {items.slice(0, 10).map(item => {
              const color = stainColor(item.stain_type)
              return (
                <div key={item.stain_type}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                    <span style={{ fontSize: 11, color, letterSpacing: '.08em' }}>{item.stain_type}</span>
                    <div style={{ display: 'flex', gap: 12 }}>
                      <span style={{ fontSize: 11, color, letterSpacing: '.06em' }}>{fmtNum(item.count)}</span>
                      <span style={{ fontSize: 10, color: 'var(--fs-text-dim)', minWidth: 44, textAlign: 'right' }}>
                        {fmtPct(item.percentage)}
                      </span>
                    </div>
                  </div>
                  <DistBar value={item.count} max={total} color={color} />
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Section: Recovery Matrix ─────────────────────────────────────────────────

function folderLaneColor(label: string): string {
  const l = label.toLowerCase()
  if (l.includes('fail'))   return 'var(--fs-red)'
  if (l.includes('suspi'))  return 'var(--fs-orange)'
  if (l.includes('manual') || l.includes('review')) return 'var(--fs-yellow)'
  return 'var(--fs-text-dim)'
}

function outcomeColor(outcome: string): string {
  const l = outcome.toLowerCase()
  if (l.includes('auto') || l.includes('recover')) return 'var(--fs-teal)'
  if (l.includes('manual'))                         return 'var(--fs-orange)'
  if (l.includes('unresolved') || l.includes('fail')) return 'var(--fs-red)'
  return 'var(--fs-text-dim)'
}

function RecoverySection() {
  const { data, isLoading } = useCoreRecovery()

  const folderEntries = Object.entries(data?.by_folder ?? {}).sort((a, b) => b[1] - a[1])
  const maxFolder     = Math.max(...folderEntries.map(([, v]) => v), 1)
  const reviewEntries = Object.entries(data?.by_review_status ?? {})
  const outcomeEntries = Object.entries(data?.by_outcome ?? {}).sort((a, b) => b[1] - a[1])
  const rate = data?.recovery_rate ?? 0
  const rateColor = rate >= 80 ? 'var(--fs-teal)' : rate >= 50 ? 'var(--fs-yellow)' : 'var(--fs-red)'

  return (
    <div className="fs-section">
      <SectionHeader
        label="RECOVERY MATRIX"
        color="var(--fs-red)"
        meta={rate > 0 ? `${fmtPct(rate)} RESOLUTION RATE` : undefined}
      />
      <div style={{ padding: '12px 16px' }}>
        {isLoading ? <FsLoading label="SCANNING RECOVERY SUBSYSTEMS" /> : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 20 }}>

            {/* Column 1 — Folder health */}
            <div>
              <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 8 }}>
                MONITORED FOLDER HEALTH
              </div>
              {folderEntries.length === 0 ? (
                <FsEmpty label="NO FOLDER DATA" />
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {folderEntries.map(([label, count]) => {
                    const c = folderLaneColor(label)
                    return (
                      <div key={label}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                          <span style={{ fontSize: 9, color: c, letterSpacing: '.10em' }}>
                            {label.replace(/_/g, ' ').toUpperCase()}
                          </span>
                          <span style={{ fontSize: 11, color: c }}>{count}</span>
                        </div>
                        <DistBar value={count} max={maxFolder} color={c} />
                      </div>
                    )
                  })}
                </div>
              )}
              <div className="fs-divider" />
              <DataRow label="TOTAL MONITORED"  value={fmtNum(data?.total_monitored)} />
              <DataRow label="MANUAL REVIEW"    value={fmtNum(data?.manual_review_count)} />
              <DataRow label="RECENT (7D)"       value={fmtNum(data?.recent_7d)} />
            </div>

            {/* Column 2 — Review status + outcomes */}
            <div>
              <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 8 }}>
                TECHNICIAN REVIEW STATUS
              </div>
              {reviewEntries.length === 0 ? (
                <FsEmpty label="NO REVIEW DATA" />
              ) : reviewEntries.map(([status, count]) => (
                <DataRow
                  key={status}
                  label={status.replace(/_/g, ' ').toUpperCase()}
                  value={String(count)}
                  color={
                    status === 'reviewed' ? 'var(--fs-teal)' :
                    status === 'pending'  ? 'var(--fs-yellow)' :
                    undefined
                  }
                />
              ))}

              <div className="fs-divider" />

              <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 8 }}>
                RESOLUTION OUTCOMES
              </div>
              {outcomeEntries.length === 0 ? (
                <FsEmpty label="NO OUTCOME DATA" />
              ) : outcomeEntries.map(([outcome, count]) => (
                <DataRow
                  key={outcome}
                  label={outcome.replace(/_/g, ' ').toUpperCase()}
                  value={String(count)}
                  color={outcomeColor(outcome)}
                />
              ))}
            </div>

            {/* Column 3 — Rate display + resolution totals */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '12px 0' }}>
                <div
                  style={{
                    border: `2px solid ${rateColor}`,
                    borderRadius: '50%',
                    width: 82,
                    height: 82,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 1,
                  }}
                >
                  <span style={{ fontSize: 20, color: rateColor, lineHeight: 1, letterSpacing: '-.02em' }}>
                    {rate.toFixed(0)}%
                  </span>
                  <span style={{ fontSize: 8, letterSpacing: '.15em', color: 'var(--fs-text-faint)' }}>
                    RATE
                  </span>
                </div>
              </div>
              <DataRow label="TOTAL CHANGES"   value={fmtNum(data?.total_changes)} />
              <DataRow label="TOTAL RESOLVED"  value={fmtNum(data?.total_resolved)} color="var(--fs-teal)" />
              <DataRow
                label="AUTO RECOVERED"
                value={fmtNum(data?.auto_recovered)}
                color={data?.auto_recovered ? 'var(--fs-teal)' : undefined}
              />
              <DataRow label="MANUAL REQUIRED" value={fmtNum(data?.manual_review_required)} />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Section: Storage Core ────────────────────────────────────────────────────

function StorageSection() {
  const { data, isLoading } = useCoreStorage()
  const byScanners = data?.by_scanner ?? []
  const totalBytes = data?.total_bytes ?? 1

  return (
    <div className="fs-section">
      <SectionHeader label="STORAGE CORE" color="var(--fs-cyan)" />
      <div style={{ padding: '12px 16px' }}>
        {isLoading ? <FsLoading label="CALCULATING STORAGE MATRIX" /> : (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
            {/* Global */}
            <div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10, marginBottom: 12 }}>
                <Metric value={fmtBytes(data?.total_bytes)} label="TOTAL PROCESSED" color="var(--fs-cyan)"        size="sm" />
                <Metric value={fmtBytes(data?.avg_bytes)}   label="AVG SLIDE SIZE"                                 size="sm" />
                <Metric value={fmtNum(data?.total_slides_with_size)} label="WITH SIZE DATA"                        size="sm" />
              </div>
              <DataRow label="LARGEST SLIDE"  value={fmtBytes(data?.max_bytes)} />
              <DataRow label="SMALLEST SLIDE" value={fmtBytes(data?.min_bytes)} />
              <DataRow label="UPLOADED TODAY" value={fmtBytes(data?.uploaded_today_bytes)} color="var(--fs-cyan)" />
            </div>

            {/* Per-scanner */}
            <div>
              <div style={{ fontSize: 9, letterSpacing: '.15em', color: 'var(--fs-text-faint)', marginBottom: 8 }}>
                STORAGE BY SCANNER
              </div>
              {byScanners.length === 0 ? (
                <FsEmpty label="NO PER-SCANNER DATA" />
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {byScanners.slice(0, 6).map((s: StorageScannerItem) => (
                    <div key={s.scanner_id}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                        <span style={{ fontSize: 10, color: 'var(--fs-text-dim)', letterSpacing: '.07em' }}>
                          {s.scanner_id}
                        </span>
                        <span style={{ fontSize: 11, color: 'var(--fs-text)' }}>{fmtBytes(s.total_bytes)}</span>
                      </div>
                      <DistBar value={s.total_bytes} max={totalBytes} color="var(--fs-cyan)" />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Left navigation rail ─────────────────────────────────────────────────────

function FsLeftRail({ active, onSelect }: { active: string; onSelect: (id: string) => void }) {
  return (
    <div className="fs-rail-col">
      {RAIL_SECTIONS.map(s => (
        <button
          key={s.id}
          type="button"
          className={`fs-nav-block${active === s.id ? ' active' : ''}`}
          style={{ background: s.color }}
          onClick={() => onSelect(s.id)}
        >
          {s.label}
        </button>
      ))}
      <div className="fs-rail-spacer" />
      <div className="fs-rail-deco" style={{ background: 'var(--fs-yellow)',  height: 18 }} />
      <div className="fs-rail-deco" style={{ background: 'var(--fs-orange)',  height: 18 }} />
      <div className="fs-rail-deco" style={{ background: 'var(--fs-pale)',    height: 24 }} />
    </div>
  )
}

// ── Bottom status bar ────────────────────────────────────────────────────────

function FsStatusBar() {
  const { data: ov }  = useCoreOverview()
  const { data: sc }  = useCoreScanners()
  const clock = useClock()

  const activeUploads  = ov?.active_uploads  ?? 0
  const failedSlides   = ov?.failed_slides   ?? 0
  const queueDepth     = ov?.queued_uploads  ?? 0
  const activeScanners = (sc?.scanners ?? []).filter(s => s.operational_state === 'active').length

  return (
    <div className="fs-statusbar">
      <span className="fs-sb-item">
        <span className="fs-blink">●</span> SYSTEMS ONLINE
      </span>
      <div className="fs-sb-sep" />
      <span className="fs-sb-item">STARDATE: <strong>{clock}</strong></span>
      <div className="fs-sb-sep" />
      <span className="fs-sb-item">
        UPLOADS:{' '}
        <strong style={activeUploads ? { color: 'rgba(51,204,153,.9)' } : undefined}>
          {activeUploads} ACTIVE
        </strong>
      </span>
      <div className="fs-sb-sep" />
      <span className="fs-sb-item">QUEUE: <strong>{queueDepth}</strong></span>
      <div className="fs-sb-sep" />
      <span
        className="fs-sb-item"
        style={failedSlides > 0 ? { animation: 'fs-alert .85s step-end infinite' } : undefined}
      >
        FAILURES:{' '}
        <strong style={failedSlides > 0 ? { color: 'rgba(255,51,102,.9)' } : undefined}>
          {failedSlides}
        </strong>
      </span>
      <div className="fs-sb-sep" />
      <span className="fs-sb-item">SCANNERS: <strong>{activeScanners} ACTIVE</strong></span>
      <div style={{ flex: 1 }} />
      <span className="fs-sb-item">PIPELINE: <strong>OPERATIONAL</strong></span>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export function ComputerCoreFullscreen() {
  const navigate = useNavigate()
  const [active, setActive] = useState('overview')

  const scrollTo = (id: string) => {
    setActive(id)
    document.getElementById(`fs-sec-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="lcars-fs">
      {/* Atmospheric overlay */}
      <div className="fs-scanlines" aria-hidden />

      {/* Header */}
      <div className="fs-header-row">
        <div className="fs-elbow">
          <span className="fs-elbow-brand">PALANTIR</span>
        </div>
        <div className="fs-header-strip">
          <span className="fs-header-title">COMPUTER CORE</span>
          <div className="fs-header-sep" />
          <span className="fs-header-sub">PATHOLOGY OPERATIONS CONSOLE</span>
          <div className="fs-header-spacer" />
          <span className="fs-header-badge fs-blink">● SYSTEMS ONLINE</span>
          <div className="fs-header-sep" />
          <button className="fs-exit-btn" type="button" onClick={() => navigate('/computer-core')}>
            ← EXIT
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="fs-body-row">
        <FsLeftRail active={active} onSelect={scrollTo} />
        <div className="fs-content-wrap">
          <div style={{ padding: '20px 24px 32px 20px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div id="fs-sec-overview"><OverviewSection /></div>
              <div id="fs-sec-scanners"><ScannerFleetSection /></div>
              <div id="fs-sec-queue"><UploadQueueSection /></div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                <div id="fs-sec-velocity"><VelocitySection /></div>
                <div id="fs-sec-stains"><StainsSection /></div>
              </div>
              <div id="fs-sec-recovery"><RecoverySection /></div>
              <div id="fs-sec-storage"><StorageSection /></div>
            </div>
          </div>
        </div>
      </div>

      {/* Status bar */}
      <FsStatusBar />
    </div>
  )
}
