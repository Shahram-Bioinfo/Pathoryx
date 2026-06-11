import {
  Activity, AlertTriangle, CloudUpload, FlaskConical,
  Layers, LayoutDashboard, MonitorPlay, RefreshCcw, ShieldCheck,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import { LiveIndicator } from '../ui/LiveIndicator'

const NAV = [
  { to: '/',              label: 'Overview',         icon: LayoutDashboard },
  { to: '/slides',        label: 'Slide Explorer',   icon: Layers          },
  { to: '/queues',        label: 'Queue Monitor',    icon: Activity        },
  { to: '/failures',      label: 'Failure Center',   icon: AlertTriangle   },
  { to: '/recovery',      label: 'Recovery Center',  icon: RefreshCcw      },
  { to: '/operations',    label: 'Operations',       icon: ShieldCheck     },
  { to: '/uploads',       label: 'Upload Operations', icon: CloudUpload    },
  { to: '/computer-core', label: 'Computer Core',    icon: MonitorPlay     },
]

export function Sidebar() {
  return (
    <aside
      className="fixed inset-y-0 left-0 z-30 flex w-60 flex-col"
      style={{
        background:           'var(--sidebar-bg)',
        borderRight:          '1px solid var(--sidebar-border)',
        boxShadow:            'var(--sidebar-shadow)',
        backdropFilter:       'blur(10px)',
        WebkitBackdropFilter: 'blur(10px)',
        transition:           'background 200ms ease, border-color 200ms ease, box-shadow 200ms ease',
      }}
    >
      {/* ── Brand ─────────────────────────────────────────────── */}
      <div
        className="flex items-center gap-3 px-5 py-[18px]"
        style={{ borderBottom: '1px solid var(--sidebar-border)' }}
      >
        <div
          className="flex h-8 w-8 items-center justify-center rounded-lg flex-shrink-0"
          style={{
            background: 'var(--accent-faint)',
            border:     '1px solid var(--border-default)',
          }}
        >
          <FlaskConical className="h-3.5 w-3.5" style={{ color: 'var(--accent)' }} aria-hidden />
        </div>
        <div>
          <span
            className="block text-sm font-semibold"
            style={{ letterSpacing: '0.10em', color: 'var(--text-primary)' }}
          >
            PALANTIR
          </span>
          <span
            className="block mt-0.5 text-[9px]"
            style={{ letterSpacing: '0.15em', color: 'var(--text-faint)' }}
          >
            The Lord of Process
          </span>
        </div>
      </div>

      {/* ── Navigation ────────────────────────────────────────── */}
      <nav className="flex-1 overflow-y-auto px-3 py-5 space-y-0.5" aria-label="Main navigation">
        <p className="section-label px-2 mb-3">Pipeline</p>

        {NAV.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === '/'}
            className="group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm relative"
            style={({ isActive }) => ({
              color:      isActive ? 'var(--sidebar-text-active)' : 'var(--sidebar-text)',
              background: isActive ? 'var(--sidebar-active-bg)'   : 'transparent',
              transition: 'color 150ms ease, background-color 150ms ease',
            })}
            onMouseEnter={e => {
              if (!(e.currentTarget as HTMLElement).classList.contains('active')) {
                (e.currentTarget as HTMLElement).style.background = 'var(--sidebar-hover-bg)'
              }
            }}
            onMouseLeave={e => {
              if (!(e.currentTarget as HTMLElement).getAttribute('aria-current')) {
                (e.currentTarget as HTMLElement).style.background = ''
              }
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

      {/* ── Footer ────────────────────────────────────────────── */}
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
