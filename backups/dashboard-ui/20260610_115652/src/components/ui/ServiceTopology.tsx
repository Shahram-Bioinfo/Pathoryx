import { formatDistanceToNow, parseISO } from 'date-fns'
import type { QueueRow, RunnerItem } from '../../types/api'

/*
 * ServiceTopology — full-width operational network diagram.
 *
 * Visualises the six Palantir services as a connected graph showing live
 * runner health, queue pressure, and last activity in a single glance.
 *
 * Visual language:
 *   active-clear  → calm emerald inner dot + soft ping ring (3.5s)
 *   active-loaded → amber inner dot + slower amber ring (5s) — queue pressure
 *   failed        → rose inner dot + restrained beacon (4.2s)
 *   stale         → dim amber inner dot, no animation
 *   offline       → very dim gray, no animation
 *
 * Animation safety:
 *   All motion uses the existing `constellationPing` keyframe (scale+opacity,
 *   GPU composited). Staggered per-node delays (0.7s apart) ensure at most
 *   one ring is expanding at any moment across the whole panel.
 *   prefers-reduced-motion suppresses everything via index.css global override.
 *
 * Recovery topology:
 *   RecoverySentry is positioned below the main pipeline and connected by two
 *   dashed paths: a vertical monitoring line to QC (it watches failed QC slides)
 *   and a curved feedback arc to Babel-Shark (requeue path).
 */

// ─── Node definitions ─────────────────────────────────────────────────────────

interface NodeDef {
  id: string
  /** Display label shown in the SVG (short, branded, never a raw ID) */
  svgLabel: string
  /** Keys to match against runner.service_name.toLowerCase() */
  runnerKeys: readonly string[]
  /** Keys to match against queue.target_service.toLowerCase() */
  queueKeys: readonly string[]
  cx: number
  cy: number
  /** True for Scanner — represents external hardware, always present */
  isSource?: boolean
  /** True for RecoverySentry — positioned below the main pipeline */
  isRecovery?: boolean
}

const NODES: readonly NodeDef[] = [
  {
    id: 'scanner',
    svgLabel: 'Scanner',
    runnerKeys: [],
    queueKeys: [],
    cx: 52, cy: 72,
    isSource: true,
  },
  {
    id: 'babelshark',
    svgLabel: 'Babel-Shark',
    runnerKeys: ['babelshark'],
    queueKeys: [],          // intake reads from filesystem, not trigger queue
    cx: 168, cy: 72,
  },
  {
    id: 'qc',
    svgLabel: 'QC Service',
    runnerKeys: ['qc', 'qc_service'],
    queueKeys: ['qc', 'qc_service'],
    cx: 284, cy: 72,
  },
  {
    id: 'dicom',
    svgLabel: 'DICOM',
    runnerKeys: ['dicom', 'dicomizer', 'dicom_service'],
    queueKeys: ['dicom', 'dicomizer', 'dicom_service'],
    cx: 400, cy: 72,
  },
  {
    id: 'upload',
    svgLabel: 'Upload',
    runnerKeys: ['uploader', 'upload_service'],
    queueKeys: ['uploader', 'upload_service'],
    cx: 516, cy: 72,
  },
  {
    id: 'recovery',
    svgLabel: 'RecoverySentry',
    runnerKeys: ['recovery_sentry', 'failed_watcher'],
    queueKeys: [],
    cx: 284, cy: 178,
    isRecovery: true,
  },
] as const

// Index pairs for the left-to-right pipeline connections
const PIPELINE_LINKS: Array<[number, number]> = [[0,1],[1,2],[2,3],[3,4]]

// ─── Derived types ────────────────────────────────────────────────────────────

type RunnerStatus = 'active' | 'stale' | 'error' | 'offline'
type NodeHealth   = 'active-clear' | 'active-loaded' | 'failed' | 'stale' | 'offline'

// ─── Data derivation helpers ──────────────────────────────────────────────────

function runnerStatus(keys: readonly string[], runners: RunnerItem[]): RunnerStatus {
  const matches = runners.filter(r =>
    keys.some(k => r.service_name.toLowerCase().includes(k))
  )
  if (!matches.length)                          return 'offline'
  if (matches.some(r => r.status === 'crashed')) return 'error'
  if (matches.some(r => r.status === 'stale'))   return 'stale'
  if (matches.some(r => r.status === 'active'))  return 'active'
  return 'offline'
}

