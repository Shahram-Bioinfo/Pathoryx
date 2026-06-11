import clsx from 'clsx'
import { CheckCircle2, FlaskConical, Microscope, Send, ShieldCheck, XCircle } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/*
 * All colours reference CSS variables from index.css (:root / .dark).
 * No hardcoded rgba — works in both light and dark themes automatically.
 */

interface Stage {
  key:      string
  label:    string
  icon:     LucideIcon
  colorVar: string   /* CSS var for the stage's primary colour */
  bgVar:    string   /* CSS var for the node background */
  borderVar:string   /* CSS var for the node border */
  statuses: string[]
  done:     string
  failed:   string
}

const STAGES: Stage[] = [
  {
    key:       'intake',
    label:     'Acquisition',
    icon:      FlaskConical,
    colorVar:  'var(--stage-intake-color)',
    bgVar:     'var(--stage-intake-bg)',
    borderVar: 'var(--stage-intake-border)',
    statuses:  ['detected', 'intake_running', 'intake_registered'],
    done:      'intake_registered',
    failed:    '',
  },
  {
    key:       'qc',
    label:     'Analysis',
    icon:      ShieldCheck,
    colorVar:  'var(--stage-qc-color)',
    bgVar:     'var(--stage-qc-bg)',
    borderVar: 'var(--stage-qc-border)',
    statuses:  ['qc_pending', 'qc_running', 'qc_passed', 'qc_failed'],
    done:      'qc_passed',
    failed:    'qc_failed',
  },
  {
    key:       'dicom',
    label:     'Processing',
    icon:      Microscope,
    colorVar:  'var(--stage-dicom-color)',
    bgVar:     'var(--stage-dicom-bg)',
    borderVar: 'var(--stage-dicom-border)',
    statuses:  ['dicom_pending', 'dicom_running', 'dicom_done', 'dicom_failed'],
    done:      'dicom_done',
    failed:    'dicom_failed',
  },
  {
    key:       'upload',
    label:     'Transmission',
    icon:      Send,
    colorVar:  'var(--stage-upload-color)',
    bgVar:     'var(--stage-upload-bg)',
    borderVar: 'var(--stage-upload-border)',
    statuses:  ['upload_pending', 'upload_running', 'uploaded', 'upload_failed'],
    done:      'uploaded',
    failed:    'upload_failed',
  },
]

// ─── Pipeline overview (counts per stage) ───────────────────────────────────

interface FlowProps {
  byStatus: Record<string, number>
  total:    number
}

