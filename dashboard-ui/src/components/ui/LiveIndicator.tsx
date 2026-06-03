import clsx from 'clsx'

interface Props {
  status?: 'live' | 'active' | 'warning' | 'error' | 'offline'
  label?: string
  className?: string
}

const DOT_COLOR: Record<NonNullable<Props['status']>, string> = {
  live:    'var(--chart-cyan)',
  active:  'var(--chart-emerald)',
  warning: 'var(--chart-amber)',
  error:   'var(--chart-rose)',
  offline: 'var(--text-faint)',
}

const LABEL_COLOR: Record<NonNullable<Props['status']>, string> = {
  live:    'var(--accent)',
  active:  'var(--chart-emerald)',
  warning: 'var(--chart-amber)',
  error:   'var(--chart-rose)',
  offline: 'var(--text-faint)',
}

const DEFAULT_LABEL: Record<NonNullable<Props['status']>, string> = {
  live:    'LIVE',
  active:  'ACTIVE',
  warning: 'WARNING',
  error:   'ALERT',
  offline: 'OFFLINE',
}

/*
 * phaseSecs — deterministic phase offset in seconds derived from a string.
 *
 * Uses a simple polynomial hash (DJB2-lite) to scatter different label strings
 * across a 0–2.79 s window (matching the default 2.8 s pulseStatus period).
 * The same label always produces the same phase, so re-renders are stable.
 *
 * Purpose: when multiple LiveIndicators exist on the same page they pulse at
 * independent phases — the system feels like N separate subsystems, not a
 * single choreographed heartbeat.
 */
function phaseSecs(label: string): number {
  let h = 5381
  for (let i = 0; i < label.length; i++) {
    h = ((h << 5) + h + label.charCodeAt(i)) >>> 0
  }
  return (h % 280) / 100   // [0.00, 2.79)
}

/*
 * pulsePeriod — each status class uses a slightly different cycle duration.
 *
 * 'live'    → 2.8 s   (canonical operational heartbeat)
 * 'active'  → 3.1 s   (slightly slower — "settled running state")
 * 'warning' → 2.4 s   (marginally faster — attention without alarm)
 * 'error'   → 1.9 s   (clearly faster — diagnostic urgency, not panic)
 *
 * These differences (< 0.7 s) are below conscious detection individually
 * but create measurably distinct rhythms across subsystems viewed together.
 */
const PULSE_PERIOD: Record<NonNullable<Props['status']>, number> = {
  live:    2.8,
  active:  3.1,
  warning: 2.4,
  error:   1.9,
  offline: 0,
}

export function LiveIndicator({ status = 'live', label, className }: Props) {
  const animate      = status === 'live' || status === 'active' ||
                       status === 'warning' || status === 'error'
  const displayLabel = label ?? DEFAULT_LABEL[status]
  const phase        = phaseSecs(displayLabel)
  const period       = PULSE_PERIOD[status]

  return (
    <span className={clsx('inline-flex items-center gap-2', className)}>
      <span
        className="inline-block h-1.5 w-1.5 rounded-full flex-shrink-0"
        style={{
          background: DOT_COLOR[status],
          ...(animate && period > 0 ? {
            animation:      `pulseStatus ${period}s ease-in-out infinite`,
            animationDelay: `${phase}s`,
            willChange:     'opacity, transform',
          } : undefined),
        }}
      />
      <span
        className="text-[10px] font-semibold"
        style={{ letterSpacing: '0.18em', fontVariantCaps: 'all-small-caps', color: LABEL_COLOR[status] }}
      >
        {displayLabel}
      </span>
    </span>
  )
}
