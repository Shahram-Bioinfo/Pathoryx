import { useState } from 'react'
import {
  Activity, AlertTriangle, CloudUpload, GitBranch,
  Layers, LayoutDashboard, MonitorPlay, RefreshCcw, ShieldCheck,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { LiveIndicator } from '../ui/LiveIndicator'
import { useTheme } from './ThemeProvider'

const NAV = [
  { to: '/',              label: 'Overview',          icon: LayoutDashboard, lcarsColor: '#CC9966' },
  { to: '/slides',        label: 'Slide Explorer',    icon: Layers,          lcarsColor: '#9999FF' },
  { to: '/queues',        label: 'Queue Monitor',     icon: Activity,        lcarsColor: '#FF9900' },
  { to: '/failures',      label: 'Failure Center',    icon: AlertTriangle,   lcarsColor: '#CC3333' },
  { to: '/recovery',      label: 'Recovery Center',   icon: RefreshCcw,      lcarsColor: '#33CC99' },
  { to: '/operations',    label: 'Operations',        icon: ShieldCheck,     lcarsColor: '#6699FF' },
  { to: '/uploads',       label: 'Upload Operations', icon: CloudUpload,     lcarsColor: '#CC99FF' },
  { to: '/computer-core', label: 'Computer Core',     icon: MonitorPlay,     lcarsColor: '#00AADD' },
  { to: '/routing',       label: 'Routing Engine',    icon: GitBranch,       lcarsColor: '#FFCC44' },
]

// ── LCARS Vertical Command Rail ───────────────────────────────────────────────

function LCARSSidebar() {
  return (
    <aside
      className="fixed left-0 bottom-0 z-30 flex w-60 flex-col"
      style={{
        top: 96,
        background: 'rgba(1,8,28,0.99)',
        borderRight: '1px solid rgba(255,153,0,0.14)',
      }}
      aria-label="DPARS navigation rail"
    >
      {/* Structural orange left spine */}
      <div
        className="absolute left-0 top-0 bottom-0"
        style={{
          width: 10,
          background: 'linear-gradient(to bottom, #FF9900 0%, rgba(255,153,0,0.55) 40%, rgba(255,153,0,0.18) 100%)',
          zIndex: 1,
        }}
        aria-hidden
      />

      {/* Section label */}
      <div
        style={{
          paddingLeft: 22,
          paddingRight: 12,
          paddingTop: 20,
          paddingBottom: 10,
          borderBottom: '1px solid rgba(255,153,0,0.10)',
        }}
      >
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 8,
          letterSpacing: '0.32em',
          textTransform: 'uppercase',
          color: 'rgba(255,153,0,0.40)',
        }}>
          PIPELINE SYSTEMS
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto" aria-label="Main navigation" style={{ paddingTop: 4, paddingBottom: 4 }}>
        {NAV.map(({ to, label, icon: Icon, lcarsColor }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: 12,
              paddingLeft: 22,
              paddingRight: 14,
              paddingTop: 11,
              paddingBottom: 11,
              position: 'relative',
              color: isActive ? '#FFFFFF' : 'rgba(180,190,220,0.55)',
              background: isActive ? `${lcarsColor}16` : 'transparent',
              borderBottom: `1px solid ${isActive ? 'rgba(255,153,0,0.08)' : 'rgba(255,153,0,0.04)'}`,
              textDecoration: 'none',
              transition: 'background 120ms ease, color 120ms ease',
            })}
            onMouseEnter={e => {
              const el = e.currentTarget as HTMLElement
              if (!el.getAttribute('aria-current')) {
                el.style.background = 'rgba(255,153,0,0.06)'
                el.style.color = 'rgba(220,230,255,0.80)'
              }
            }}
            onMouseLeave={e => {
              const el = e.currentTarget as HTMLElement
              if (!el.getAttribute('aria-current')) {
                el.style.background = 'transparent'
                el.style.color = 'rgba(180,190,220,0.55)'
              }
            }}
          >
            {({ isActive }) => (
              <>
                {/* Colored accent block — replaces thin spine */}
                <span
                  className="absolute left-[10px] inset-y-1"
                  style={{
                    width: 5,
                    background: lcarsColor,
                    opacity: isActive ? 1.0 : 0.35,
                    boxShadow: isActive ? `0 0 6px ${lcarsColor}` : 'none',
                    transition: 'opacity 150ms ease, box-shadow 150ms ease',
                  }}
                  aria-hidden
                />

                <Icon
                  style={{
                    width: 14, height: 14, flexShrink: 0,
                    color: isActive ? lcarsColor : 'rgba(160,170,200,0.45)',
                    filter: isActive ? `drop-shadow(0 0 3px ${lcarsColor}60)` : 'none',
                    transition: 'color 120ms ease',
                  }}
                  aria-hidden
                />
                <span style={{
                  fontFamily: "'Antonio', 'Inter', sans-serif",
                  fontSize: 12,
                  fontWeight: isActive ? 600 : 400,
                  letterSpacing: '0.10em',
                  textTransform: 'uppercase',
                  lineHeight: 1,
                }}>
                  {label}
                </span>

                {/* Active: right glow accent */}
                {isActive && (
                  <span
                    className="absolute right-0 inset-y-1"
                    style={{
                      width: 2,
                      background: lcarsColor,
                      opacity: 0.50,
                      boxShadow: `0 0 4px ${lcarsColor}`,
                    }}
                    aria-hidden
                  />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer status strip */}
      <div
        style={{
          borderTop: '1px solid rgba(255,153,0,0.14)',
          padding: '14px 22px 14px 22px',
          gap: 8,
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <LiveIndicator status="live" label="Systems Nominal" />
        <p style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 8,
          letterSpacing: '0.18em',
          color: 'rgba(255,153,0,0.28)',
          textTransform: 'uppercase',
        }}>
          SITE: LOCAL — RBAC PENDING
        </p>
      </div>
    </aside>
  )
}

// ── Sidebar logo helper ───────────────────────────────────────────────────────

function SidebarLogo() {
  const [err, setErr] = useState(false)

  const frameStyle: React.CSSProperties = {
    border: '1px solid rgba(120,85,40,0.30)',
    borderRadius: 6,
    padding: '6px 12px',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    background: 'var(--accent-faint)',
  }

  if (err) {
    return (
      <div style={frameStyle}>
        <span style={{
          fontFamily: "'Antonio', 'Inter', sans-serif",
          fontSize: 30, fontWeight: 900, letterSpacing: '0.12em',
          color: 'var(--accent)', textTransform: 'uppercase', lineHeight: 1,
        }}>DPARS</span>
      </div>
    )
  }
  return (
    <div style={frameStyle}>
      <img
        src="/dpars-logo.png"
        alt="DPARS"
        onError={() => setErr(true)}
        style={{ height: 60, width: 'auto', maxWidth: 180, objectFit: 'contain' }}
      />
    </div>
  )
}

// ── Sidebar (mode-aware) ──────────────────────────────────────────────────────

export function Sidebar() {
  const { isLCARS } = useTheme()

  if (isLCARS) return <LCARSSidebar />

  return (
    <aside
      className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col"
      style={{
        background:           'var(--sidebar-bg)',
        borderRight:          '1px solid var(--sidebar-border)',
        boxShadow:            'var(--sidebar-shadow)',
        backdropFilter:       'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        transition:           'background 200ms ease, border-color 200ms ease',
      }}
    >
      {/* Brand */}
      <div
        className="flex flex-col items-center justify-center px-5 py-5"
        style={{ borderBottom: '1px solid var(--sidebar-border)', minHeight: 140 }}
      >
        <SidebarLogo />
        <div className="mt-3 text-center">
          <span
            className="block uppercase"
            style={{
              fontFamily: "'Antonio', 'Inter', sans-serif",
              fontSize: 20, fontWeight: 900, letterSpacing: '0.16em',
              color: 'var(--text-primary)', lineHeight: 1,
            }}
          >
            DPARS
          </span>
          <span
            className="block mt-1.5 uppercase"
            style={{
              fontSize: 8, letterSpacing: '0.12em',
              color: 'var(--text-faint)', lineHeight: 1.4,
            }}
          >
            Digital Pathology Analysis &amp; Retrieval System
          </span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-5 space-y-0.5" aria-label="Main navigation">
        <p className="section-label px-2 mb-3">Pipeline</p>

        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className="group flex items-center gap-3 py-2.5 px-3 text-sm relative rounded-lg"
            style={({ isActive }) => ({
              color:      isActive ? 'var(--sidebar-text-active)' : 'var(--sidebar-text)',
              background: isActive ? 'var(--sidebar-active-bg)'   : 'transparent',
              transition: 'color 150ms ease, background-color 150ms ease',
            })}
            onMouseEnter={e => {
              const el = e.currentTarget as HTMLElement
              if (!el.getAttribute('aria-current')) el.style.background = 'var(--sidebar-hover-bg)'
            }}
            onMouseLeave={e => {
              const el = e.currentTarget as HTMLElement
              if (!el.getAttribute('aria-current')) el.style.background = ''
            }}
          >
            {({ isActive }) => (
              <>
                {isActive && (
                  <span
                    className="absolute left-0 top-2.5 bottom-2.5 w-0.5 rounded-full"
                    style={{ background: 'var(--sidebar-accent-bar)' }}
                    aria-hidden
                  />
                )}
                <Icon
                  className="h-3.5 w-3.5 flex-shrink-0"
                  style={{
                    color:      isActive ? 'var(--accent)' : 'var(--text-faint)',
                    transition: 'color 150ms ease',
                  }}
                  aria-hidden
                />
                <span className="font-medium text-xs">{label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div
        className="px-5 py-4 space-y-2.5"
        style={{ borderTop: '1px solid var(--sidebar-border)' }}
      >
        <LiveIndicator status="live" label="Systems Nominal" />
        <p
          className="text-[9px]"
          style={{ letterSpacing: '0.12em', color: 'var(--text-faint)' }}
        >
          Site: Local — RBAC pending
        </p>
      </div>
    </aside>
  )
}