function queueTotals(keys: readonly string[], queues: QueueRow[]) {
  return queues
    .filter(q => keys.some(k => q.target_service.toLowerCase().includes(k)))
    .reduce(
      (acc, q) => ({
        pending: acc.pending + q.pending,
        running: acc.running + q.running,
        failed:  acc.failed  + q.failed,
      }),
      { pending: 0, running: 0, failed: 0 },
    )
}

function lastSeen(keys: readonly string[], runners: RunnerItem[]): string | null {
  const matches = runners.filter(r =>
    keys.some(k => r.service_name.toLowerCase().includes(k))
  )
  if (!matches.length) return null
  const latest = matches.reduce((best, r) =>
    r.last_heartbeat_at > best.last_heartbeat_at ? r : best
  )
  try {
    return formatDistanceToNow(parseISO(latest.last_heartbeat_at), { addSuffix: true })
  } catch {
    return null
  }
}

function nodeHealth(
  status: RunnerStatus,
  pending: number,
  failed: number,
  isSource?: boolean,
): NodeHealth {
  if (isSource) return 'active-clear'   // Scanner always present
  if (status === 'offline') return 'offline'
  if (status === 'error' || failed > 0) return 'failed'
  if (status === 'stale') return 'stale'
  // Queue pressure threshold: ≥5 pending triggers amber load indicator
  if (pending >= 5) return 'active-loaded'
  return 'active-clear'
}

// ─── Visual maps ─────────────────────────────────────────────────────────────

// Inner dot fill — represents service health state
const DOT_COLOR: Record<NodeHealth, string> = {
  'active-clear':  'var(--chart-emerald)',
  'active-loaded': 'var(--chart-amber)',
  'failed':        'var(--chart-rose)',
  'stale':         'var(--chart-amber)',
  'offline':       'var(--text-faint)',
}

// Inner dot opacity — offline is barely visible
const DOT_OPACITY: Record<NodeHealth, number> = {
  'active-clear':  0.85,
  'active-loaded': 0.80,
  'failed':        0.75,
  'stale':         0.40,
  'offline':       0.18,
}

// Ping ring stroke colour (null → no animation)
const RING_COLOR: Record<NodeHealth, string | null> = {
  'active-clear':  'var(--chart-emerald)',
  'active-loaded': 'var(--chart-amber)',
  'failed':        'var(--chart-rose)',
  'stale':         null,
  'offline':       null,
}

// Animation duration varies by health state — failed is slower (more ominous)
const RING_DURATION: Record<NodeHealth, number | null> = {
  'active-clear':  3.5,
  'active-loaded': 5.0,
  'failed':        4.2,
  'stale':         null,
  'offline':       null,
}

// ─── Component ───────────────────────────────────────────────────────────────

interface Props {
  runners: RunnerItem[]
  queues?: QueueRow[]
}

