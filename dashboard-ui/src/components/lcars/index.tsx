/*
 * LCARS Global Primitive Components
 *
 * These work in both LCARS and modern themes via CSS vars, but are designed
 * for maximum impact in LCARS mode. Use them from any page; they do not
 * depend on the .lcars-core page namespace.
 *
 * Exports:
 *   LCARSSection       — content section panel with capsule header
 *   LCARSMetric        — large operational number
 *   LCARSDataRow       — key / value row
 *   LCARSDistBar       — horizontal distribution bar
 *   LCARSStatusDot     — coloured status indicator dot
 *   LCARSFrame         — full-page frame (header strip + content area)
 */
import type { ReactNode, CSSProperties } from 'react'

// ── LCARSSection ─────────────────────────────────────────────────────────────

interface LCARSSectionProps {
  label: string
  color?: string
  className?: string
  style?: CSSProperties
  headerRight?: ReactNode
  children: ReactNode
}

export function LCARSSection({
  label,
  color = '#FF9900',
  className = '',
  style,
  headerRight,
  children,
}: LCARSSectionProps) {
  return (
    <div className={`lc-g-section ${className}`} style={style}>
      <div className="lc-g-header">
        <div className="lc-g-header-pill" style={{ background: color }}>
          {label}
        </div>
        {headerRight && (
          <div className="lc-g-header-tail">
            {headerRight}
          </div>
        )}
      </div>
      <div className="p-4">
        {children}
      </div>
    </div>
  )
}

// ── LCARSMetric ──────────────────────────────────────────────────────────────

interface LCARSMetricProps {
  value:  string | number | null | undefined
  label:  string
  color?: string
  size?:  'lg' | 'md' | 'sm' | 'xs'
  className?: string
}

export function LCARSMetric({
  value,
  label,
  color,
  size = 'md',
  className = '',
}: LCARSMetricProps) {
  return (
    <div className={`lc-g-metric ${className}`}>
      <span className={`lc-g-metric-value ${size}`} style={color ? { color } : undefined}>
        {value ?? '—'}
      </span>
      <span className="lc-g-metric-label">{label}</span>
    </div>
  )
}

// ── LCARSDataRow ─────────────────────────────────────────────────────────────

interface LCARSDataRowProps {
  label: string
  value: string | number | null | undefined
  color?: string
}

export function LCARSDataRow({ label, value, color }: LCARSDataRowProps) {
  return (
    <div className="lc-g-row">
      <span className="lc-g-row-key">{label}</span>
      <span className="lc-g-row-val" style={color ? { color } : undefined}>
        {value ?? '—'}
      </span>
    </div>
  )
}

// ── LCARSDistBar ─────────────────────────────────────────────────────────────

interface LCARSDistBarProps {
  value:     number   // 0–100
  color?:    string
  className?: string
}

export function LCARSDistBar({ value, color = '#FF9900', className = '' }: LCARSDistBarProps) {
  return (
    <div className={`lc-g-distbar ${className}`}>
      <div
        className="lc-g-distbar-fill"
        style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }}
      />
    </div>
  )
}

// ── LCARSStatusDot ───────────────────────────────────────────────────────────

interface LCARSStatusDotProps {
  color:   string
  blink?:  boolean
  size?:   number
}

export function LCARSStatusDot({ color, blink = false, size = 6 }: LCARSStatusDotProps) {
  return (
    <span
      className={`lc-g-dot${blink ? ' blink' : ''}`}
      style={{ background: color, width: size, height: size }}
    />
  )
}

// ── LCARSFrame ───────────────────────────────────────────────────────────────
//
// Full-page frame for pages that want the complete LCARS console look without
// adopting the elaborate .lcars-core ComputerCore chrome. Provides:
//   • a top strip with elbow + header label + optional right-side actions
//   • a scrollable content area
//
// Usage:
//   <LCARSFrame title="SCANNER FLEET" color="#9999FF" actions={<button>...</button>}>
//     ... page content ...
//   </LCARSFrame>

interface LCARSFrameProps {
  title:    string
  color?:   string
  actions?: ReactNode
  children: ReactNode
}

export function LCARSFrame({ title, color = '#FF9900', actions, children }: LCARSFrameProps) {
  return (
    <div
      style={{
        display:       'flex',
        flexDirection: 'column',
        height:        '100%',
        background:    'rgba(1, 8, 24, 0.98)',
      }}
    >
      {/* Header strip */}
      <div
        style={{
          display:     'flex',
          alignItems:  'stretch',
          height:      '40px',
          flexShrink:  0,
          background:  'rgba(0, 3, 14, 0.95)',
          borderBottom: '1px solid rgba(255,153,0,0.12)',
        }}
      >
        <div
          style={{
            display:       'flex',
            alignItems:    'center',
            padding:       '0 20px',
            background:    color,
            borderRadius:  '0 20px 20px 0',
            fontFamily:    "'Antonio', 'Inter', sans-serif",
            fontSize:      '13px',
            fontWeight:    600,
            letterSpacing: '0.20em',
            textTransform: 'uppercase',
            color:         'rgba(0, 5, 20, 0.90)',
            flexShrink:    0,
          }}
        >
          {title}
        </div>
        {actions && (
          <div
            style={{
              flex:        1,
              display:     'flex',
              alignItems:  'center',
              justifyContent: 'flex-end',
              padding:     '0 16px',
              gap:         '8px',
            }}
          >
            {actions}
          </div>
        )}
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px' }}>
        {children}
      </div>
    </div>
  )
}
