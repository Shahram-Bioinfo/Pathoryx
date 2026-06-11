import { useFlashOnChange } from '../../hooks/useFlashOnChange'
import { useTheme } from '../layout/ThemeProvider'

export interface TelemetryMetric {
  key:     string
  label:   string
  value:   string
  sub?:    string
  accent?: string
  loading?: boolean
}

interface Props {
  metrics:   TelemetryMetric[]
  columns?:  2 | 3 | 4 | 6
  className?: string
}

// ── LCARS cell ────────────────────────────────────────────────────────────────

function LCARSCell({ metric }: { metric: TelemetryMetric }) {
  const isFlashing = useFlashOnChange(metric.loading ? '—' : metric.value)

  return (
    <div className={`lc-telemetry-cell${isFlashing ? ' flashing' : ''}`}>
      <span className="lc-telemetry-label">{metric.label}</span>
      {metric.loading ? (
        <div className="ops-skeleton" style={{ height: 22, width: 56, borderRadius: 0 }} />
      ) : (
        <span
          className="lc-telemetry-value"
          style={metric.accent ? { color: metric.accent } : undefined}
        >
          {metric.value}
        </span>
      )}
      {metric.sub && (
        <span className="lc-telemetry-sub">{metric.sub}</span>
      )}
    </div>
  )
}

// ── Modern cell ───────────────────────────────────────────────────────────────

function ModernCell({
  metric,
  borderRight,
  borderBottom,
}: {
  metric:       TelemetryMetric
  borderRight:  boolean
  borderBottom: boolean
}) {
  const isFlashing = useFlashOnChange(metric.loading ? '—' : metric.value)

  return (
    <div
      className="flex flex-col justify-center gap-1 px-4 py-3"
      style={{
        borderRight:  borderRight  ? '1px solid var(--border-faint)' : undefined,
        borderBottom: borderBottom ? '1px solid var(--border-faint)' : undefined,
        minWidth: 0,
      }}
    >
      <span
        className="text-[9px] font-mono tracking-[0.18em] uppercase leading-none truncate"
        style={{ color: 'var(--text-faint)' }}
      >
        {metric.label}
      </span>

      {metric.loading ? (
        <div className="ops-skeleton h-[18px] w-14 rounded mt-0.5" />
      ) : (
        <span
          className="text-[18px] font-semibold tabular leading-none"
          style={{
            fontFamily: '"JetBrains Mono", monospace',
            color:      metric.accent ?? 'var(--text-primary)',
            ...(isFlashing ? { animation: 'kpiFlash 500ms ease-out' } : {}),
          }}
        >
          {metric.value}
        </span>
      )}

      {metric.sub && (
        <span
          className="text-[9px] leading-none truncate"
          style={{ color: 'var(--text-faint)' }}
        >
          {metric.sub}
        </span>
      )}
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export function TelemetryMetricRow({ metrics, columns = 4, className }: Props) {
  const { isLCARS } = useTheme()

  if (isLCARS) {
    return (
      <div className={`lc-telemetry-strip ${className ?? ''}`}>
        {metrics.map(m => (
          <LCARSCell key={m.key} metric={m} />
        ))}
      </div>
    )
  }

  const cols = Math.min(columns, metrics.length)
  const rows: TelemetryMetric[][] = []
  for (let i = 0; i < metrics.length; i += cols) {
    rows.push(metrics.slice(i, i + cols))
  }

  return (
    <div
      className={`mission-card glass overflow-hidden ${className ?? ''}`}
      style={{ border: '1px solid var(--border-default)' }}
    >
      {rows.map((rowItems, ri) => (
        <div
          key={ri}
          className="grid"
          style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
        >
          {rowItems.map((m, ci) => (
            <ModernCell
              key={m.key}
              metric={m}
              borderRight={ci < rowItems.length - 1}
              borderBottom={ri < rows.length - 1}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
