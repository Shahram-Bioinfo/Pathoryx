/**
 * ComputerCore — Phase 4.4
 * Palantir Computer Core: LCARS-inspired operational command console.
 *
 * Full-bleed LCARS layout with:
 *   - Elbow + header strip across the top
 *   - Left rail with colored LCARS nav blocks
 *   - Segmented content panels for each operational domain
 *
 * All colour and layout tokens live in index.css under .lcars-core.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  AreaChart, Area, XAxis, RadialBarChart, RadialBar,
} from 'recharts'
import {
  useCoreOverview,
  useCoreRecovery,
  useCoreScanners,
  useCoreStains,
  useCoreStorage,
  useCoreUploads,
} from '../hooks/useComputerCore'
import type {
  ScannerActivityItem,
  StainDistributionItem,
  StorageScannerItem,
} from '../types/api'
import { useTheme } from '../components/layout/ThemeProvider'

// ── Utility formatters ──────────────────────────────────────────────────────

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

function fmtPct(n: number): string {
  return `${n.toFixed(1)} %`
}

function fmtDuration(secs: number | null | undefined): string {
  if (secs == null) return '—'
  if (secs < 60)  return `${Math.round(secs)} s`
  const m = Math.floor(secs / 60)
  const s = Math.round(secs % 60)
  return `${m}m ${s}s`
}

function fmtDay(iso: string | null): string {
  if (!iso) return '?'
  try { return iso.slice(5, 10) } catch { return '?' }  // MM-DD
}

// ── LCARS primitives ────────────────────────────────────────────────────────

/** Thick colored pill header on a section panel */
function SectionHeader({
  label,
  color,
  children,
}: {
  label: string
  color: string
  children?: React.ReactNode
}) {
  return (
    <div className="lc-section-header">
      <div className="lc-section-pill" style={{ background: color }}>
        <span
          className="lc-section-title"
          style={{ color: 'var(--lc-bg)', letterSpacing: '0.18em' }}
        >
          {label}
        </span>
      </div>
      {children && (
        <div className="flex-1 flex items-center justify-end px-3">
          {children}
        </div>
      )}
    </div>
  )
}

/** Large operational number display */
function LCARSMetric({
  value,
  label,
  color,
  size = 'md',
}: {
  value: string | number
  label: string
  color?: string
  size?: 'lg' | 'md' | 'sm'
}) {
  return (
    <div className="lc-metric">
      <span
        className={`lc-metric-value ${size}`}
        style={color ? { color } : undefined}
      >
        {value ?? '—'}
      </span>
      <span className="lc-metric-label">{label}</span>
    </div>
  )
}

/** Horizontal distribution bar */
function DistBar({
  value,
  max,
  color,
}: {
  value: number
  max: number
  color: string
}) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0
  return (
    <div className="lc-dist-bar">
      <div
        className="lc-dist-bar-fill"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  )
}

/** Mini bar chart for 7-day uploads */
function DailyBarChart({
  data,
  color,
}: {
  data: Array<{ day: string | null; count: number }>
  color: string
}) {
  const max = Math.max(...data.map(d => d.count), 1)
  return (
    <div className="lc-bar-chart">
      {data.map((d, i) => (
        <div key={i} className="lc-bar-chart-col">
          <div
            className="lc-bar-chart-bar"
            style={{
              height: `${Math.max((d.count / max) * 40, 2)}px`,
              background: color,
              opacity: 0.75 + (i / data.length) * 0.25,
            }}
            title={`${fmtDay(d.day)}: ${d.count}`}
          />
          <span className="lc-bar-chart-label">{fmtDay(d.day)}</span>
        </div>
      ))}
    </div>
  )
}

/** Status dot */
function StatusDot({ state }: { state: string }) {
  const cls =
    state === 'active'             ? 'active' :
    state === 'idle'               ? 'idle' :
    'inactive'
  return <span className={`lc-status-dot ${cls}`} />
}

/** key→value data row */
function DataRow({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="lc-data-row">
      <span className="lc-data-key">{label}</span>
      <span className="lc-data-val" style={color ? { color } : undefined}>{value}</span>
    </div>
  )
}

// ── Left rail ───────────────────────────────────────────────────────────────

const RAIL_SECTIONS = [
  { id: 'status',    label: 'STATUS',    color: 'var(--lc-orange)' },
  { id: 'scanners',  label: 'SCANNERS',  color: 'var(--lc-lt-blue)' },
  { id: 'stains',    label: 'STAINS',    color: 'var(--lc-purple)' },
  { id: 'uploads',   label: 'UPLOADS',   color: 'var(--lc-teal)' },
  { id: 'recovery',  label: 'RECOVERY',  color: 'var(--lc-red)' },
  { id: 'storage',   label: 'STORAGE',   color: 'var(--lc-cyan)' },
]

