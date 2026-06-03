import type { ReactNode } from 'react'

/*
 * PageHeader — operational page identifier.
 *
 * Design notes for this revision:
 *   - mb-8 → mb-5: the previous 32px gap was SaaS-app whitespace.
 *     Mission consoles prioritize vertical scan density.
 *   - text-2xl → text-xl: 24px hero titles read as marketing copy.
 *     20px keeps clear hierarchy without competing with content.
 *   - Decorative accent underline removed: the gradient rule below the
 *     title was a SaaS landing page pattern. The accent tag above the
 *     title already supplies color identity.
 */

interface Props {
  title: string
  subtitle?: string
  actions?: ReactNode
  tag?: string
}

export function PageHeader({ title, subtitle, actions, tag }: Props) {
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
          <p className="text-[10px] mt-0.5 font-mono" style={{ color: 'var(--text-faint)', letterSpacing: '0.04em' }}>
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
