import type { QueueRow } from '../../types/api'
import { getChartColors } from '../../utils/colors'
import { fmtServiceName } from '../../utils/formatters'
import { useTheme } from '../layout/ThemeProvider'

interface Props { queues: QueueRow[] }

const LEGEND = [
  { label: 'PENDING',   key: 'amber'   as const },
  { label: 'RUNNING',   key: 'cyan'    as const },
  { label: 'FAILED',    key: 'rose'    as const },
  { label: 'COMPLETED', key: 'emerald' as const },
]

export function QueueTelemetryStrip({ queues }: Props) {
  useTheme()
  const C = getChartColors()

  const rows = queues.map(q => {
    const total = q.pending + q.running + q.failed + q.completed
    return { q, total }
  }).filter(({ total }) => total > 0)

  if (!rows.length) return null

  return (
    <div className="space-y-2">
      {/* Tick scale header */}
      <div className="flex items-center" style={{ paddingLeft: '7.5rem', paddingRight: '4.5rem' }}>
        {[0, 25, 50, 75, 100].map((t, i) => (
          <div
            key={t}
            className="flex-1 text-[8px] font-mono"
            style={{
              color: 'var(--text-faint)',
              textAlign: i === 0 ? 'left' : i === 4 ? 'right' : 'center',
            }}
          >
            {t === 0 || t === 100 ? '' : `${t}%`}
          </div>
        ))}
      </div>

      {/* Service channel rows */}
      {rows.map(({ q, total }) => {
        const segs = [
          { key: 'pending',   v: q.pending,   color: C.amber   },
          { key: 'running',   v: q.running,   color: C.cyan    },
          { key: 'failed',    v: q.failed,    color: C.rose    },
          { key: 'completed', v: q.completed, color: C.emerald },
        ].filter(s => s.v > 0)

        return (
          <div key={q.target_service} className="flex items-center gap-3">
            {/* Channel label */}
            <span
              className="text-[9px] font-mono tracking-wide shrink-0 truncate text-right"
              style={{ width: '7rem', color: 'var(--text-muted)' }}
            >
              {fmtServiceName(q.target_service).toUpperCase()}
            </span>

            {/* Signal rail */}
            <div
              className="relative flex-1 rounded-sm overflow-hidden"
              style={{ height: '5px', background: 'var(--accent-faint)' }}
            >
              {/* Tick gridlines at 25 / 50 / 75 % */}
              {[25, 50, 75].map(t => (
                <div
                  key={t}
                  className="absolute inset-y-0 w-px pointer-events-none"
                  style={{ left: `${t}%`, background: 'var(--border-default)', zIndex: 1 }}
                />
              ))}
              {/* Proportional segments */}
              <div className="absolute inset-0 flex">
                {segs.map(s => (
                  <div
                    key={s.key}
                    style={{
                      width: `${(s.v / total) * 100}%`,
                      background: s.color,
                      opacity: 0.82,
                      minWidth: '2px',
                    }}
                  />
                ))}
              </div>
            </div>

            {/* Total count */}
            <span
              className="text-[10px] font-mono tabular shrink-0 text-right"
              style={{ width: '4rem', color: 'var(--text-muted)' }}
            >
              {total.toLocaleString()}
            </span>
          </div>
        )
      })}

      {/* Legend rail */}
      <div
        className="flex items-center gap-5 pt-1.5 mt-0.5"
        style={{ paddingLeft: '7.5rem', borderTop: '1px solid var(--border-faint)' }}
      >
        {LEGEND.map(l => (
          <div key={l.label} className="flex items-center gap-1.5">
            <div className="rounded-full" style={{ height: '3px', width: '1rem', background: C[l.key], opacity: 0.85 }} />
            <span className="text-[9px] font-mono tracking-widest" style={{ color: 'var(--text-faint)' }}>
              {l.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
