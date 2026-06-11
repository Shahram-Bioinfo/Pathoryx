import { useQueryClient } from '@tanstack/react-query'
import { Cpu, LayoutDashboard, RefreshCw, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'
import { format } from 'date-fns'
import { useTheme } from './ThemeProvider'
import { useSSE } from '../../hooks/useSSE'
import { useEnvironmentConfig } from '../../hooks/useOperations'

// ── Mission clock ──────────────────────────────────────────────────────────────

function MissionClock({ lcars }: { lcars?: boolean }) {
  const [time, setTime] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  if (lcars) {
    return (
      <div className="hidden sm:flex flex-col items-end select-none gap-0" aria-label="Mission clock">
        <time
          className="tabular-nums leading-none"
          style={{
            fontFamily: "'Antonio', 'Inter', sans-serif",
            fontSize: 17,
            letterSpacing: '0.06em',
            color: 'rgba(0,5,20,0.90)',
          }}
          dateTime={time.toISOString()}
        >
          {format(time, 'HH:mm:ss')}
        </time>
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 8,
          letterSpacing: '0.22em',
          color: 'rgba(0,5,20,0.48)',
          textTransform: 'uppercase',
        }}>
          UTC
        </span>
      </div>
    )
  }

  return (
    <div className="hidden sm:flex items-center gap-2 select-none">
      <span
        className="text-[9px] tracking-[0.2em] font-medium"
        style={{ color: 'var(--text-faint)', letterSpacing: '0.18em' }}
        aria-hidden
      >UTC</span>
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

// ── SSE indicator ──────────────────────────────────────────────────────────────

const SSE_LABEL: Record<string, string> = { live: 'LIVE', reconnecting: 'SYNC', offline: 'OFFLINE' }
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

function SseIndicator({ lcars }: { lcars?: boolean }) {
  const { status } = useSSE()

  if (lcars) {
    const dotColor =
      status === 'live'         ? '#1A6640' :
      status === 'reconnecting' ? '#7A4800' :
                                   'rgba(0,5,20,0.35)'
    const textColor =
      status === 'live'         ? 'rgba(0,30,15,0.90)' :
      status === 'reconnecting' ? 'rgba(80,40,0,0.90)' :
                                   'rgba(0,5,20,0.48)'

    return (
      <div
        className="hidden sm:flex flex-col items-end gap-0 select-none"
        title={status === 'live' ? 'Realtime stream connected' : 'Stream ' + status}
      >
        <div className="flex items-center gap-1.5">
          <span
            className="inline-block rounded-full flex-shrink-0"
            style={{
              width: 6, height: 6,
              background: dotColor,
              animation: status === 'live' ? 'lcBlink 2.8s ease-in-out infinite' : undefined,
            }}
            aria-hidden
          />
          <span style={{
            fontFamily: "'Antonio', 'Inter', sans-serif",
            fontSize: 11,
            letterSpacing: '0.18em',
            color: textColor,
          }}>
            {SSE_LABEL[status]}
          </span>
        </div>
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 7,
          letterSpacing: '0.20em',
          color: 'rgba(0,5,20,0.35)',
          textTransform: 'uppercase',
        }}>
          DATASTREAM
        </span>
      </div>
    )
  }

  return (
    <span
      className="hidden sm:inline-flex items-center gap-1.5"
      title={status === 'live' ? 'Realtime stream connected' : 'Stream ' + status}
    >
      <span
        className={`inline-block h-1.5 w-1.5 rounded-full flex-shrink-0 ${SSE_DOT_CLASS[status]}`}
        style={status === 'live' ? { animation: 'pulseStatus 2.8s ease-in-out infinite', animationDelay: '0.45s' } : undefined}
        aria-hidden
      />
      <span
        className="text-[9px] font-semibold"
        style={{ letterSpacing: '0.18em', fontVariantCaps: 'all-small-caps', color: SSE_LABEL_COLOR[status] }}
      >
        {SSE_LABEL[status]}
      </span>
    </span>
  )
}

// ── Environment chip ───────────────────────────────────────────────────────────

