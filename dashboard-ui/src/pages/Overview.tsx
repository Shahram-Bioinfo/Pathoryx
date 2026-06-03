import { SlideStateRail } from '../components/charts/SlideStateRail'
import { QueueTelemetryStrip } from '../components/charts/QueueTelemetryStrip'
import { EventStream } from '../components/ui/EventStream'
import { PipelineFlow } from '../components/ui/PipelineFlow'
import { ServiceTopology } from '../components/ui/ServiceTopology'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { LiveIndicator } from '../components/ui/LiveIndicator'
import { PageHeader } from '../components/ui/PageHeader'
import { StatusBadge } from '../components/ui/StatusBadge'
import { TelemetryMetricRow } from '../components/ui/TelemetryMetricRow'
import { useOverview } from '../hooks/useOverview'
import { useQueues } from '../hooks/useQueues'
import { useServicesHealth } from '../hooks/useServicesHealth'
import { fmtNumber, fmtPercent, fmtServiceName } from '../utils/formatters'

export function Overview() {
  const { data: ov, isPending, isError, refetch } = useOverview()
  const { data: queues } = useQueues()
  const { data: health } = useServicesHealth()

  const byStatus = ov?.slides.by_status ?? {}
  const total    = ov?.slides.total ?? 0
  const qcTotal  = (byStatus['qc_passed'] ?? 0) + (byStatus['qc_failed'] ?? 0)
  const qcRate   = qcTotal > 0 ? fmtPercent(byStatus['qc_passed'] ?? 0, qcTotal) : '—'

  return (
    <>
      <PageHeader
        tag="Mission Control"
        title="Operations Overview"
        subtitle="Live pipeline telemetry — 30s refresh cadence"
        actions={<LiveIndicator status="live" />}
      />

      {isError && (
        <div className="mb-6">
          <ErrorBanner
            message="Cannot reach API backend. Is pathoryx-dashboard running on port 8090?"
            onRetry={refetch}
          />
        </div>
      )}

      {/* Operations status — unified instrument panel replaces 8 isolated KPI cards */}
      <TelemetryMetricRow
        className="mb-6"
        metrics={[
          {
            key: 'slides',
            label: 'Artifacts',
            value: fmtNumber(total),
            sub: 'indexed',
            loading: isPending,
          },
          {
            key: 'qc',
            label: 'QC Yield',
            value: qcRate,
            sub: `${fmtNumber(qcTotal)} evaluated`,
            accent: qcTotal > 0 ? 'var(--chart-emerald)' : undefined,
            loading: isPending,
          },
          {
            key: 'queue',
            label: 'Queue Depth',
            value: fmtNumber(ov?.triggers.pending),
            sub: `${fmtNumber(ov?.triggers.running)} running`,
            accent: (ov?.triggers.pending ?? 0) > 0 ? 'var(--chart-amber)' : undefined,
            loading: isPending,
          },
          {
            key: 'runners',
            label: 'Runners',
            value: fmtNumber(ov?.runners.active),
            sub: (ov?.runners.stale ?? 0) > 0 ? `${ov?.runners.stale} stale` : 'all nominal',
            accent: (ov?.runners.stale ?? 0) > 0 ? 'var(--chart-amber)' : 'var(--chart-emerald)',
            loading: isPending,
          },
          {
            key: 'events',
            label: 'Events / 24h',
            value: fmtNumber(ov?.events_last_24h),
            loading: isPending,
          },
          {
            key: 'failures',
            label: 'Failures',
            value: fmtNumber(ov?.triggers.failed),
            accent: (ov?.triggers.failed ?? 0) > 0 ? 'var(--chart-rose)' : undefined,
            sub: (ov?.triggers.failed ?? 0) > 0 ? 'action required' : 'nominal',
            loading: isPending,
          },
          {
            key: 'completed',
            label: 'Completed',
            value: fmtNumber(ov?.triggers.completed),
            loading: isPending,
          },
          {
            key: 'uploaded',
            label: 'Uploaded',
            value: fmtNumber(byStatus['uploaded']),
            loading: isPending,
          },
        ]}
      />

      {/* Pipeline */}
      <div
        className="mission-card glass mb-5 p-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="section-label">Interstellar Pipeline</p>
            <p className="panel-anno">
              Scanner → Babel-Shark → QC → DICOM → Upload
            </p>
          </div>
          {total > 0 && (
            <span
              className="text-[10px] font-mono tabular"
              style={{ color: 'var(--text-muted)' }}
            >
              {total.toLocaleString()} objects tracked
            </span>
          )}
        </div>
        {total > 0 ? (
          <PipelineFlow byStatus={byStatus} total={total} />
        ) : (
          <div
            className="flex items-center justify-center h-24 text-[10px] tracking-widest uppercase"
            style={{ color: 'var(--text-faint)' }}
          >
            Awaiting first acquisition
          </div>
        )}
      </div>

      {/* Service Network Topology */}
      <div
        className="mission-card glass mb-5 p-5"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <div className="flex items-center justify-between mb-4">
          <div>
            <p className="section-label">Service Network</p>
            <p className="panel-anno">Live operational state · pipeline topology</p>
          </div>
          {health && (
            <span className="text-[10px] font-mono tabular" style={{ color: 'var(--text-muted)' }}>
              {health.runners.filter(r => r.status === 'active').length} active ·{' '}
              {health.runners.filter(r => r.status !== 'active').length} other
            </span>
          )}
        </div>
        <ServiceTopology
          runners={health?.runners ?? []}
          queues={queues?.queues}
        />
      </div>

      {/* Queue telemetry + slide distribution */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4 mb-4">
        <div className="mission-card glass xl:col-span-3 p-5">
          <p className="section-label">Queue Telemetry</p>
          <p className="panel-anno">Trigger throughput · signal rail by service</p>
          {queues && queues.queues.length > 0 ? (
            <QueueTelemetryStrip queues={queues.queues} />
          ) : (
            <div className="flex items-center justify-center h-[200px] text-[10px] tracking-widest uppercase" style={{ color: 'var(--text-faint)' }}>
              No queue data
            </div>
          )}
        </div>

        <div className="mission-card glass xl:col-span-2 p-5">
          <p className="section-label">Status Topology</p>
          {total > 0 ? (
            <SlideStateRail byStatus={byStatus} total={total} />
          ) : (
            <div className="flex items-center justify-center h-[200px] text-[10px]" style={{ color: 'var(--text-faint)' }}>
              No slides yet
            </div>
          )}
        </div>
      </div>

      {/* Service health + mission log */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-4">
        <div className="mission-card glass xl:col-span-2 p-5">
          <p className="section-label">Service Health</p>

          {health && health.runners.length > 0 ? (
            /*
             * Runner registry as a telemetry list — no per-runner card borders.
             * Border-separated rows read as operational status lines, not
             * individual analytics widgets.
             */
            <div>
              {health.runners.map((r, idx) => {
                let rh = 5381
                for (let i = 0; i < r.runner_id.length; i++)
                  rh = ((rh << 5) + rh + r.runner_id.charCodeAt(i)) >>> 0
                const runnerPhase  = (rh % 900) / 100
                const runnerPeriod = 9 + (rh % 400) / 100

                return (
                  <div
                    key={r.runner_id}
                    className="flex items-center gap-3 py-2"
                    style={{
                      borderBottom: idx < health.runners.length - 1
                        ? '1px solid var(--border-faint)'
                        : 'none',
                    }}
                  >
                    <span
                      className={`inline-flex rounded-full h-1.5 w-1.5 flex-shrink-0 ${
                        r.status === 'active'  ? 'bg-emerald-500 dark:bg-emerald-400' :
                        r.status === 'stale'   ? 'bg-amber-500 dark:bg-amber-400'    :
                        r.status === 'crashed' ? 'bg-rose-500 dark:bg-rose-400'      :
                                                 'bg-slate-400 dark:bg-slate-500'
                      }`}
                      style={r.status === 'active' ? {
                        animation:      `subsystemBreath ${runnerPeriod.toFixed(1)}s ease-in-out infinite`,
                        animationDelay: `${runnerPhase.toFixed(2)}s`,
                        willChange:     'opacity',
                      } : undefined}
                    />
                    <span className="flex-1 text-[11px] font-medium" style={{ color: 'var(--text-secondary)' }}>
                      {fmtServiceName(r.service_name)}
                    </span>
                    <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
                      {r.host_id}
                    </span>
                    <StatusBadge status={r.status} />
                  </div>
                )
              })}
            </div>
          ) : (
            <p className="text-[10px] tracking-wide" style={{ color: 'var(--text-faint)' }}>
              No runners registered
            </p>
          )}
        </div>

        <div className="mission-card glass xl:col-span-3 p-5">
          <p className="section-label">Mission Log</p>
          <EventStream />
        </div>
      </div>
    </>
  )
}
