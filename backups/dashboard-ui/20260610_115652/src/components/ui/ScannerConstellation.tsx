import type { RunnerItem } from '../../types/api'

/*
 * ScannerConstellation — compact SVG topology showing pipeline service health.
 *
 * Four nodes represent the four pipeline stages in flow order. Each node has:
 *   • A filled inner circle (status colour)
 *   • An animated outer ring that pulses when the service is active/stale/error
 *     (constellationPing keyframe: transform: scale + opacity — GPU composited)
 * Connecting lines are static (no animation) — flow direction is implied by order.
 *
 * Staggered animation delays (0, 0.85, 1.7, 2.55s) ensure at most one ring is
 * expanding at any moment, keeping visual activity low.
 *
 * SVG transformBox:'fill-box' + transformOrigin:'center' makes each ring's
 * scale anchor correctly to the circle's own centre, not the SVG origin.
 */

interface Props {
  runners: RunnerItem[]
}

const NODES = [
  { id: 'intake',     label: 'Intake',  keys: ['babelshark'],                         cx: 35,  cy: 38 },
  { id: 'qc',         label: 'QC',      keys: ['qc', 'qc_service'],                   cx: 115, cy: 38 },
  { id: 'dicom',      label: 'DICOM',   keys: ['dicom', 'dicomizer', 'dicom_service'], cx: 195, cy: 38 },
  { id: 'upload',     label: 'Upload',  keys: ['uploader', 'upload_service'],          cx: 275, cy: 38 },
] as const

const LINKS: Array<[number, number]> = [[0,1],[1,2],[2,3]]

type NodeStatus = 'active' | 'stale' | 'error' | 'offline'

function resolveStatus(nodeKeys: readonly string[], runners: RunnerItem[]): NodeStatus {
  const matches = runners.filter(r =>
    nodeKeys.some(k => r.service_name.toLowerCase().includes(k))
  )
  if (matches.length === 0) return 'offline'
  if (matches.some(r => r.status === 'crashed'))  return 'error'
  if (matches.some(r => r.status === 'stale'))    return 'stale'
  if (matches.some(r => r.status === 'active'))   return 'active'
  return 'offline'
}

const STATUS_COLOR: Record<NodeStatus, string> = {
  active:  'var(--chart-emerald)',
  stale:   'var(--chart-amber)',
  error:   'var(--chart-rose)',
  offline: 'var(--text-faint)',
}

const RING_OPACITY: Record<NodeStatus, number> = {
  active:  0.75,
  stale:   0.65,
  error:   0.80,
  offline: 0,
}

export function ScannerConstellation({ runners }: Props) {
  return (
    <div aria-hidden className="mb-5">
      <svg
        viewBox="0 0 310 62"
        width="100%"
        style={{ overflow: 'visible', display: 'block' }}
      >
        {/* Pipeline connection lines — static, dashed, very subtle */}
        {LINKS.map(([from, to]) => (
          <line
            key={`link-${from}-${to}`}
            x1={NODES[from].cx} y1={NODES[from].cy}
            x2={NODES[to].cx}   y2={NODES[to].cy}
            style={{ stroke: 'var(--border-default)', strokeWidth: 0.75, strokeDasharray: '3 5' }}
          />
        ))}

        {/* Service nodes */}
        {NODES.map((node, idx) => {
          const status   = resolveStatus(node.keys, runners)
          const color    = STATUS_COLOR[status]
          const ringOpacity = RING_OPACITY[status]

          return (
            <g key={node.id}>
              {/*
               * Animated status ring.
               * Non-uniform delays: 0, 0.73, 1.82, 3.14 s.
               * Gaps: 0.73, 1.09, 1.32 s — decreasing but not uniform.
               * This makes the four rings feel like independent sensor sweeps.
               */}
              {status !== 'offline' && (
                <circle
                  cx={node.cx} cy={node.cy} r={8}
                  fill="none"
                  style={{
                    stroke: color,
                    strokeWidth: 1,
                    opacity: 0,
                    transformBox: 'fill-box',
                    transformOrigin: 'center',
                    animation: 'constellationPing 3.2s ease-out infinite',
                    animationDelay: `${[0, 0.73, 1.82, 3.14][idx] ?? idx * 0.85}s`,
                    willChange: 'transform, opacity',
                  }}
                />
              )}

              {/* Inner node circle */}
              <circle
                cx={node.cx} cy={node.cy} r={5}
                style={{
                  fill: color,
                  opacity: status === 'offline' ? 0.25 : 0.85,
                }}
              />

              {/* Node border ring — static */}
              <circle
                cx={node.cx} cy={node.cy} r={5}
                fill="none"
                style={{
                  stroke: color,
                  strokeWidth: 1,
                  opacity: status === 'offline' ? 0.15 : ringOpacity * 0.55,
                }}
              />

              {/* Service label */}
              <text
                x={node.cx} y={node.cy + 17}
                textAnchor="middle"
                style={{
                  fontSize: '6.5px',
                  fill: 'var(--text-muted)',
                  fontFamily: 'Inter, system-ui, sans-serif',
                  letterSpacing: '0.07em',
                  textTransform: 'uppercase',
                  opacity: 0.7,
                }}
              >
                {node.label}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