export function PipelineFlow({ byStatus, total }: FlowProps) {
  return (
    <div className="relative flex items-start gap-0">
      {STAGES.map((stage, idx) => {
        const count   = stage.statuses.reduce((a, s) => a + (byStatus[s] ?? 0), 0)
        const hasData = count > 0
        const pct     = total > 0 ? ((count / total) * 100).toFixed(1) : '0.0'
        const Icon    = stage.icon

        return (
          <div key={stage.key} className="flex items-center flex-1 min-w-0">
            <div className="flex-1 flex flex-col items-center min-w-0">
              {/* Stage icon node */}
              <div
                className="flex h-10 w-10 items-center justify-center rounded-full border"
                style={{
                  borderColor: hasData ? stage.borderVar : 'var(--border-faint)',
                  background:  hasData ? stage.bgVar     : 'var(--surface-inset)',
                }}
              >
                <Icon
                  className="h-4 w-4"
                  style={{
                    color:   hasData ? stage.colorVar : 'var(--text-faint)',
                    opacity: hasData ? 1 : 0.4,
                  }}
                />
              </div>

              {/* Label + count */}
              <div className="mt-3 text-center">
                <p
                  className="text-[10px] font-semibold uppercase tracking-[0.14em] leading-none"
                  style={{ color: hasData ? stage.colorVar : 'var(--text-faint)' }}
                >
                  {stage.label}
                </p>
                <p
                  className="mt-2 text-[22px] font-semibold leading-none tracking-tight tabular"
                  style={{
                    fontFamily: '"JetBrains Mono", monospace',
                    color:      hasData ? 'var(--text-primary)' : 'var(--border-default)',
                  }}
                >
                  {count.toLocaleString()}
                </p>
                <p className="mt-1 text-[10px]" style={{ color: 'var(--text-faint)' }}>
                  {pct}%
                </p>
              </div>
            </div>

            {/* Connector between stages */}
            {idx < STAGES.length - 1 && (
              <div className="flex-shrink-0 mx-3 relative" style={{ width: 32, height: 1 }}>
                {/* Connector line */}
                <div
                  style={{
                    position: 'absolute',
                    inset: 0,
                    background: hasData
                      ? `linear-gradient(90deg, ${stage.colorVar}, ${STAGES[idx + 1].colorVar})`
                      : 'var(--border-faint)',
                    opacity: hasData ? 0.35 : 1,
                  }}
                />
                {/*
                 * Telemetry particle — non-uniform delays so the three connector
                 * particles travel at independent rhythms rather than a mechanical
                 * cascade. Gaps: 0.85 s, 1.45 s (vs. uniform 1.25 s / 1.25 s).
                 * The slight irregularity is subconsciously operational.
                 */}
                {hasData && (
                  <div
                    style={{
                      position: 'absolute',
                      top: '-1px',
                      left: 0,
                      width: 3,
                      height: 3,
                      borderRadius: '50%',
                      background: stage.colorVar,
                      opacity: 0,
                      animation: `telemetryDot 3.8s ease-in-out infinite`,
                      animationDelay: `${[0, 0.85, 2.30][idx] ?? idx * 1.1}s`,
                      willChange: 'transform, opacity',
                      pointerEvents: 'none',
                    }}
                  />
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// ─── Slide detail timeline ─────────────────────────────────────────────────

type StageState = 'done' | 'active' | 'failed' | 'pending'

function getState(stage: Stage, status: string | null): StageState {
  if (!status) return 'pending'
  if (status === stage.done) return 'done'
  if (stage.failed && status === stage.failed) return 'failed'
  if (stage.statuses.some(s => status === s)) return 'active'

  const order    = ['intake', 'qc', 'dicom', 'upload']
  const stageIdx = order.indexOf(stage.key)
  const curIdx   = order.findIndex(k => {
    if (k === 'upload' && status === 'uploaded') return true
    if (k === 'intake' && status === 'intake_registered') return true
    return status.startsWith(k + '_')
  })
  return curIdx > stageIdx ? 'done' : 'pending'
}

export function SlidePipelineTimeline({ status }: { status: string | null }) {
  return (
    <div className="flex items-center flex-wrap gap-y-3">
      {STAGES.map((stage, idx) => {
        const state = getState(stage, status)
        const Icon  = stage.icon

        const nodeStyle = {
          done: {
            borderColor: 'var(--chart-emerald)',
            background:  'rgba(5,150,105,0.08)',   /* emerald-600 at 8% */
          },
          active: {
            borderColor: stage.borderVar,
            background:  stage.bgVar,
          },
          failed: {
            borderColor: 'var(--chart-rose)',
            background:  'rgba(225,29,72,0.08)',
          },
          pending: {
            borderColor: 'var(--border-faint)',
            background:  'var(--surface-inset)',
          },
        }[state]

        return (
          <div key={stage.key} className="flex items-center">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-full border flex-shrink-0"
              style={nodeStyle}
            >
              {state === 'done'    && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />}
              {state === 'failed'  && <XCircle      className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />}
              {(state === 'active' || state === 'pending') && (
                <Icon
                  className={clsx('h-3.5 w-3.5', state === 'pending' && 'opacity-30')}
                  style={{ color: state === 'active' ? stage.colorVar : 'var(--text-faint)' }}
                />
              )}
            </div>

            <p
              className="ml-2 text-[10px] font-semibold uppercase tracking-[0.14em] leading-none"
              style={{
                color:
                  state === 'done'    ? 'var(--chart-emerald)' :
                  state === 'active'  ? stage.colorVar          :
                  state === 'failed'  ? 'var(--chart-rose)'     :
                  'var(--text-faint)',
              }}
            >
              {stage.label}
            </p>

            {idx < STAGES.length - 1 && (
              <div
                className="mx-4 flex-shrink-0"
                style={{
                  width: 28, height: 1,
                  background: state === 'done'
                    ? `linear-gradient(90deg, var(--chart-emerald), ${STAGES[idx + 1].colorVar})`
                    : 'var(--border-faint)',
                  opacity: state === 'done' ? 0.4 : 1,
                }}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
