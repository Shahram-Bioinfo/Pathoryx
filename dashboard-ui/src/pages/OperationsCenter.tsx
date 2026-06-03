/**
 * OperationsCenter — Phase 10 Observability & Safety
 *
 * Operational incident surface, service health with heartbeat ages,
 * stuck trigger detection, environment/upload safety indicators,
 * and DB health metrics.
 *
 * This page is investigation-oriented, not business-analytics-oriented.
 * Every section answers a specific operational question.
 */
import { Link } from 'react-router-dom'
import {
  Activity, AlertTriangle, CheckCircle2, Clock, Database,
  Server, ShieldAlert, ShieldCheck, Wifi, WifiOff,
} from 'lucide-react'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { TelemetryMetricRow } from '../components/ui/TelemetryMetricRow'
import {
  useDbHealth,
  useEnvironmentConfig,
  useOperationalIncidents,
  useOperationsHealth,
  useStuckTriggers,
} from '../hooks/useOperations'
import type { OperationalIncident, ServiceHealthExtended, StuckTriggerItem } from '../types/api'
import { fmtDuration, fmtDatetime, fmtServiceName, fmtStageName } from '../utils/formatters'

// ---------------------------------------------------------------------------
// Severity colours
// ---------------------------------------------------------------------------

const SEV_COLOR: Record<string, string> = {
  critical: 'var(--chart-rose)',
  warning:  'var(--chart-amber)',
  info:     'var(--accent)',
}

const SEV_BG: Record<string, string> = {
  critical: 'rgba(225,29,72,0.06)',
  warning:  'rgba(217,119,6,0.06)',
  info:     'rgba(59,130,246,0.06)',
}

const SEV_BORDER: Record<string, string> = {
  critical: 'rgba(225,29,72,0.20)',
  warning:  'rgba(217,119,6,0.20)',
  info:     'rgba(59,130,246,0.18)',
}

// ---------------------------------------------------------------------------
// Environment banner
// ---------------------------------------------------------------------------

