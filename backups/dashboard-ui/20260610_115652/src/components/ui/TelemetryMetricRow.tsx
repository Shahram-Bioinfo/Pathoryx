import { useFlashOnChange } from '../../hooks/useFlashOnChange'

/*
 * TelemetryMetricRow — replaces isolated KpiCard grids with a unified
 * operational instrument panel.
 *
 * Design rationale vs. KpiCard:
 *
 *   KpiCard: each metric is a standalone "card" with its own shadow, hover,
 *   accent gradient, and icon — equal-weight BI dashboard aesthetics.
 *
 *   TelemetryMetricRow: all metrics share one glass surface; cells are
 *   separated only by thin lines — an instrument cluster, not a widget grid.
 *   Values are 18 px (vs. KpiCard's 28 px) — readout values, not hero numbers.
 *   No per-cell shadow, hover, or decorative accent lines.
 *
 * Layout: up to 4 metrics per row. For 5–8 metrics, rows wrap automatically.
 * Each row is a grid of cells; rows are separated by a 1 px horizontal line.
 *
 * Flash: values call useFlashOnChange so they still signal data updates —
 * the same kpiFlash keyframe fires, but on a smaller number so it reads
 * as "telemetry refresh" rather than "dashboard update".
 */

export interface TelemetryMetric {
  key:     string
  label:   string
  value:   string
  /** Optional subtext beneath the value (small, faint). */
  sub?:    string
  /** CSS color or var() for non-default (warning/error/success) states. */
  accent?: string
  loading?: boolean
}

interface Props {
  metrics:  TelemetryMetric[]
  /** Cells per row. Defaults to min(4, metrics.length). */
  columns?: 2 | 3 | 4 | 6
  className?: string
}

// ── Individual cell (hook must be in its own component) ──────────────────────

function MetricCell({
  metric,
  borderRight,
  borderBottom,
}: {
  metric:      TelemetryMetric
  borderRight: boolean
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
      {/* Label — 9 px monospace, wide tracking, faint */}
      <span
        className="text-[9px] font-mono tracking-[0.18em] uppercase leading-none truncate"
        style={{ color: 'var(--text-faint)' }}
      >
        {metric.label}
      </span>

      {/* Value */}
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

      {/* Subtext */}
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
  const cols = Math.min(columns, metrics.length)

  // Split metrics into rows of `cols`
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
          style={{
            gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
          }}
        >
          {rowItems.map((m, ci) => (
            <MetricCell
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
