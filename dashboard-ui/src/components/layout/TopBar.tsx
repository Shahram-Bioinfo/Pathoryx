import { useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { Moon, RefreshCw, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTheme } from './ThemeProvider'
import { useSSE } from '../../hooks/useSSE'
import { useEnvironmentConfig } from '../../hooks/useOperations'

// ── Mission clock ─────────────────────────────────────────────────────────────

function MissionClock() {
  const [time, setTime] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <div className="hidden sm:flex items-center gap-2 select-none">
      <span
        className="text-[9px] tracking-[0.2em] font-medium"
        style={{ color: 'var(--text-faint)', letterSpacing: '0.18em' }}
        aria-hidden
      >
        UTC
      </span>
      <time
        className="text-[11px] font-medium tabular"
        style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--text-muted)' }}
        dateTime={time.toISOString()}
      >
        {format(time, 'HH:mm:ss')}
      </time>
    </div>
  )
}

// ── SSE connection indicator ──────────────────────────────────────────────────
//
// Three states, all visually quiet — this is infrastructure status, not a
// notification.  Consistent with the existing LiveIndicator aesthetic: a small
// dot + a short monospace label.
//
//   live         — emerald dot + "LIVE"        (pulseStatus animation)
//   reconnecting — amber dot + "SYNC"          (no animation — transient state)
//   offline      — faint dot + "OFFLINE"       (static — something is wrong)

const SSE_DOT_CLASS: Record<string, string> = {
  live:         'bg-emerald-500 dark:bg-emerald-400',
  reconnecting: 'bg-amber-500 dark:bg-amber-400',
  offline:      'bg-slate-400 dark:bg-slate-500',
}

const SSE_LABEL_COLOR: Record<string, string> = {
  live:         'var(--chart-emerald)',
  reconnecting: 'var(--chart-amber)',
  offline:      'var(--text-faint)',
}

const SSE_LABEL: Record<string, string> = {
  live:         'LIVE',
  reconnecting: 'SYNC',
  offline:      'OFFLINE',
}

function SseIndicator() {
  const { status } = useSSE()

  return (
    <span
      className="hidden sm:inline-flex items-center gap-1.5"
      title={
        status === 'live'         ? 'Realtime stream connected'
        : status === 'reconnecting' ? 'Reconnecting to realtime stream…'
        : 'Realtime stream offline — polling fallback active'
      }
      aria-label={`Stream status: ${status}`}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full flex-shrink-0 ${SSE_DOT_CLASS[status]}`}
        style={
          status === 'live'
            ? {
                animation:      'pulseStatus 2.8s ease-in-out infinite',
                animationDelay: '0.45s',
                willChange:     'opacity, transform',
              }
            : undefined
        }
        aria-hidden
      />
      <span
        className="text-[9px] font-semibold"
        style={{
          letterSpacing: '0.18em',
          fontVariantCaps: 'all-small-caps',
          color: SSE_LABEL_COLOR[status],
        }}
      >
        {SSE_LABEL[status]}
      </span>
    </span>
  )
}

// ── TopBar ────────────────────────────────────────────────────────────────────

function EnvironmentChip() {
  const { data: env } = useEnvironmentConfig()
  if (!env) return null

  const isProd     = env.environment === 'production' || env.environment === 'prod'
  const isDryRun   = env.upload_dry_run
  const color      = isProd ? 'var(--chart-rose)' : 'var(--chart-amber)'
  const bg         = isProd ? 'rgba(225,29,72,0.07)' : 'rgba(217,119,6,0.07)'
  const border     = isProd ? 'rgba(225,29,72,0.20)' : 'rgba(217,119,6,0.20)'

  return (
    <span
      className="hidden sm:inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-semibold uppercase tracking-[0.15em]"
      style={{ color, background: bg, border: `1px solid ${border}` }}
      title={`Environment: ${env.environment} — Upload: ${isDryRun ? 'dry-run' : 'LIVE'}`}
    >
      {env.environment.slice(0, 4).toUpperCase()}
      {isDryRun
        ? <span style={{ color: 'var(--chart-teal)', fontSize: '8px' }}>DRY</span>
        : <span style={{ color: 'var(--chart-rose)', fontSize: '8px' }}>LIVE</span>}
    </span>
  )
}

export function TopBar() {
  const { theme, toggle } = useTheme()
  const qc = useQueryClient()

  return (
    <header
      className="fixed top-0 right-0 left-60 z-20 flex h-12 items-center px-5"
      style={{
        background:           'var(--bg-overlay)',
        borderBottom:         '1px solid var(--border-faint)',
        boxShadow:            'var(--topbar-shadow)',
        backdropFilter:       'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        transition:           'background 200ms ease, box-shadow 200ms ease',
      }}
    >
      <div className="flex-1" />

      <div className="flex items-center gap-3">
        {/* Environment + upload mode chip — always visible */}
        <EnvironmentChip />

        <div
          className="h-3.5 w-px hidden sm:block"
          style={{ background: 'var(--border-default)' }}
          aria-hidden
        />

        <MissionClock />

        <div
          className="h-3.5 w-px hidden sm:block"
          style={{ background: 'var(--border-default)' }}
          aria-hidden
        />

        {/* SSE connection status */}
        <SseIndicator />

        <div
          className="h-3.5 w-px hidden sm:block"
          style={{ background: 'var(--border-default)' }}
          aria-hidden
        />

        <button
          onClick={() => qc.invalidateQueries()}
          className="btn-ghost-ops"
          aria-label="Refresh all data"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          <span className="hidden sm:inline text-[11px]">Refresh</span>
        </button>

        <button
          onClick={toggle}
          className="btn-ghost-ops p-2"
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark'
            ? <Sun  className="h-3.5 w-3.5" aria-hidden />
            : <Moon className="h-3.5 w-3.5" aria-hidden />}
        </button>

        {/* Avatar — wire to auth context when RBAC is configured */}
        <div
          className="h-6 w-6 rounded-full flex items-center justify-center flex-shrink-0"
          style={{
            background: 'var(--accent-faint)',
            border:     '1px solid var(--border-strong)',
          }}
          aria-hidden
        >
          <span
            className="text-[10px] font-semibold"
            style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--accent)' }}
          >
            P
          </span>
        </div>
      </div>
    </header>
  )
}
