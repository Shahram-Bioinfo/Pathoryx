import { TelemetryMetricRow } from '../components/ui/TelemetryMetricRow'

/*
 * servicePhaseSecs — deterministic pulse-delay derived from service name.
 *
 * Same DJB2-lite hash used in LiveIndicator. Maps each service name to a
 * unique phase in [0, 2.59) s so running-state dots across service cards
 * never pulse in sync. The system reads as N independent live subsystems.
 */
function servicePhaseSecs(name: string): number {
  let h = 5381
  for (let i = 0; i < name.length; i++) {
    h = ((h << 5) + h + name.charCodeAt(i)) >>> 0
  }
  return (h % 260) / 100  // [0.00, 2.59)
}
import { QueueTelemetryStrip } from '../components/charts/QueueTelemetryStrip'
import { ServiceQueueSignal } from '../components/charts/ServiceQueueSignal'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { useQueues } from '../hooks/useQueues'
import { fmtNumber, fmtServiceName } from '../utils/formatters'
import { getChartColors } from '../utils/colors'
import { useTheme } from '../components/layout/ThemeProvider'

export function QueueMonitor() {
  const { data, isPending, isError, refetch } = useQueues()
  useTheme()
  const C = getChartColors()

  const totalRunning   = data?.queues.reduce((s, q) => s + q.running, 0)   ?? 0
  const totalCompleted = data?.queues.reduce((s, q) => s + q.completed, 0) ?? 0

  return (
    <>
      <PageHeader
        tag="Operations"
        title="Queue Monitor"
        subtitle="Live trigger queue depths across all pipeline services"
      />

      {isError && <div className="mb-5"><ErrorBanner message="Failed to load queue data." onRetry={refetch} /></div>}

      <TelemetryMetricRow
        className="mb-6"
        metrics={[
          {
            key:     'pending',
            label:   'Pending',
            value:   fmtNumber(data?.total_pending),
            accent:  (data?.total_pending ?? 0) > 0 ? 'var(--chart-amber)' : undefined,
            loading: isPending,
          },
          {
            key:     'running',
            label:   'Running',
            value:   fmtNumber(totalRunning),
            accent:  totalRunning > 0 ? 'var(--accent)' : undefined,
            loading: isPending,
          },
          {
            key:     'failed',
            label:   'Faulted',
            value:   fmtNumber(data?.total_failed),
            accent:  (data?.total_failed ?? 0) > 0 ? 'var(--chart-rose)' : undefined,
            sub:     (data?.total_failed ?? 0) > 0 ? 'action required' : 'nominal',
            loading: isPending,
          },
          {
            key:     'completed',
            label:   'Completed',
            value:   fmtNumber(totalCompleted),
            loading: isPending,
          },
        ]}
      />

      {data && data.queues.length > 0 && (
        <div className="glass rounded-xl p-5 mb-5" style={{ border: '1px solid var(--border-default)' }}>
          <p className="section-label">Queue Telemetry</p>
          <p className="panel-anno">Trigger throughput · signal rail by service</p>
          <QueueTelemetryStrip queues={data.queues} />
        </div>
      )}

      {/* Per-service subsystem diagnostics */}
      {isPending ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="glass rounded-xl h-36 ops-skeleton" />
          ))}
        </div>
      ) : data && data.queues.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-5">
          {data.queues.map(q => {
            const total   = q.pending + q.running + q.failed + q.completed
            const healthy = total > 0 ? ((q.completed + q.running) / total) * 100 : 100
            const failRate = total > 0 ? q.failed / total : 0

            const opState = failRate > 0.1 ? 'FAULT'
                          : q.failed > 0   ? 'DEGRADED'
                          : q.running > 0  ? 'ACTIVE'
                          : q.pending > 0  ? 'QUEUED'
                          : 'NOMINAL'

            const opColor = opState === 'FAULT'    ? C.rose
                          : opState === 'DEGRADED' ? C.amber
                          : opState === 'ACTIVE'   ? C.cyan
                          : opState === 'QUEUED'   ? C.violet
                          : C.emerald

            return (
              <div
                key={q.target_service}
                className="glass glass-hover rounded-xl p-4"
                style={{
                  border: q.failed > 0
                    ? '1px solid rgba(225,29,72,0.18)'
                    : '1px solid var(--border-default)',
                }}
              >
                {/* Subsystem header */}
                <div className="flex items-center justify-between mb-2.5">
                  <div className="flex items-center gap-2 min-w-0">
                    <span
                      className="h-1.5 w-1.5 rounded-full flex-shrink-0"
                      style={{
                        background: opColor,
                        /*
                         * Per-service pulse: same pulseStatus keyframe as LiveIndicator
                         * but with a hash-derived delay unique to each service name.
                         * Period varies slightly (2.7–3.0 s) to further desynchronise.
                         * Idle services (running === 0) have no animation.
                         */
                        ...(q.running > 0 ? {
                          animation:      `pulseStatus ${2.7 + (servicePhaseSecs(q.target_service) % 30) / 100}s ease-in-out infinite`,
                          animationDelay: `${servicePhaseSecs(q.target_service)}s`,
                          willChange:     'opacity, transform',
                        } : undefined),
                      }}
                    />
                    <span className="text-xs font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                      {fmtServiceName(q.target_service)}
                    </span>
                  </div>
                  <span
                    className="text-[9px] font-mono tracking-widest font-semibold shrink-0 ml-2"
                    style={{ color: opColor }}
                  >
                    {opState}
                  </span>
                </div>

                {/* Signal rail */}
                <ServiceQueueSignal {...q} />

                {/* Ops health bar */}
                {total > 0 && (
                  <div
                    className="mt-2 rounded-full overflow-hidden"
                    style={{ height: '2px', background: 'var(--border-faint)' }}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${healthy}%`, background: opColor, opacity: 0.5 }}
                    />
                  </div>
                )}

                {/*
                 * Compact single-line readout — replaces equal-weight 4-cell grid.
                 * FAIL is only colorized when non-zero to draw operational attention.
                 * Separator dots reduce visual weight vs. the previous 4-box layout.
                 */}
                <div className="mt-3 flex items-center gap-0 text-[10px] font-mono tabular">
                  {[
                    { l: 'PEND', v: q.pending,   color: q.pending   > 0 ? C.amber   : 'var(--text-faint)' },
                    { l: 'RUN',  v: q.running,   color: q.running   > 0 ? C.cyan    : 'var(--text-faint)' },
                    { l: 'FAIL', v: q.failed,    color: q.failed    > 0 ? C.rose    : 'var(--text-faint)' },
                    { l: 'DONE', v: q.completed, color: 'var(--text-faint)' },
                  ].map(({ l, v, color }, i) => (
                    <span key={l} className="flex items-center">
                      {i > 0 && (
                        <span className="mx-1.5" style={{ color: 'var(--border-default)' }}>·</span>
                      )}
                      <span style={{ color: 'var(--text-faint)', fontSize: '8px', letterSpacing: '0.1em' }}>
                        {l}&nbsp;
                      </span>
                      <span style={{ color }}>{v.toLocaleString()}</span>
                    </span>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        !isError && <EmptyState title="No queue data" description="No service triggers found." icon="⊘" />
      )}

      {data && data.queues.length > 0 && (
        <div
          className="glass rounded-xl overflow-hidden"
          style={{ border: '1px solid var(--border-default)' }}
        >
          <div className="px-5 py-2.5" style={{ borderBottom: '1px solid var(--border-faint)' }}>
            <p className="section-label mb-0">Trigger Summary</p>
          </div>
          <table className="ops-table">
            <thead>
              <tr>
                {['Service','Pending','Running','Failed','Completed','Total'].map(h => <th key={h}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {isPending
                ? Array.from({ length: 4 }, (_, i) => <SkeletonRow key={i} cols={6} />)
                : data.queues.map(q => {
                  const total = q.pending + q.running + q.failed + q.completed
                  return (
                    <tr key={q.target_service}>
                      <td className="font-medium text-xs" style={{ color: 'var(--text-secondary)' }}>
                        {fmtServiceName(q.target_service)}
                      </td>
                      <td><span className="font-mono text-xs tabular" style={{ color: C.amber   }}>{q.pending}</span></td>
                      <td><span className="font-mono text-xs tabular" style={{ color: C.cyan    }}>{q.running}</span></td>
                      <td>
                        <span
                          className="font-mono text-xs tabular font-semibold"
                          style={{ color: q.failed > 0 ? C.rose : 'var(--text-faint)' }}
                        >
                          {q.failed}
                        </span>
                      </td>
                      <td><span className="font-mono text-xs tabular" style={{ color: C.emerald }}>{q.completed}</span></td>
                      <td><span className="font-mono text-xs tabular" style={{ color: 'var(--text-muted)' }}>{total}</span></td>
                    </tr>
                  )
                })
              }
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}
