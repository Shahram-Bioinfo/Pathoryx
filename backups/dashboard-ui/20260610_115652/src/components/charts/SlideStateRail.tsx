import { DOT_HEX_DARK, DOT_HEX_LIGHT, statusVariant } from '../../utils/colors'
import { fmtStatusLabel } from '../../utils/formatters'
import { useTheme } from '../layout/ThemeProvider'

interface Props { byStatus: Record<string, number>; total: number }

export function SlideStateRail({ byStatus, total }: Props) {
  const { theme } = useTheme()
  const hex = theme === 'dark' ? DOT_HEX_DARK : DOT_HEX_LIGHT

  const states = Object.entries(byStatus)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .map(([status, count]) => ({
      status,
      count,
      color: hex[statusVariant(status)] ?? (theme === 'dark' ? '#64748b' : '#94a3b8'),
      pct: (count / total) * 100,
    }))

  return (
    <div>
      {/* Total readout */}
      <div className="flex items-baseline gap-2 mb-3">
        <span
          className="text-2xl font-semibold tabular"
          style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--text-primary)' }}
        >
          {total.toLocaleString()}
        </span>
        <span className="text-[10px] tracking-[0.15em] uppercase" style={{ color: 'var(--text-faint)' }}>
          artifacts
        </span>
      </div>

      {/* Segmented state rail */}
      <div
        className="flex rounded-sm overflow-hidden mb-4"
        style={{ height: '7px', background: 'var(--accent-faint)', gap: '1px' }}
      >
        {states.map(s => (
          <div
            key={s.status}
            style={{
              width: `${s.pct}%`,
              background: s.color,
              opacity: 0.82,
              minWidth: s.count > 0 ? '3px' : 0,
            }}
            title={`${fmtStatusLabel(s.status)}: ${s.count.toLocaleString()}`}
          />
        ))}
      </div>

      {/* State breakdown */}
      <div className="space-y-1 max-h-44 overflow-y-auto pr-1 scrollbar-none">
        {states.map(s => (
          <div key={s.status} className="flex items-center gap-2">
            <div
              className="shrink-0 rounded-full"
              style={{ height: '3px', width: '0.875rem', background: s.color, opacity: 0.85 }}
            />
            <span
              className="flex-1 text-[10px] font-mono truncate"
              style={{ color: 'var(--text-secondary)' }}
            >
              {fmtStatusLabel(s.status)}
            </span>
            <span className="text-[10px] font-mono tabular" style={{ color: 'var(--text-muted)' }}>
              {s.count.toLocaleString()}
            </span>
            <span
              className="text-[9px] font-mono tabular text-right"
              style={{ color: 'var(--text-faint)', minWidth: '2.75rem' }}
            >
              {s.pct.toFixed(1)}%
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