function EnvironmentChip({ lcars }: { lcars?: boolean }) {
  const { data: env } = useEnvironmentConfig()
  if (!env) return null

  const isProd  = env.environment === 'production' || env.environment === 'prod'
  const isDryRun = env.upload_dry_run

  if (lcars) {
    const color = isProd ? 'rgba(90,0,0,0.90)' : 'rgba(0,5,20,0.75)'
    return (
      <div className="hidden sm:flex flex-col items-end gap-0 select-none">
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 11,
          letterSpacing: '0.18em',
          color,
          fontWeight: isProd ? 700 : 400,
        }}>
          {env.environment.toUpperCase().slice(0, 6)}
        </span>
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 7,
          letterSpacing: '0.20em',
          color: 'rgba(0,5,20,0.40)',
          textTransform: 'uppercase',
        }}>
          {isDryRun ? 'DRY-RUN' : 'LIVE UPLOAD'}
        </span>
      </div>
    )
  }

  const color  = isProd ? 'var(--chart-rose)' : 'var(--chart-amber)'
  const bg     = isProd ? 'rgba(225,29,72,0.07)' : 'rgba(217,119,6,0.07)'
  const border = isProd ? 'rgba(225,29,72,0.20)' : 'rgba(217,119,6,0.20)'

  return (
    <span
      className="hidden sm:inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-[9px] font-semibold uppercase tracking-[0.15em]"
      style={{ color, background: bg, border: `1px solid ${border}` }}
    >
      {env.environment.slice(0, 4).toUpperCase()}
      {isDryRun
        ? <span style={{ color: 'var(--chart-teal)', fontSize: '8px' }}>DRY</span>
        : <span style={{ color: 'var(--chart-rose)', fontSize: '8px' }}>LIVE</span>}
    </span>
  )
}

// ── LCARS Command TopBar (full-width, unified orange) ─────────────────────────

