import clsx from 'clsx'
import type { LucideIcon } from 'lucide-react'
import { useFlashOnChange } from '../../hooks/useFlashOnChange'

interface Props {
  label: string
  value: string | number
  icon: LucideIcon
  accent?: 'cyan' | 'teal' | 'amber' | 'rose' | 'violet' | 'emerald'
  subtext?: string
  trend?: 'up' | 'down' | 'neutral'
  loading?: boolean
}

// CSS-variable-based accent colours — switch with theme automatically
const ACCENT_VAR: Record<NonNullable<Props['accent']>, string> = {
  cyan:    'var(--stage-intake-color)',
  teal:    'var(--stage-upload-color)',
  amber:   'var(--chart-amber)',
  rose:    'var(--chart-rose)',
  violet:  'var(--stage-dicom-color)',
  emerald: 'var(--chart-emerald)',
}

// Icon classes use both light and dark text-color variants
const ICON_CLASS: Record<NonNullable<Props['accent']>, string> = {
  cyan:    'text-sky-600 dark:text-cyan-400',
  teal:    'text-teal-600 dark:text-teal-400',
  amber:   'text-amber-600 dark:text-amber-400',
  rose:    'text-rose-600 dark:text-rose-400',
  violet:  'text-violet-600 dark:text-violet-400',
  emerald: 'text-emerald-600 dark:text-emerald-400',
}

export function KpiCard({ label, value, icon: Icon, accent = 'cyan', subtext, trend, loading }: Props) {
  const accentColor = ACCENT_VAR[accent]
  const isFlashing  = useFlashOnChange(loading ? '—' : String(value))

  return (
    <div className="mission-card glass-hover animate-entry" style={{ animationFillMode: 'both' }}>
      {/* Decorative top line — uses the per-accent CSS var, adapts with theme */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: 0, left: '14%', right: '14%',
          height: '1px',
          background: `linear-gradient(90deg, transparent, ${accentColor}55, transparent)`,
          pointerEvents: 'none',
        }}
      />

      <div className="p-5">
        <div className="flex items-start justify-between mb-4">
          <p className="section-label mb-0 leading-none">{label}</p>
          <Icon
            className={clsx('h-3.5 w-3.5 opacity-50', ICON_CLASS[accent])}
            aria-hidden
          />
        </div>

        {loading ? (
          <div className="ops-skeleton h-8 w-28 rounded" />
        ) : (
          <p
            className="text-[28px] font-semibold leading-none tracking-tight tabular"
            style={{
              fontFamily: '"JetBrains Mono", monospace',
              color: 'var(--text-primary)',
              ...(isFlashing && { animation: 'kpiFlash 500ms ease-out' }),
            }}
          >
            {value}
          </p>
        )}

        {subtext && (
          <p
            className={clsx('text-xs mt-2.5 leading-none', {
              'text-emerald-600 dark:text-emerald-400': trend === 'up',
              'text-rose-600    dark:text-rose-400':    trend === 'down',
            })}
            style={!trend ? { color: 'var(--text-muted)' } : undefined}
          >
            {subtext}
          </p>
        )}
      </div>
    </div>
  )
}
