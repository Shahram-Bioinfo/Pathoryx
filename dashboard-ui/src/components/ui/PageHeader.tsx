import type { ReactNode } from 'react'
import { useTheme } from '../layout/ThemeProvider'

interface Props {
  title:     string
  subtitle?: string
  actions?:  ReactNode
  tag?:      string
}

export function PageHeader({ title, subtitle, actions, tag }: Props) {
  const { isLCARS } = useTheme()

  if (isLCARS) {
    return (
      <div className="lc-page-hdr">
        {/* Title capsule pill */}
        <div className="lc-page-hdr-pill">
          {title}
        </div>

        {/* Sub-labels */}
        {(tag || subtitle) && (
          <div className="lc-page-hdr-sub">
            {tag && <span>{tag}</span>}
            {tag && subtitle && <span className="lc-page-hdr-sep">▪</span>}
            {subtitle && <span>{subtitle}</span>}
          </div>
        )}

        {/* Right side: actions + status */}
        <div className="lc-page-hdr-meta">
          {actions}
          <span className="lc-page-hdr-status">
            <span className="lc-page-hdr-status-dot" aria-hidden />
            ONLINE
          </span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex items-start justify-between mb-5">
      <div>
        {tag && (
          <p
            className="text-[9px] font-semibold uppercase mb-1.5"
            style={{ letterSpacing: '0.28em', color: 'var(--accent)' }}
          >
            {tag}
          </p>
        )}
        <h1
          className="text-xl font-semibold tracking-tight"
          style={{ color: 'var(--text-primary)' }}
        >
          {title}
        </h1>
        {subtitle && (
          <p
            className="text-[10px] mt-0.5 font-mono"
            style={{ color: 'var(--text-faint)', letterSpacing: '0.04em' }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