function EnvironmentBanner() {
  const { data: env } = useEnvironmentConfig()
  if (!env) return null

  const isProd = env.environment === 'production' || env.environment === 'prod'
  const envColor = isProd ? 'var(--chart-rose)' : 'var(--chart-amber)'
  const envBg    = isProd ? 'rgba(225,29,72,0.07)' : 'rgba(217,119,6,0.07)'
  const envBorder = isProd ? 'rgba(225,29,72,0.22)' : 'rgba(217,119,6,0.22)'

  return (
    <div
      className="rounded-xl px-5 py-3 mb-5 flex items-center gap-4 flex-wrap"
      style={{ background: envBg, border: `1px solid ${envBorder}` }}
    >
      {/* Environment */}
      <div className="flex items-center gap-2">
        <Server className="h-3 w-3 flex-shrink-0" style={{ color: envColor }} aria-hidden />
        <span
          className="text-[10px] font-semibold uppercase tracking-[0.18em]"
          style={{ color: envColor }}
        >
          {env.environment.toUpperCase()}
        </span>
      </div>

      {/* Separator */}
      <div className="h-3 w-px" style={{ background: 'var(--border-default)' }} aria-hidden />

      {/* Upload mode */}
      {env.upload_dry_run ? (
        <div className="flex items-center gap-1.5">
          <ShieldCheck className="h-3 w-3" style={{ color: 'var(--chart-teal)' }} aria-hidden />
          <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: 'var(--chart-teal)' }}>
            Upload Dry-Run Active
          </span>
          <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
            — no slides delivered to PACS
          </span>
        </div>
      ) : (
        <div className="flex items-center gap-1.5">
          <ShieldAlert className="h-3 w-3 flex-shrink-0" style={{ color: 'var(--chart-rose)' }} aria-hidden />
          <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: 'var(--chart-rose)' }}>
            Live PACS Delivery Enabled
          </span>
          {env.upload_peer_ip && (
            <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
              → {env.upload_peer_ip}{env.upload_peer_port ? `:${env.upload_peer_port}` : ''}
            </span>
          )}
        </div>
      )}

      {/* Integrations */}
      <div className="flex items-center gap-3 ml-auto">
        {[
          { label: 'C-STORE', on: env.c_store_enabled },
          { label: 'LIS',     on: env.lis_enabled },
          { label: 'PASNET',  on: env.pasnet_enabled },
        ].map(({ label, on }) => (
          <span
            key={label}
            className="text-[9px] font-mono px-1.5 py-0.5 rounded"
            style={{
              color:      on ? 'var(--chart-teal)' : 'var(--text-faint)',
              background: 'var(--accent-faint)',
              border:     '1px solid var(--border-faint)',
            }}
          >
            {label} {on ? 'ON' : 'OFF'}
          </span>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Incident row
// ---------------------------------------------------------------------------

function IncidentRow({ incident }: { incident: OperationalIncident }) {
  const color  = SEV_COLOR[incident.severity] ?? 'var(--text-muted)'
  const bg     = SEV_BG[incident.severity]    ?? 'transparent'
  const border = SEV_BORDER[incident.severity] ?? 'var(--border-faint)'
  const Icon   = incident.severity === 'critical' ? AlertTriangle
                 : incident.severity === 'warning' ? Clock
                 : CheckCircle2

  return (
    <div
      className="flex items-start gap-3 rounded-lg px-3 py-2.5 mb-2"
      style={{ background: bg, border: `1px solid ${border}` }}
    >
      <Icon className="h-3 w-3 flex-shrink-0 mt-0.5" style={{ color }} aria-hidden />
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-semibold" style={{ color }}>
          {incident.title}
        </p>
        <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
          {incident.detail}
        </p>
        {incident.related_ids.length > 0 && (
          <p className="text-[9px] font-mono mt-1" style={{ color: 'var(--text-faint)' }}>
            Trigger IDs: {incident.related_ids.slice(0, 5).join(', ')}
            {incident.related_ids.length > 5 ? ` +${incident.related_ids.length - 5} more` : ''}
          </p>
        )}
      </div>
      <span
        className="text-[9px] font-semibold uppercase tracking-wider flex-shrink-0"
        style={{ color }}
      >
        {incident.severity}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Service health row
// ---------------------------------------------------------------------------

const HEALTH_COLOR: Record<string, string> = {
  healthy:     'var(--chart-emerald)',
  degraded:    'var(--chart-amber)',
  stale:       'var(--chart-rose)',
  disconnected:'var(--chart-rose)',
}

const HEALTH_ICON_MAP: Record<string, typeof Wifi> = {
  healthy:     Wifi,
  degraded:    Wifi,
  stale:       WifiOff,
  disconnected:WifiOff,
}

function ServiceHealthRow({ svc }: { svc: ServiceHealthExtended }) {
  const color     = HEALTH_COLOR[svc.health_state] ?? 'var(--text-faint)'
  const HIcon     = HEALTH_ICON_MAP[svc.health_state] ?? WifiOff

  return (
    <div
      className="flex items-center gap-3 py-2.5"
      style={{ borderBottom: '1px solid var(--border-faint)' }}
    >
      {/* State dot */}
      <span
        className="h-1.5 w-1.5 rounded-full flex-shrink-0"
        style={{ background: color }}
        aria-hidden
      />

      {/* Service name */}
      <span className="flex-1 text-xs font-medium min-w-0" style={{ color: 'var(--text-secondary)' }}>
        {fmtServiceName(svc.service_name)}
      </span>

      {/* Heartbeat age */}
      <span
        className="text-[9px] font-mono tabular flex-shrink-0"
        style={{ color }}
        title={svc.last_heartbeat_at ? fmtDatetime(svc.last_heartbeat_at) : 'No heartbeat'}
      >
        {svc.heartbeat_age_seconds != null
          ? `hb ${fmtDuration(svc.heartbeat_age_seconds)} ago`
          : 'no heartbeat'}
      </span>

      {/* Uptime */}
      {svc.uptime_seconds != null && (
        <span className="text-[9px] font-mono hidden sm:block" style={{ color: 'var(--text-faint)' }}>
          up {fmtDuration(svc.uptime_seconds)}
        </span>
      )}

      {/* Queue brief */}
      <div className="flex items-center gap-1.5 text-[9px] font-mono hidden sm:flex">
        {svc.queue_pending > 0 && (
          <span style={{ color: 'var(--chart-amber)' }}>p{svc.queue_pending}</span>
        )}
        {svc.queue_running > 0 && (
          <span style={{ color: 'var(--accent)' }}>r{svc.queue_running}</span>
        )}
        {svc.queue_failed > 0 && (
          <span style={{ color: 'var(--chart-rose)' }}>f{svc.queue_failed}</span>
        )}
      </div>

      {/* Host */}
      <span className="text-[9px] font-mono hidden md:block flex-shrink-0" style={{ color: 'var(--text-faint)' }}>
        {svc.host_id}
      </span>

      {/* Health state chip */}
      <span
        className="text-[9px] font-semibold uppercase tracking-wider flex-shrink-0 flex items-center gap-1"
        style={{ color }}
      >
        <HIcon className="h-2.5 w-2.5" aria-hidden />
        {svc.health_state}
      </span>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Stuck trigger row
// ---------------------------------------------------------------------------

function StuckTriggerRow({ item }: { item: StuckTriggerItem }) {
  const color = item.severity === 'critical' ? 'var(--chart-rose)' : 'var(--chart-amber)'
  const kindLabel = item.kind === 'pending_stuck' ? 'STUCK PENDING'
                  : item.kind === 'running_stuck' ? 'STUCK RUNNING'
                  : 'EXHAUSTED'

  return (
    <div
      className="flex items-start gap-3 py-2.5"
      style={{ borderBottom: '1px solid var(--border-faint)' }}
    >
      {/* Severity dot */}
      <span className="h-1.5 w-1.5 rounded-full flex-shrink-0 mt-1" style={{ background: color }} aria-hidden />

      {/* Stage + service */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[10px] font-semibold" style={{ color }}>
            {kindLabel}
          </span>
          <span className="text-[10px]" style={{ color: 'var(--text-secondary)' }}>
            {fmtStageName(item.stage)} → {fmtServiceName(item.target_service)}
          </span>
        </div>
        <p className="text-[9px] mt-0.5" style={{ color: 'var(--text-faint)' }}>
          {item.likely_cause}
        </p>
      </div>

      {/* Stuck duration */}
      {item.stuck_seconds != null && (
        <span className="text-[9px] font-mono flex-shrink-0" style={{ color }}>
          {fmtDuration(item.stuck_seconds)}
        </span>
      )}

      {/* Retry count */}
      {item.retry_count > 0 && (
        <span className="text-[9px] font-mono flex-shrink-0" style={{ color: 'var(--chart-amber)' }}>
          ×{item.retry_count}
        </span>
      )}

      {/* Investigate link */}
      {item.global_artifact_id && (
        <Link
          to={`/slides/${encodeURIComponent(item.global_artifact_id)}`}
          className="text-[9px] flex-shrink-0"
          style={{ color: 'var(--accent)' }}
        >
          →
        </Link>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// DB Health section
// ---------------------------------------------------------------------------

function DbHealthSection() {
  const { data, isPending } = useDbHealth()
  if (isPending) return (
    <div className="animate-pulse text-[10px]" style={{ color: 'var(--text-faint)' }}>Loading DB metrics…</div>
  )
  if (!data) return null

  const tables = Object.entries(data.table_sizes).sort((a, b) => b[1] - a[1])

  return (
    <div className="space-y-2">
      {/* Table sizes */}
      {tables.length > 0 && (
        <div className="overflow-x-auto">
          <table className="ops-table">
            <thead>
              <tr>
                <th>Table</th>
                <th>Approx rows</th>
              </tr>
            </thead>
            <tbody>
              {tables.map(([tbl, count]) => (
                <tr key={tbl}>
                  <td>
                    <span className="text-[10px] font-mono" style={{ color: 'var(--text-muted)' }}>{tbl}</span>
                  </td>
                  <td>
                    <span className="text-[10px] font-mono tabular" style={{ color: 'var(--text-secondary)' }}>
                      {count.toLocaleString()}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Key metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {[
          {
            label: 'Failed triggers',
            value: data.failed_triggers,
            accent: data.failed_triggers > 10 ? 'var(--chart-rose)' : undefined,
          },
          {
            label: 'Pending triggers',
            value: data.pending_triggers,
            accent: data.pending_triggers > 50 ? 'var(--chart-amber)' : undefined,
          },
          {
            label: 'Recovery backlog',
            value: data.recovery_backlog,
            accent: data.recovery_backlog > 20 ? 'var(--chart-amber)' : undefined,
          },
          {
            label: 'Oldest pending',
            value: data.oldest_pending_age_seconds != null
              ? fmtDuration(data.oldest_pending_age_seconds)
              : '—',
            accent: (data.oldest_pending_age_seconds ?? 0) > 900 ? 'var(--chart-amber)' : undefined,
          },
        ].map(({ label, value, accent }) => (
          <div
            key={label}
            className="rounded px-3 py-2"
            style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
          >
            <p className="text-[9px] uppercase tracking-wider mb-0.5" style={{ color: 'var(--text-faint)' }}>
              {label}
            </p>
            <p className="text-[11px] font-mono tabular" style={{ color: accent ?? 'var(--text-secondary)' }}>
              {typeof value === 'number' ? value.toLocaleString() : value}
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function OperationsCenter() {
  const { data: incidents, isPending: incPending, isError: incError, refetch: refetchInc } = useOperationalIncidents()
  const { data: health, isPending: healthPending } = useOperationsHealth()
  const { data: stuck, isPending: stuckPending } = useStuckTriggers()
  const { data: env } = useEnvironmentConfig()

  const criticalCount = incidents?.critical_count ?? 0
  const warningCount  = incidents?.warning_count  ?? 0
  const stuckTotal    = stuck?.total ?? 0
  const staleServices = health?.services.filter(
    s => s.health_state === 'stale' || s.health_state === 'disconnected'
  ).length ?? 0

  return (
    <>
      <PageHeader
        tag="Operations"
        title="Operations Center"
        subtitle="Service health · incident surface · queue intelligence · safety controls"
        actions={
          env ? (
            <span
              className="text-[9px] font-semibold uppercase tracking-[0.18em] px-2 py-0.5 rounded"
              style={{
                color:      env.environment === 'production' ? 'var(--chart-rose)' : 'var(--chart-amber)',
                background: env.environment === 'production' ? 'rgba(225,29,72,0.09)' : 'rgba(217,119,6,0.09)',
                border:     env.environment === 'production' ? '1px solid rgba(225,29,72,0.22)' : '1px solid rgba(217,119,6,0.22)',
              }}
            >
              {env.environment.toUpperCase()}
            </span>
          ) : undefined
        }
      />

      {incError && (
        <div className="mb-5">
          <ErrorBanner message="Failed to load operational data." onRetry={refetchInc} />
        </div>
      )}

      {/* Environment + upload safety banner */}
      <EnvironmentBanner />

      {/* Operational counters */}
      <TelemetryMetricRow
        className="mb-6"
        columns={6}
        metrics={[
          {
            key:    'critical',
            label:  'Critical',
            value:  String(criticalCount),
            accent: criticalCount > 0 ? 'var(--chart-rose)' : undefined,
            loading: incPending,
          },
          {
            key:    'warning',
            label:  'Warnings',
            value:  String(warningCount),
            accent: warningCount > 0 ? 'var(--chart-amber)' : undefined,
            loading: incPending,
          },
          {
            key:    'stuck',
            label:  'Stuck Triggers',
            value:  String(stuckTotal),
            accent: stuckTotal > 0 ? 'var(--chart-rose)' : undefined,
            loading: stuckPending,
          },
          {
            key:    'stale_svc',
            label:  'Stale Services',
            value:  String(staleServices),
            accent: staleServices > 0 ? 'var(--chart-rose)' : undefined,
            loading: healthPending,
          },
          {
            key:    'upload',
            label:  'Upload Mode',
            value:  env ? (env.upload_dry_run ? 'DRY RUN' : 'LIVE') : '—',
            accent: env?.upload_dry_run === false ? 'var(--chart-rose)' : 'var(--chart-teal)',
          },
        ]}
      />

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-5">

        {/* Left: Incidents + Stuck Triggers */}
        <div className="space-y-5">

          {/* Operational Incidents */}
          <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
            <div
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--border-faint)' }}
            >
              <Activity className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
              <p className="section-label mb-0">Operational Incidents</p>
              {criticalCount > 0 && (
                <span
                  className="ml-auto text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
                  style={{ color: 'var(--chart-rose)', background: 'rgba(225,29,72,0.09)' }}
                >
                  {criticalCount} critical
                </span>
              )}
            </div>
            <div className="p-4">
              {incPending ? (
                <div className="space-y-2">
                  {Array.from({ length: 3 }, (_, i) => (
                    <div key={i} className="ops-skeleton h-10 rounded-lg" />
                  ))}
                </div>
              ) : (incidents?.incidents.length ?? 0) === 0 ? (
                <div className="flex items-center gap-2 py-3">
                  <CheckCircle2 className="h-3.5 w-3.5" style={{ color: 'var(--chart-emerald)' }} aria-hidden />
                  <p className="text-[10px]" style={{ color: 'var(--chart-emerald)' }}>
                    All systems nominal — no active incidents
                  </p>
                </div>
              ) : (
                incidents?.incidents.map((inc, i) => (
                  <IncidentRow key={`${inc.category}-${i}`} incident={inc} />
                ))
              )}
            </div>
          </div>

          {/* Stuck Triggers */}
          <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
            <div
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--border-faint)' }}
            >
              <AlertTriangle className="h-3.5 w-3.5" style={{ color: stuckTotal > 0 ? 'var(--chart-rose)' : 'var(--text-muted)' }} aria-hidden />
              <p className="section-label mb-0">Stuck Triggers</p>
              {stuck && (
                <div className="ml-auto flex items-center gap-3 text-[9px] font-mono">
                  {stuck.exhausted > 0 && (
                    <span style={{ color: 'var(--chart-rose)' }}>{stuck.exhausted} exhausted</span>
                  )}
                  {stuck.running_stuck > 0 && (
                    <span style={{ color: 'var(--chart-rose)' }}>{stuck.running_stuck} running</span>
                  )}
                  {stuck.pending_stuck > 0 && (
                    <span style={{ color: 'var(--chart-amber)' }}>{stuck.pending_stuck} pending</span>
                  )}
                </div>
              )}
            </div>
            <div className="px-4 py-2">
              {stuckPending ? (
                Array.from({ length: 3 }, (_, i) => <SkeletonRow key={i} cols={4} />)
              ) : stuckTotal === 0 ? (
                <div className="flex items-center gap-2 py-3">
                  <CheckCircle2 className="h-3.5 w-3.5" style={{ color: 'var(--chart-emerald)' }} aria-hidden />
                  <p className="text-[10px]" style={{ color: 'var(--chart-emerald)' }}>
                    No stuck triggers detected
                  </p>
                </div>
              ) : (
                stuck?.items.map(item => (
                  <StuckTriggerRow key={item.trigger_id} item={item} />
                ))
              )}
            </div>
          </div>
        </div>

        {/* Right: Service Health + DB Health */}
        <div className="space-y-5">

          {/* Service Health */}
          <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
            <div
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--border-faint)' }}
            >
              <Server className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
              <p className="section-label mb-0">Service Health</p>
              <span className="ml-auto text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
                heartbeat threshold {health?.stale_threshold_seconds}s
              </span>
            </div>
            <div className="px-4 py-2">
              {healthPending ? (
                Array.from({ length: 4 }, (_, i) => <SkeletonRow key={i} cols={5} />)
              ) : (health?.services.length ?? 0) === 0 ? (
                <p className="text-[10px] py-3" style={{ color: 'var(--text-faint)' }}>
                  No runners registered — services not started
                </p>
              ) : (
                health?.services.map(svc => (
                  <ServiceHealthRow key={svc.runner_id} svc={svc} />
                ))
              )}
            </div>
          </div>

          {/* DB Health */}
          <div className="glass rounded-xl overflow-hidden" style={{ border: '1px solid var(--border-default)' }}>
            <div
              className="flex items-center gap-2 px-4 py-3"
              style={{ borderBottom: '1px solid var(--border-faint)' }}
            >
              <Database className="h-3.5 w-3.5" style={{ color: 'var(--text-muted)' }} aria-hidden />
              <p className="section-label mb-0">Database Health</p>
              <span className="ml-auto text-[9px]" style={{ color: 'var(--text-faint)' }}>
                approx row counts via pg_stat_user_tables
              </span>
            </div>
            <div className="p-4">
              <DbHealthSection />
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