function LeftRail({
  active,
  onSelect,
}: {
  active: string
  onSelect: (id: string) => void
}) {
  return (
    <div className="lc-rail" style={{ width: 'var(--lc-rail-w)', flexShrink: 0 }}>
      {RAIL_SECTIONS.map(s => (
        <button
          key={s.id}
          type="button"
          className={`lc-rail-block${active === s.id ? ' active' : ''}`}
          style={{ background: s.color, color: s.color }}
          onClick={() => onSelect(s.id)}
        >
          <span className="lc-rail-block-label">{s.label}</span>
        </button>
      ))}
      <div className="lc-rail-spacer" style={{ background: 'var(--lc-orange)', opacity: 0.3 }} />
      {/* Bottom decorative blocks */}
      <div className="lc-rail-block" style={{ background: 'var(--lc-yellow)', minHeight: 20, cursor: 'default' }} />
      <div className="lc-rail-block" style={{ background: 'var(--lc-orange)', minHeight: 20, cursor: 'default' }} />
      <div className="lc-rail-block" style={{ background: 'var(--lc-pale)', minHeight: 28, cursor: 'default' }} />
    </div>
  )
}

// ── Section: Computer Core Status ───────────────────────────────────────────

function StatusSection() {
  const { data, isLoading } = useCoreOverview()

  const loading = (label: string) => (
    <LCARSMetric value="—" label={label} size="md" color="var(--lc-text-faint)" />
  )

  return (
    <div className="lc-section">
      <SectionHeader label="COMPUTER CORE STATUS" color="var(--lc-orange)" />
      <div className="p-4">
        {/* Primary metrics row */}
        <div
          className="grid gap-5 mb-5"
          style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}
        >
          {isLoading ? (
            ['TOTAL SLIDES', 'UPLOADED TODAY', 'ACTIVE UPLOADS', 'QUEUE DEPTH'].map(l => loading(l))
          ) : (
            <>
              <LCARSMetric value={fmtNum(data?.total_slides)} label="TOTAL SLIDES" size="lg" color="var(--lc-orange)" />
              <LCARSMetric value={fmtNum(data?.uploaded_today)} label="UPLOADED TODAY" size="lg" color="var(--lc-teal)" />
              <LCARSMetric value={fmtNum(data?.active_uploads)} label="ACTIVE UPLOADS" size="lg"
                color={data?.active_uploads ? 'var(--lc-cyan)' : 'var(--lc-text-faint)'}
              />
              <LCARSMetric value={fmtNum(data?.queued_uploads)} label="QUEUE DEPTH" size="lg" color="var(--lc-blue)" />
            </>
          )}
        </div>

        <div className="lc-divider" />

        {/* Secondary metrics */}
        <div className="grid gap-5" style={{ gridTemplateColumns: 'repeat(5, 1fr)' }}>
          {isLoading ? (
            ['SLIDES TODAY', 'FAILED', 'DELAYED', 'RECOVERY BACKLOG', 'UNREVIEWED'].map(l => loading(l))
          ) : (
            <>
              <LCARSMetric value={fmtNum(data?.slides_today)} label="SLIDES TODAY" size="sm" />
              <LCARSMetric
                value={fmtNum(data?.failed_slides)}
                label="PIPELINE FAILURES"
                size="sm"
                color={data?.failed_slides ? 'var(--lc-red)' : 'var(--lc-text-faint)'}
              />
              <LCARSMetric
                value={fmtNum(data?.delayed_uploads)}
                label="DELAYED UPLOADS"
                size="sm"
                color={data?.delayed_uploads ? 'var(--lc-orange)' : 'var(--lc-text-faint)'}
              />
              <LCARSMetric value={fmtNum(data?.recovery_backlog)} label="RECOVERY BACKLOG" size="sm" />
              <LCARSMetric
                value={fmtNum(data?.unreviewed_changes)}
                label="UNREVIEWED CHANGES"
                size="sm"
                color={data?.unreviewed_changes ? 'var(--lc-yellow)' : 'var(--lc-text-faint)'}
              />
            </>
          )}
        </div>

        {/* Status distribution band */}
        {!isLoading && data && Object.keys(data.status_counts).length > 0 && (
          <div className="mt-5">
            <div className="lc-divider" />
            <div className="lc-metric-label mb-2">PIPELINE STATUS DISTRIBUTION</div>
            <div className="flex gap-1.5 h-3 rounded overflow-hidden">
              {Object.entries(data.status_counts).map(([status, count]) => {
                const pct = data.total_slides > 0 ? (count / data.total_slides) * 100 : 0
                const color =
                  status.includes('upload') || status === 'uploaded' ? 'var(--lc-teal)' :
                  status.includes('failed') ? 'var(--lc-red)' :
                  status.includes('qc') ? 'var(--lc-purple)' :
                  status.includes('dicom') ? 'var(--lc-blue)' :
                  status.includes('intake') ? 'var(--lc-cyan)' :
                  'var(--lc-text-faint)'
                return (
                  <div
                    key={status}
                    title={`${status}: ${count}`}
                    style={{
                      flex: `${pct} 0 0`,
                      background: color,
                      borderRadius: 2,
                      minWidth: pct > 0 ? 2 : 0,
                    }}
                  />
                )
              })}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
              {Object.entries(data.status_counts).slice(0, 8).map(([status, count]) => (
                <span key={status} className="lc-data-key">
                  {status.replace(/_/g, ' ')}: <span className="lc-data-val" style={{ fontSize: 10 }}>{count}</span>
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

function ScannersSection() {
  const { data, isLoading } = useCoreScanners()

  const scanners = data?.scanners ?? []

  return (
    <div className="lc-section">
      <SectionHeader label="SCANNER FLEET OPERATIONS" color="var(--lc-lt-blue)">
        <span className="lc-data-key">{scanners.length} UNITS CONFIGURED</span>
      </SectionHeader>
      <div className="p-4">
        {isLoading ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            ACCESSING SCANNER DATABASE...
          </div>
        ) : scanners.length === 0 ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            NO SCANNER DATA AVAILABLE
          </div>
        ) : (
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
            {scanners.map(sc => (
              <ScannerCard key={sc.scanner_id} scanner={sc} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function ScannerCard({ scanner }: { scanner: ScannerActivityItem }) {
  const state = scanner.operational_state
  const stateLabel =
    state === 'active'             ? 'ACTIVE RECENTLY' :
    state === 'idle'               ? 'IDLE' :
    'NO RECENT ACTIVITY'
  const stateColor =
    state === 'active'  ? 'var(--lc-teal)' :
    state === 'idle'    ? 'var(--lc-yellow)' :
    'var(--lc-text-faint)'

  return (
    <div className="lc-scanner-card" data-state={state === 'no_recent_activity' ? 'inactive' : state}>
      <div className="flex items-center justify-between mb-2">
        <span
          className="lc-data-val"
          style={{ fontSize: 13, letterSpacing: '0.08em' }}
        >
          {scanner.display_name}
        </span>
        <div className="flex items-center gap-1.5">
          <StatusDot state={state === 'no_recent_activity' ? 'inactive' : state} />
          <span style={{ fontSize: 9, letterSpacing: '0.18em', color: stateColor }}>
            {stateLabel}
          </span>
        </div>
      </div>

      <div className="grid gap-x-3 gap-y-1" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <DataRow label="SLIDES" value={fmtNum(scanner.total_slides)} />
        <DataRow label="UPLOADED" value={fmtNum(scanner.uploaded_count)} />
        <DataRow
          label="FAILURES"
          value={fmtNum(scanner.failed_count)}
          color={scanner.failed_count > 0 ? 'var(--lc-red)' : undefined}
        />
        <DataRow label="AVG SIZE" value={fmtBytes(scanner.avg_file_size)} />
        {scanner.avg_upload_speed_mbps != null && (
          <DataRow label="AVG SPEED" value={`${scanner.avg_upload_speed_mbps} Mb/s`} />
        )}
      </div>

      {scanner.total_slides > 0 && (
        <div className="mt-2">
          <DistBar
            value={scanner.uploaded_count}
            max={scanner.total_slides}
            color={state === 'active' ? 'var(--lc-teal)' : 'var(--lc-lt-blue)'}
          />
          <div className="lc-metric-label mt-1">
            {scanner.total_slides > 0
              ? `${((scanner.uploaded_count / scanner.total_slides) * 100).toFixed(0)} % UPLOADED`
              : 'NO SLIDES'}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Section: Stain Analysis ──────────────────────────────────────────────────

const STAIN_COLORS: Record<string, string> = {
  'H&E':       'var(--lc-orange)',
  'HE':        'var(--lc-orange)',
  'IHC':       'var(--lc-purple)',
  'PAS':       'var(--lc-cyan)',
  'EVG':       'var(--lc-teal)',
  'Masson':    'var(--lc-blue)',
  'Unknown':   'var(--lc-text-faint)',
}

function stainColor(stain: string): string {
  for (const [key, color] of Object.entries(STAIN_COLORS)) {
    if (stain.toUpperCase().includes(key.toUpperCase())) return color
  }
  return 'var(--lc-lt-blue)'
}

function StainsSection() {
  const { data, isLoading } = useCoreStains()
  const items = data?.items ?? []
  const total = data?.total ?? 0

  return (
    <div className="lc-section">
      <SectionHeader label="STAIN ANALYSIS CORE" color="var(--lc-purple)">
        <span className="lc-data-key">{total > 0 ? `${fmtNum(total)} SLIDES ANALYZED` : ''}</span>
      </SectionHeader>
      <div className="p-4">
        {isLoading ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            ANALYZING STAIN DATABASE...
          </div>
        ) : items.length === 0 ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            NO STAIN DATA AVAILABLE
          </div>
        ) : (
          <div className="space-y-2">
            {items.slice(0, 12).map(item => (
              <StainRow key={item.stain_type} item={item} total={total} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function StainRow({ item, total }: { item: StainDistributionItem; total: number }) {
  const color = stainColor(item.stain_type)
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span
          className="lc-data-val"
          style={{ fontSize: 11, letterSpacing: '0.08em', color }}
        >
          {item.stain_type}
        </span>
        <div className="flex items-center gap-3">
          <span className="lc-metric-label" style={{ color }}>
            {fmtNum(item.count)}
          </span>
          <span className="lc-data-val" style={{ fontSize: 11, color: 'var(--lc-text-dim)', minWidth: 44, textAlign: 'right' }}>
            {fmtPct(item.percentage)}
          </span>
        </div>
      </div>
      <DistBar value={item.count} max={total} color={color} />
    </div>
  )
}

// ── Section: Upload Velocity ─────────────────────────────────────────────────

function UploadsSection() {
  const { data, isLoading } = useCoreUploads()

  return (
    <div className="lc-section">
      <SectionHeader label="UPLOAD VELOCITY" color="var(--lc-teal)" />
      <div className="p-4">
        {isLoading ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            QUERYING UPLOAD SUBSYSTEMS...
          </div>
        ) : (
          <>
            {/* Primary velocity metrics */}
            <div className="grid gap-4 mb-4" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
              <LCARSMetric
                value={data?.avg_speed_mbps != null ? `${data.avg_speed_mbps} Mb/s` : '—'}
                label="AVG UPLOAD SPEED"
                size="md"
                color="var(--lc-teal)"
              />
              <LCARSMetric
                value={fmtDuration(data?.avg_duration_seconds)}
                label="AVG DURATION"
                size="md"
                color="var(--lc-cyan)"
              />
              <LCARSMetric
                value={fmtNum(data?.completed_total)}
                label="TOTAL COMPLETED"
                size="md"
                color="var(--lc-teal)"
              />
              <LCARSMetric
                value={fmtNum(data?.failed_total)}
                label="TOTAL FAILED"
                size="md"
                color={data?.failed_total ? 'var(--lc-red)' : 'var(--lc-text-faint)'}
              />
            </div>

            <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr' }}>
              {/* Queue state */}
              <div>
                <div className="lc-metric-label mb-3">QUEUE STATE</div>
                <div className="space-y-0">
                  <DataRow label="QUEUE DEPTH" value={fmtNum(data?.queue_depth)} />
                  <DataRow
                    label="DELAYED"
                    value={fmtNum(data?.delayed_count)}
                    color={data?.delayed_count ? 'var(--lc-orange)' : undefined}
                  />
                  <DataRow label="TOTAL RETRIES" value={fmtNum(data?.total_retries)} />
                </div>
              </div>

              {/* 7-day activity */}
              <div>
                <div className="lc-metric-label mb-3">7-DAY UPLOAD ACTIVITY</div>
                {data?.daily_uploads_7d && data.daily_uploads_7d.length > 0 ? (
                  <DailyBarChart data={data.daily_uploads_7d} color="var(--lc-teal)" />
                ) : (
                  <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
                    NO UPLOAD HISTORY
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Section: Recovery Matrix ─────────────────────────────────────────────────

function RecoverySection() {
  const { data, isLoading } = useCoreRecovery()

  return (
    <div className="lc-section">
      <SectionHeader label="RECOVERY MATRIX" color="var(--lc-red)">
        {!isLoading && data && data.recovery_rate > 0 && (
          <span
            style={{ fontSize: 13, color: 'var(--lc-teal)', letterSpacing: '0.12em' }}
          >
            {fmtPct(data.recovery_rate)} RESOLUTION RATE
          </span>
        )}
      </SectionHeader>
      <div className="p-4">
        {isLoading ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            SCANNING RECOVERY SUBSYSTEMS...
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
            {/* Folder counts */}
            <div>
              <div className="lc-metric-label mb-3">MONITORED FOLDERS</div>
              <div className="space-y-0">
                <DataRow label="TOTAL MONITORED" value={fmtNum(data?.total_monitored)} />
                <DataRow
                  label="FAILED LANE"
                  value={fmtNum(data?.failed_count)}
                  color={data?.failed_count ? 'var(--lc-red)' : undefined}
                />
                <DataRow
                  label="SUSPICIOUS LANE"
                  value={fmtNum(data?.suspicious_count)}
                  color={data?.suspicious_count ? 'var(--lc-orange)' : undefined}
                />
                <DataRow
                  label="MANUAL REVIEW"
                  value={fmtNum(data?.manual_review_count)}
                  color={data?.manual_review_count ? 'var(--lc-yellow)' : undefined}
                />
              </div>
            </div>

            {/* Resolution outcomes */}
            <div>
              <div className="lc-metric-label mb-3">RESOLUTION OUTCOMES</div>
              <div className="space-y-0">
                <DataRow
                  label="AUTO RECOVERED"
                  value={fmtNum(data?.auto_recovered)}
                  color={data?.auto_recovered ? 'var(--lc-teal)' : undefined}
                />
                <DataRow label="MANUAL REQUIRED" value={fmtNum(data?.manual_review_required)} />
                <DataRow label="TOTAL CHANGES" value={fmtNum(data?.total_changes)} />
                <DataRow label="TOTAL RESOLVED" value={fmtNum(data?.total_resolved)} />
              </div>
            </div>

            {/* Metrics */}
            <div>
              <div className="lc-metric-label mb-3">OPERATIONAL METRICS</div>
              <div className="mb-3">
                <LCARSMetric
                  value={fmtPct(data?.recovery_rate ?? 0)}
                  label="RECOVERY RATE"
                  size="md"
                  color={
                    (data?.recovery_rate ?? 0) >= 80 ? 'var(--lc-teal)' :
                    (data?.recovery_rate ?? 0) >= 50 ? 'var(--lc-yellow)' :
                    'var(--lc-red)'
                  }
                />
              </div>
              <DataRow label="RECENT (7d)" value={fmtNum(data?.recent_7d)} />
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

  return (
    <div className="lc-section">
      <SectionHeader label="STORAGE CORE" color="var(--lc-cyan)" />
      <div className="p-4">
        {isLoading ? (
          <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
            CALCULATING STORAGE MATRIX...
          </div>
        ) : (
          <div className="grid gap-4" style={{ gridTemplateColumns: '1fr 1fr' }}>
            {/* Global stats */}
            <div>
              <div className="lc-metric-label mb-3">GLOBAL STORAGE</div>
              <div className="grid gap-3 mb-3" style={{ gridTemplateColumns: '1fr 1fr' }}>
                <LCARSMetric
                  value={fmtBytes(data?.total_bytes)}
                  label="TOTAL PROCESSED"
                  size="sm"
                  color="var(--lc-cyan)"
                />
                <LCARSMetric
                  value={fmtBytes(data?.avg_bytes)}
                  label="AVG SLIDE SIZE"
                  size="sm"
                />
              </div>
              <div className="space-y-0">
                <DataRow label="SLIDES WITH SIZE" value={fmtNum(data?.total_slides_with_size)} />
                <DataRow label="LARGEST SLIDE" value={fmtBytes(data?.max_bytes)} />
                <DataRow label="SMALLEST SLIDE" value={fmtBytes(data?.min_bytes)} />
                <DataRow label="UPLOADED TODAY" value={fmtBytes(data?.uploaded_today_bytes)} />
              </div>
            </div>

            {/* Per-scanner breakdown */}
            <div>
              <div className="lc-metric-label mb-3">STORAGE BY SCANNER</div>
              {(data?.by_scanner ?? []).length === 0 ? (
                <div className="lc-metric-label" style={{ color: 'var(--lc-text-faint)' }}>
                  NO PER-SCANNER DATA
                </div>
              ) : (
                <div className="space-y-2">
                  {(data?.by_scanner ?? []).slice(0, 6).map(s => (
                    <StorageScannerRow
                      key={s.scanner_id}
                      item={s}
                      totalBytes={data?.total_bytes ?? 1}
                    />
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

function StorageScannerRow({
  item,
  totalBytes,
}: {
  item: StorageScannerItem
  totalBytes: number
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="lc-data-key">{item.scanner_id}</span>
        <span className="lc-data-val" style={{ fontSize: 11 }}>{fmtBytes(item.total_bytes)}</span>
      </div>
      <DistBar value={item.total_bytes} max={totalBytes} color="var(--lc-cyan)" />
    </div>
  )
}

// ── Modern-mode layout ───────────────────────────────────────────────────────
// Used when theme === 'modern'. Shows the same data in standard mission-cards.

// ── Modern Computer Core chart helpers ──────────────────────────────────────

const DONUT_COLORS = ['#22D3EE', '#818CF8', '#C084FC', '#2DD4BF', '#FB7185', '#FCD34D']

function ModernStatCard({
  label, value, color, accent, blink,
}: {
  label: string; value: string; color?: string; accent?: string; blink?: boolean
}) {
  return (
    <div className="mission-card" style={{ padding: '18px 20px', position: 'relative', overflow: 'hidden' }}>
      {accent && (
        <div style={{
          position: 'absolute', top: 0, left: 0, right: 0, height: 3,
          background: accent,
          borderRadius: '8px 8px 0 0',
        }} />
      )}
      <div style={{
        fontSize: 36, fontWeight: 800, lineHeight: 1,
        fontFamily: '"JetBrains Mono", monospace',
        color: color ?? 'var(--text-primary)',
        letterSpacing: '-0.02em',
      }}>
        {value}
      </div>
      <div style={{
        fontSize: 10, marginTop: 8, textTransform: 'uppercase',
        letterSpacing: '0.16em', fontWeight: 600,
        color: 'var(--text-muted)',
        display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {blink && (
          <span style={{
            width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
            background: color ?? 'var(--text-muted)',
            display: 'inline-block',
            animation: 'lcBlink 2s ease-in-out infinite',
          }} />
        )}
        {label}
      </div>
    </div>
  )
}

function ComputerCoreModern() {
  const navigate = useNavigate()
  const { data: overview, isLoading: ovLoading } = useCoreOverview()
  const { data: scanners } = useCoreScanners()
  const { data: recovery } = useCoreRecovery()
  const { data: uploads  } = useCoreUploads()
  const { data: stains   } = useCoreStains()
  const { data: storage  } = useCoreStorage()

  // Build donut data from status_counts
  const donutData = Object.entries(overview?.status_counts ?? {}).map(([status, count], i) => ({
    name: status.replace(/_/g, ' '),
    value: count,
    color: DONUT_COLORS[i % DONUT_COLORS.length],
  })).filter(d => d.value > 0).slice(0, 6)

  // Build recovery radial data
  const recoveryRate = recovery?.recovery_rate ?? 0
  const radialData = [{ name: 'Recovery', value: recoveryRate, fill: '#22D3EE' }]

  // Build upload sparkline from daily data
  const sparkData = (uploads?.daily_uploads_7d ?? []).slice(-7).map((d, i) => ({
    i, v: d.count ?? 0,
  }))

  // Build stain donut
  const stainDonut = (stains?.items ?? []).slice(0, 6).map((d, i) => ({
    name: d.stain_type,
    value: d.count,
    color: DONUT_COLORS[i % DONUT_COLORS.length],
  }))

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 style={{
            fontSize: 22, fontWeight: 800, letterSpacing: '0.06em',
            color: 'var(--text-primary)', textTransform: 'uppercase',
            fontFamily: "'Inter', sans-serif",
          }}>
            Computer Core
          </h1>
          <p style={{ fontSize: 11, marginTop: 4, color: 'var(--text-muted)', letterSpacing: '0.14em', textTransform: 'uppercase' }}>
            Operational Data Systems — Pipeline Analytics Console
          </p>
        </div>
        <button className="btn-ghost-ops" onClick={() => navigate('/computer-core/fullscreen')}>
          Fullscreen
        </button>
      </div>

      {/* Primary KPI row — large numbers */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(6, 1fr)' }}>
        <ModernStatCard
          label="Total Slides"
          value={ovLoading ? '—' : fmtNum(overview?.total_slides)}
          accent="var(--chart-cyan)"
        />
        <ModernStatCard
          label="Uploaded Today"
          value={ovLoading ? '—' : fmtNum(overview?.uploaded_today)}
          color="var(--chart-emerald)"
          accent="var(--chart-emerald)"
        />
        <ModernStatCard
          label="Active"
          value={ovLoading ? '—' : fmtNum(overview?.active_uploads)}
          color={overview?.active_uploads ? 'var(--chart-cyan)' : undefined}
          accent="var(--chart-cyan)"
          blink={!!(overview?.active_uploads)}
        />
        <ModernStatCard
          label="Queued"
          value={ovLoading ? '—' : fmtNum(overview?.queued_uploads)}
          color={overview?.queued_uploads ? 'var(--chart-amber)' : undefined}
          accent="var(--chart-amber)"
        />
        <ModernStatCard
          label="Failed"
          value={ovLoading ? '—' : fmtNum(overview?.failed_slides)}
          color={overview?.failed_slides ? 'var(--chart-rose)' : undefined}
          accent={overview?.failed_slides ? 'var(--chart-rose)' : 'var(--border-faint)'}
          blink={!!(overview?.failed_slides)}
        />
        <ModernStatCard
          label="Recovery"
          value={ovLoading ? '—' : fmtNum(overview?.recovery_backlog)}
          color={overview?.recovery_backlog ? 'var(--chart-amber)' : undefined}
          accent={overview?.recovery_backlog ? 'var(--chart-amber)' : 'var(--border-faint)'}
        />
      </div>

      {/* Main section grid */}
      <div className="grid gap-5" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>

        {/* Pipeline Distribution — Donut */}
        <div className="mission-card p-5">
          <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--accent)', marginBottom: 16 }}>
            Pipeline Distribution
          </h3>
          {donutData.length > 0 ? (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width={100} height={100}>
                <PieChart>
                  <Pie data={donutData} dataKey="value" cx="50%" cy="50%" innerRadius={28} outerRadius={46} strokeWidth={0}>
                    {donutData.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', borderRadius: 6, fontSize: 11 }}
                    itemStyle={{ color: 'var(--tooltip-text)' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-1.5 min-w-0">
                {donutData.map(d => (
                  <div key={d.name} className="flex items-center gap-2">
                    <span style={{ width: 6, height: 6, borderRadius: 2, flexShrink: 0, background: d.color, display: 'inline-block' }} />
                    <span style={{ fontSize: 10, color: 'var(--text-secondary)', textTransform: 'capitalize', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>No pipeline data</p>
          )}
        </div>

        {/* QC / Stain Donut */}
        <div className="mission-card p-5">
          <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--chart-violet)', marginBottom: 16 }}>
            Stain Distribution
          </h3>
          {stainDonut.length > 0 ? (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width={100} height={100}>
                <PieChart>
                  <Pie data={stainDonut} dataKey="value" cx="50%" cy="50%" innerRadius={28} outerRadius={46} strokeWidth={0}>
                    {stainDonut.map((entry, i) => (
                      <Cell key={i} fill={entry.color} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', borderRadius: 6, fontSize: 11 }}
                    itemStyle={{ color: 'var(--tooltip-text)' }}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex-1 space-y-1.5 min-w-0">
                {stainDonut.map(d => (
                  <div key={d.name} className="flex items-center gap-2">
                    <span style={{ width: 6, height: 6, borderRadius: 2, flexShrink: 0, background: d.color, display: 'inline-block' }} />
                    <span style={{ fontSize: 10, color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                    <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              {(stains?.items ?? []).slice(0, 5).map(d => (
                <div key={d.stain_type}>
                  <div className="flex justify-between mb-0.5">
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{d.stain_type}</span>
                    <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>{d.count}</span>
                  </div>
                  <div style={{ height: 3, borderRadius: 2, overflow: 'hidden', background: 'var(--surface-1)' }}>
                    <div style={{ height: '100%', width: `${d.percentage ?? 0}%`, background: 'var(--chart-violet)', transition: 'width 500ms ease' }} />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recovery — Radial gauge */}
        <div className="mission-card p-5">
          <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--chart-rose)', marginBottom: 16 }}>
            Recovery Rate
          </h3>
          <div className="flex items-center gap-5">
            <div style={{ position: 'relative', width: 100, height: 100, flexShrink: 0 }}>
              <ResponsiveContainer width={100} height={100}>
                <RadialBarChart innerRadius={28} outerRadius={46} data={radialData} startAngle={225} endAngle={-45}>
                  <RadialBar dataKey="value" background={{ fill: 'var(--surface-1)' }} cornerRadius={4} />
                </RadialBarChart>
              </ResponsiveContainer>
              <div style={{
                position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center',
              }}>
                <span style={{ fontSize: 16, fontWeight: 800, color: 'var(--chart-cyan)', fontFamily: '"JetBrains Mono", monospace', lineHeight: 1 }}>
                  {recoveryRate.toFixed(0)}%
                </span>
              </div>
            </div>
            <div className="flex-1 space-y-2">
              {[
                { label: 'Monitored',  value: fmtNum(recovery?.total_monitored) },
                { label: 'Auto-fixed', value: fmtNum(recovery?.auto_recovered) },
                { label: 'Manual',     value: fmtNum(recovery?.manual_review_required) },
                { label: 'Resolved',   value: fmtNum(recovery?.total_resolved) },
              ].map(({ label, value }) => (
                <div key={label} className="flex justify-between">
                  <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
                  <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Second row: Scanner Fleet + Upload Velocity */}
      <div className="grid gap-5" style={{ gridTemplateColumns: '1fr 1fr' }}>

        {/* Scanner Fleet */}
        <div className="mission-card p-5">
          <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--chart-cyan)', marginBottom: 16 }}>
            Scanner Fleet
          </h3>
          {(scanners?.scanners ?? []).length === 0 ? (
            <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>No scanner data</p>
          ) : (
            <div className="space-y-3">
              {(scanners?.scanners ?? []).slice(0, 6).map(s => {
                const isActive = s.operational_state === 'active'
                return (
                  <div key={s.scanner_id}>
                    <div className="flex items-center justify-between mb-1.5 gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        <span style={{
                          width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                          background: isActive ? 'var(--chart-emerald)' : 'var(--chart-slate)',
                          boxShadow: isActive ? '0 0 6px var(--chart-emerald)' : 'none',
                          animation: isActive ? 'lcBlink 2.2s ease-in-out infinite' : 'none',
                          display: 'inline-block',
                        }} />
                        <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {s.scanner_id}
                        </span>
                      </div>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace', flexShrink: 0 }}>
                        {fmtNum(s.total_slides)}
                      </span>
                    </div>
                    <div style={{ height: 3, borderRadius: 2, overflow: 'hidden', background: 'var(--surface-1)' }}>
                      <div style={{
                        height: '100%',
                        width: `${scanners?.scanners?.length ? Math.min((s.total_slides / Math.max(...(scanners.scanners.map(x => x.total_slides)), 1)) * 100, 100) : 0}%`,
                        background: isActive ? 'var(--chart-emerald)' : 'var(--chart-slate)',
                        transition: 'width 600ms ease',
                      }} />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* Upload Velocity — sparkline */}
        <div className="mission-card p-5">
          <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--chart-teal)', marginBottom: 16 }}>
            Upload Velocity
          </h3>
          {sparkData.length > 1 ? (
            <>
              <ResponsiveContainer width="100%" height={80}>
                <AreaChart data={sparkData} margin={{ top: 2, right: 4, left: 0, bottom: 2 }}>
                  <defs>
                    <linearGradient id="uploadGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#2DD4BF" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#2DD4BF" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="i" hide />
                  <Tooltip
                    contentStyle={{ background: 'var(--tooltip-bg)', border: '1px solid var(--tooltip-border)', borderRadius: 6, fontSize: 10 }}
                    itemStyle={{ color: 'var(--chart-teal)' }}
                    formatter={(v: number) => [v, 'Uploads']}
                  />
                  <Area type="monotone" dataKey="v" stroke="#2DD4BF" strokeWidth={2} fill="url(#uploadGrad)" dot={false} />
                </AreaChart>
              </ResponsiveContainer>
              <div className="mt-3 space-y-1.5">
                {[
                  { label: 'Completed total', value: fmtNum(uploads?.completed_total) },
                  { label: 'Queue depth',     value: fmtNum(uploads?.queue_depth) },
                  { label: 'Delayed',         value: fmtNum(uploads?.delayed_count) },
                  { label: 'Avg speed',       value: uploads?.avg_speed_mbps != null ? `${uploads.avg_speed_mbps} Mb/s` : '—' },
                ].map(({ label, value }) => (
                  <div key={label} className="flex justify-between">
                    <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
                    <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="space-y-2">
              {[
                { label: 'Completed total', value: fmtNum(uploads?.completed_total) },
                { label: 'Queue depth',     value: fmtNum(uploads?.queue_depth) },
                { label: 'Delayed',         value: fmtNum(uploads?.delayed_count) },
                { label: 'Total retries',   value: fmtNum(uploads?.total_retries) },
                { label: 'Avg speed',       value: uploads?.avg_speed_mbps != null ? `${uploads.avg_speed_mbps} Mb/s` : '—' },
              ].map(({ label, value }) => (
                <div key={label} className="flex justify-between">
                  <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>{label}</span>
                  <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Storage Core — full-width with capacity gauge */}
      <div className="mission-card p-5">
        <h3 style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--chart-cyan)', marginBottom: 20 }}>
          Storage Core
        </h3>
        <div className="grid gap-6" style={{ gridTemplateColumns: '200px 1fr' }}>
          <div className="space-y-3">
            {[
              { label: 'Total processed', value: fmtBytes(storage?.total_bytes) },
              { label: 'Average slide',   value: fmtBytes(storage?.avg_bytes) },
              { label: 'Largest slide',   value: fmtBytes(storage?.max_bytes) },
              { label: 'Uploaded today',  value: fmtBytes(storage?.uploaded_today_bytes) },
            ].map(({ label, value }) => (
              <div key={label}>
                <div className="flex justify-between mb-1">
                  <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>{label}</span>
                  <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-primary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</span>
                </div>
              </div>
            ))}
          </div>
          <div>
            <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--text-faint)', marginBottom: 12 }}>
              Storage by scanner
            </div>
            <div className="space-y-3">
              {(storage?.by_scanner ?? []).slice(0, 6).map(s => {
                const pct = storage?.total_bytes ? Math.min((s.total_bytes / storage.total_bytes) * 100, 100) : 0
                return (
                  <div key={s.scanner_id}>
                    <div className="flex justify-between mb-1.5">
                      <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{s.scanner_id}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>{fmtBytes(s.total_bytes)}</span>
                    </div>
                    <div style={{ height: 4, borderRadius: 2, overflow: 'hidden', background: 'var(--surface-1)' }}>
                      <div style={{
                        height: '100%', width: `${pct}%`,
                        background: 'linear-gradient(90deg, var(--chart-cyan), var(--chart-teal))',
                        transition: 'width 600ms ease',
                        borderRadius: 2,
                      }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export function ComputerCore() {
  const { isLCARS } = useTheme()
  const navigate = useNavigate()
  const [activeSection, setActiveSection] = useState('status')

  if (!isLCARS) return <ComputerCoreModern />

  const scrollTo = (id: string) => {
    setActiveSection(id)
    const el = document.getElementById(`lc-section-${id}`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div
      className="lcars-core"
      style={{
        margin: 0,
        height: 'calc(100vh - 96px)',
        overflow: 'hidden',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      {/* ── Page title ribbon ───────────────────────────────────── */}
      <div style={{
        display: 'flex',
        alignItems: 'stretch',
        height: 44,
        flexShrink: 0,
        background: 'rgba(0,3,14,0.95)',
        borderBottom: '1px solid rgba(255,153,0,0.14)',
      }}>
        {/* Orange pill */}
        <div style={{
          display: 'flex', alignItems: 'center',
          padding: '0 24px',
          borderRadius: '0 22px 22px 0',
          background: '#FF9900',
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 15, fontWeight: 600,
          letterSpacing: '0.22em',
          textTransform: 'uppercase',
          color: 'rgba(0,5,20,0.90)',
          flexShrink: 0,
        }}>
          COMPUTER CORE
        </div>
        {/* Sub-label */}
        <div style={{
          display: 'flex', alignItems: 'center',
          padding: '0 16px', gap: 8,
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 9, letterSpacing: '0.24em',
          textTransform: 'uppercase',
          color: 'rgba(255,153,0,0.45)',
        }}>
          PIPELINE ANALYTICS &amp; OPERATIONS CONSOLE
        </div>
        {/* Right meta */}
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', padding: '0 16px', gap: 12 }}>
          <button
            type="button"
            onClick={() => navigate('/computer-core/fullscreen')}
            style={{
              background: 'rgba(255,153,0,0.12)',
              border: '1px solid rgba(255,153,0,0.28)',
              borderRadius: 3,
              padding: '4px 14px',
              fontSize: 10,
              fontWeight: 600,
              letterSpacing: '.16em',
              color: 'rgba(255,153,0,0.80)',
              cursor: 'pointer',
              textTransform: 'uppercase',
              fontFamily: 'inherit',
              flexShrink: 0,
              transition: 'background 120ms',
            }}
          >
            ⊞ FULLSCREEN
          </button>
          <div style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            fontFamily: "'Antonio', 'Inter', sans-serif",
            fontSize: 9, letterSpacing: '0.20em',
            textTransform: 'uppercase',
            color: 'rgba(0,221,136,0.75)',
          }}>
            <span style={{
              width: 5, height: 5, borderRadius: '50%',
              background: '#00DD88', flexShrink: 0,
              animation: 'lcBlink 2.4s ease-in-out infinite',
            }} aria-hidden />
            ONLINE
          </div>
        </div>
      </div>

      {/* ── Body row ────────────────────────────────────────────── */}
      {/*
       * minHeight: 0 is critical here: without it, a flex item's minimum
       * size is its content size, which prevents lc-content from scrolling.
       */}
      <div
        style={{
          display: 'flex',
          flex: 1,
          minHeight: 0,
          overflow: 'hidden',
        }}
      >
        {/* Left navigation rail */}
        <div style={{ width: 132, flexShrink: 0, overflow: 'hidden' }}>
          <LeftRail active={activeSection} onSelect={scrollTo} />
        </div>

        {/* Main content */}
        <div
          className="lc-content"
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '16px 20px 24px 16px' }}
        >
          <div className="space-y-4">
            <div id="lc-section-status"><StatusSection /></div>
            <div id="lc-section-scanners"><ScannersSection /></div>
            <div
              style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}
            >
              <div id="lc-section-stains"><StainsSection /></div>
              <div id="lc-section-uploads"><UploadsSection /></div>
            </div>
            <div id="lc-section-recovery"><RecoverySection /></div>
            <div id="lc-section-storage"><StorageSection /></div>
          </div>
        </div>
      </div>
    </div>
  )
}