export function ServiceTopology({ runners, queues = [] }: Props) {
  // Pre-compute all node state outside JSX for clarity
  const nodes = NODES.map((def, idx) => {
    const status  = def.isSource
      ? 'active' as RunnerStatus
      : runnerStatus(def.runnerKeys, runners)
    const q       = queueTotals(def.queueKeys, queues)
    const health  = nodeHealth(status, q.pending, q.failed, def.isSource)
    const seen    = def.isSource ? null : lastSeen(def.runnerKeys, runners)
    const dot     = DOT_COLOR[health]
    const dotOp   = DOT_OPACITY[health]
    const ring    = RING_COLOR[health]
    const ringDur = RING_DURATION[health]
    /*
     * Non-uniform ring delays — each node fires its ping at an independent time.
     * Gaps: 0.63, 0.95, 1.21, 1.17, 1.25 s (irregular, not random).
     * This ensures at most one ring is visually prominent at any moment while
     * avoiding the mechanical feel of a perfect cadence (idx × constant).
     */
    const RING_DELAYS = ['0s', '0.63s', '1.58s', '2.79s', '3.96s', '5.21s']
    const delay   = RING_DELAYS[idx] ?? `${idx * 0.7}s`

    // Status summary line (appears below node name)
    const active = q.pending + q.running
    const summaryText = (() => {
      if (def.isSource)              return 'watch active'
      if (health === 'offline')      return 'offline'
      if (health === 'stale')        return 'stale'
      if (health === 'failed')       return q.failed > 0 ? `${q.failed} failed` : 'alert'
      if (health === 'active-loaded') return `${active} queued`
      return 'nominal'
    })()

    const summaryColor = (() => {
      if (def.isSource)                         return 'var(--text-muted)'
      if (health === 'failed')                  return 'var(--chart-rose)'
      if (health === 'active-loaded')           return 'var(--chart-amber)'
      if (health === 'stale')                   return 'var(--chart-amber)'
      if (health === 'offline')                 return 'var(--text-faint)'
      return 'var(--text-muted)'
    })()

    // Queue pressure bar width (0–36 SVG units = 0–q≥15)
    const barWidth = q.pending > 0
      ? Math.min(Math.round((q.pending / 15) * 36), 36)
      : 0
    const barColor = q.pending >= 10 ? 'var(--chart-rose)' : 'var(--chart-amber)'

    return {
      ...def, idx, status, q, health, seen,
      dot, dotOp, ring, ringDur, delay,
      summaryText, summaryColor, barWidth, barColor,
    }
  })

  return (
    <div aria-label="Pipeline service network topology">
      <svg
        viewBox="0 0 620 230"
        width="100%"
        style={{ display: 'block', overflow: 'visible' }}
        aria-hidden="true"
      >
        <defs>
          {/* Solid arrowhead for pipeline direction */}
          <marker
            id="svc-arrow"
            markerWidth="6" markerHeight="5"
            refX="5" refY="2.5"
            orient="auto"
          >
            <path
              d="M0,0.5 L5,2.5 L0,4.5 z"
              style={{ fill: 'var(--border-default)' }}
            />
          </marker>
          {/* Hollow chevron for recovery/monitoring paths */}
          <marker
            id="svc-recovery-arrow"
            markerWidth="6" markerHeight="5"
            refX="4" refY="2.5"
            orient="auto"
          >
            <path
              d="M0,0.5 L4,2.5 L0,4.5"
              fill="none"
              style={{ stroke: 'var(--text-faint)', strokeWidth: 0.8, opacity: 0.5 }}
            />
          </marker>
        </defs>

        {/* ── Pipeline connection lines ─────────────────────────────── */}
        {PIPELINE_LINKS.map(([from, to]) => {
          const a = NODES[from]
          const b = NODES[to]
          // Lines start/end just outside each node's main circle (r=14, +2px gap)
          return (
            <line
              key={`pipe-${from}-${to}`}
              x1={a.cx + 16} y1={a.cy}
              x2={b.cx - 16} y2={b.cy}
              style={{
                stroke: 'var(--border-default)',
                strokeWidth: 0.75,
                markerEnd: 'url(#svc-arrow)',
              }}
            />
          )
        })}

        {/* ── Recovery monitoring paths (dashed) ───────────────────── */}

        {/*
         * Vertical monitoring line: RecoverySentry → QC
         * RecoverySentry watches QC-failed slides and can trigger re-analysis.
         */}
        <line
          x1={284} y1={164}    /* just above RecoverySentry */
          x2={284} y2={90}     /* just below QC node */
          style={{
            stroke: 'var(--text-faint)',
            strokeWidth: 0.7,
            strokeDasharray: '3 4',
            opacity: 0.45,
            markerEnd: 'url(#svc-recovery-arrow)',
          }}
        />

        {/*
         * Curved requeue arc: RecoverySentry → Babel-Shark
         * When RecoverySentry requeues a slide, the trigger goes back to intake.
         * The bezier arc dips below the main pipeline area before curving up.
         */}
        <path
          d="M 268,178 C 200,192 120,148 168,90"
          fill="none"
          style={{
            stroke: 'var(--text-faint)',
            strokeWidth: 0.7,
            strokeDasharray: '3 4',
            opacity: 0.35,
            markerEnd: 'url(#svc-recovery-arrow)',
          }}
        />

        {/* Small "↺" requeue label on the curved arc — positioned mid-arc */}
        <text
          x={178} y={168}
          style={{
            fontSize: '6px',
            fill: 'var(--text-faint)',
            fontFamily: 'Inter, system-ui, sans-serif',
            opacity: 0.5,
          }}
        >
          requeue
        </text>

        {/* Small "monitor" label on the vertical dashed line */}
        <text
          x={289} y={132}
          style={{
            fontSize: '6px',
            fill: 'var(--text-faint)',
            fontFamily: 'Inter, system-ui, sans-serif',
            opacity: 0.5,
          }}
        >
          monitor
        </text>

        {/* ── Service nodes ─────────────────────────────────────────── */}
        {nodes.map(node => {
          const {
            cx, cy, dot, dotOp, ring, ringDur, delay,
            summaryText, summaryColor, barColor,
            svgLabel, seen, barWidth: bw, health,
          } = node

          return (
            <g key={node.id}>
              {/*
               * Outer ambient halo — very faint, just marks the node's presence.
               * Dim for offline nodes, barely visible for active ones.
               */}
              <circle
                cx={cx} cy={cy} r={22}
                style={{
                  fill: dot,
                  opacity: health === 'offline' ? 0.02 : 0.035,
                }}
              />

              {/*
               * Animated status ring.
               * Uses constellationPing keyframe (scale+opacity, GPU composited).
               * transformBox+transformOrigin anchor the scale to this circle's
               * own center, not the SVG viewport origin.
               */}
              {ring && ringDur && (
                <circle
                  cx={cx} cy={cy} r={18}
                  fill="none"
                  style={{
                    stroke: ring,
                    strokeWidth: health === 'failed' ? 1.5 : 1,
                    opacity: 0,
                    transformBox: 'fill-box',
                    transformOrigin: 'center',
                    animation: `constellationPing ${ringDur}s ease-out infinite`,
                    animationDelay: delay,
                    willChange: 'transform, opacity',
                  }}
                />
              )}

              {/*
               * Node main circle — translucent fill + border ring.
               * The fill gives body to the node without looking solid/heavy.
               */}
              <circle
                cx={cx} cy={cy} r={14}
                style={{
                  fill: dot,
                  opacity: dotOp * 0.14,
                }}
              />
              <circle
                cx={cx} cy={cy} r={14}
                fill="none"
                style={{
                  stroke: dot,
                  strokeWidth: 1,
                  opacity: dotOp * 0.45,
                }}
              />

              {/* Inner dot — the main health indicator */}
              <circle
                cx={cx} cy={cy} r={5}
                style={{ fill: dot, opacity: dotOp }}
              />

              {/* ── Text labels ─────────────────────────────────────── */}

              {/* Service display name */}
              <text
                x={cx} y={cy + 24}
                textAnchor="middle"
                style={{
                  fontSize: '7.5px',
                  fontWeight: '600',
                  fill: health === 'offline' ? 'var(--text-faint)' : 'var(--text-secondary)',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  letterSpacing: '0.015em',
                }}
              >
                {svgLabel}
              </text>

              {/* Status / queue summary line */}
              <text
                x={cx} y={cy + 35}
                textAnchor="middle"
                style={{
                  fontSize: '6px',
                  fill: summaryColor,
                  fontFamily: 'Inter, system-ui, sans-serif',
                  letterSpacing: '0.01em',
                }}
              >
                {summaryText}
              </text>

              {/* Last heartbeat — only for service runner nodes */}
              {seen && (
                <text
                  x={cx} y={cy + 45}
                  textAnchor="middle"
                  style={{
                    fontSize: '5.5px',
                    fill: 'var(--text-faint)',
                    fontFamily: 'Inter, system-ui, sans-serif',
                    opacity: 0.65,
                  }}
                >
                  {seen}
                </text>
              )}

              {/*
               * Queue pressure bar — only rendered when pending > 0.
               * Width ∝ queue depth (capped at 36 SVG units = ≥15 pending).
               * Color shifts rose when queue is critically deep (≥10 pending).
               */}
              {bw > 0 && (
                <rect
                  x={cx - 18}
                  y={cy + 49}
                  width={bw}
                  height={2}
                  rx={1}
                  style={{ fill: barColor, opacity: 0.55 }}
                />
              )}
            </g>
          )
        })}
      </svg>
    </div>
  )
}