function LCARSTopBar() {
  const qc = useQueryClient()
  const { theme, setTheme } = useTheme()
  const [logoErr, setLogoErr] = useState(false)

  // Dark divider on orange
  const dividerStyle: React.CSSProperties = {
    width: 1, height: 28,
    background: 'rgba(0,5,20,0.18)',
    flexShrink: 0,
  }

  const THEMES = [
    { value: 'light'  as const, icon: Sun,             label: 'Light'  },
    { value: 'modern' as const, icon: LayoutDashboard, label: 'Dark'   },
    { value: 'lcars'  as const, icon: Cpu,             label: 'LCARS'  },
  ]

  return (
    <header
      className="lc-cmd-bar fixed top-0 left-0 right-0 z-40 flex"
      style={{ height: 96, background: '#FF9900', borderBottom: '3px solid rgba(0,5,20,0.18)' }}
      aria-label="DPARS Command Bar"
    >
      {/* ── Left zone — brand identity (unified, no card separation) ───── */}
      <div
        className="flex items-center justify-center flex-shrink-0"
        style={{
          width: 248,
          background: '#FF9900',
          paddingLeft: 14,
          paddingRight: 14,
        }}
      >
        {logoErr ? (
          /* Fallback: large white DPARS text */
          <div style={{
            border: '1px solid rgba(120,85,40,0.35)',
            borderRadius: 6,
            padding: '8px 16px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            width: '100%',
          }}>
            <span style={{
              fontFamily: "'Antonio', 'Inter', sans-serif",
              fontSize: 36, fontWeight: 900, letterSpacing: '0.12em',
              color: '#FFFFFF', textTransform: 'uppercase', lineHeight: 1,
            }}>DPARS</span>
          </div>
        ) : (
          /* Logo inside bronze frame — no text below */
          <div style={{
            border: '1px solid rgba(120,85,40,0.35)',
            borderRadius: 6,
            padding: '5px 10px',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(255,255,255,0.04)',
          }}>
            <img
              src="/dpars-logo.png"
              alt="DPARS"
              onError={() => setLogoErr(true)}
              style={{
                height: 80,
                width: 'auto',
                maxWidth: 210,
                objectFit: 'contain',
                objectPosition: 'center center',
              }}
            />
          </div>
        )}
      </div>

      {/* Vertical separator */}
      <div style={{ width: 1, background: 'rgba(0,5,20,0.14)', flexShrink: 0, margin: '12px 0' }} aria-hidden />

      {/* ── Right command rail — same orange, dark controls ──────────────── */}
      <div
        className="flex flex-1 items-center"
        style={{
          background: '#FF9900',
          paddingLeft: 28,
          paddingRight: 20,
        }}
      >
        {/* System identification */}
        <div className="flex-1 min-w-0">
          <div style={{
            fontFamily: "'Antonio', 'Inter', sans-serif",
            fontSize: 22, fontWeight: 900, letterSpacing: '0.12em',
            textTransform: 'uppercase',
            color: '#FFFFFF',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            lineHeight: 1,
          }}>
            DPARS
          </div>
          <div style={{
            fontFamily: "'Antonio', 'Inter', sans-serif",
            fontSize: 10, letterSpacing: '0.16em',
            textTransform: 'uppercase',
            color: 'rgba(255,255,255,0.65)', marginTop: 5,
            lineHeight: 1,
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          }}>
            Digital Pathology Analysis &amp; Retrieval System
          </div>
        </div>

        {/* Right controls group */}
        <div className="flex items-center" style={{ gap: 18, flexShrink: 0 }}>

          <EnvironmentChip lcars />

          <div style={dividerStyle} />

          <MissionClock lcars />

          <div style={dividerStyle} />

          <SseIndicator lcars />

          <div style={dividerStyle} />

          {/* Refresh */}
          <button
            onClick={() => qc.invalidateQueries()}
            aria-label="Refresh all data"
            style={{
              padding: '6px 11px',
              background: 'rgba(0,5,20,0.10)',
              border: '1.5px solid rgba(0,5,20,0.20)',
              borderRadius: 4,
              color: 'rgba(0,5,20,0.70)',
              cursor: 'pointer',
              display: 'flex', alignItems: 'center',
            }}
          >
            <RefreshCw style={{ width: 14, height: 14 }} aria-hidden />
          </button>

          {/* Theme toggle — 3 options */}
          <div
            className="flex items-center overflow-hidden"
            style={{ border: '1.5px solid rgba(0,5,20,0.22)', borderRadius: 4 }}
            role="group"
            aria-label="UI theme"
          >
            {THEMES.map(({ value, icon: Icon, label }, i) => (
              <button
                key={value}
                onClick={() => setTheme(value)}
                className="p-2 flex items-center justify-center"
                aria-label={`Switch to ${label} mode`}
                aria-pressed={theme === value}
                title={label}
                style={{
                  background: theme === value ? 'rgba(0,5,20,0.22)' : 'transparent',
                  color:      theme === value ? 'rgba(0,5,20,0.94)' : 'rgba(0,5,20,0.48)',
                  borderLeft: i > 0 ? '1px solid rgba(0,5,20,0.18)' : 'none',
                  transition: 'background 150ms ease, color 150ms ease',
                  cursor: 'pointer',
                }}
              >
                <Icon style={{ width: 14, height: 14 }} aria-hidden />
              </button>
            ))}
          </div>

          {/* Operator avatar */}
          <div
            style={{
              width: 32, height: 32,
              borderRadius: 4,
              background: 'rgba(0,5,20,0.14)',
              border: '1.5px solid rgba(0,5,20,0.28)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
            aria-hidden
          >
            <span style={{
              fontFamily: "'Antonio', 'Inter', sans-serif",
              fontSize: 14, fontWeight: 800, letterSpacing: '0.08em',
              color: 'rgba(0,5,20,0.82)',
            }}>OP</span>
          </div>
        </div>
      </div>
    </header>
  )
}

// ── TopBar (mode-aware) ───────────────────────────────────────────────────────

export function TopBar() {
  const { isLCARS, theme, setTheme } = useTheme()
  const qc = useQueryClient()

  if (isLCARS) return <LCARSTopBar />

  const THEMES = [
    { value: 'light'  as const, icon: Sun,             label: 'Light mode'  },
    { value: 'modern' as const, icon: LayoutDashboard, label: 'Modern dark' },
    { value: 'lcars'  as const, icon: Cpu,             label: 'LCARS mode'  },
  ]

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
        <EnvironmentChip />

        <div className="h-3.5 w-px hidden sm:block" style={{ background: 'var(--border-default)' }} aria-hidden />
        <MissionClock />
        <div className="h-3.5 w-px hidden sm:block" style={{ background: 'var(--border-default)' }} aria-hidden />
        <SseIndicator />
        <div className="h-3.5 w-px hidden sm:block" style={{ background: 'var(--border-default)' }} aria-hidden />

        <button onClick={() => qc.invalidateQueries()} className="btn-ghost-ops" aria-label="Refresh all data">
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          <span className="hidden sm:inline text-[11px]">Refresh</span>
        </button>

        <div
          className="flex items-center rounded-lg overflow-hidden"
          style={{ border: '1px solid var(--border-default)' }}
          role="group"
          aria-label="UI theme"
        >
          {THEMES.map(({ value, icon: Icon, label }, i) => (
            <button
              key={value}
              onClick={() => setTheme(value)}
              className="p-1.5 flex items-center justify-center"
              aria-label={label}
              aria-pressed={theme === value}
              title={label}
              style={{
                background: theme === value ? 'var(--accent-faint)' : 'transparent',
                color:      theme === value ? 'var(--accent)'       : 'var(--text-faint)',
                borderLeft: i > 0 ? '1px solid var(--border-default)' : 'none',
                transition: 'background 150ms ease, color 150ms ease',
              }}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden />
            </button>
          ))}
        </div>

        <div
          className="h-6 w-6 rounded-full flex items-center justify-center flex-shrink-0"
          style={{ background: 'var(--accent-faint)', border: '1px solid var(--border-strong)' }}
          aria-hidden
        >
          <span className="text-[10px] font-semibold" style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--accent)' }}>P</span>
        </div>
      </div>
    </header>
  )
}
