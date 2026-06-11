import { useEffect, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Tooltip, Cell,
} from 'recharts'
import { useWallboard } from '../hooks/useWallboard'
import type { WallboardResponse } from '../types/api'

// ── Color palette ─────────────────────────────────────────────────────────────

const C = {
  bg:        '#010B1E',
  panel:     '#040E24',
  panel2:    '#071430',
  orange:    '#FF9900',
  teal:      '#33CC99',
  cyan:      '#33CCFF',
  purple:    '#CC99FF',
  red:       '#FF3366',
  amber:     '#FFCC00',
  magenta:   '#FF2D95',
  green:     '#66FF99',
  ltBlue:    '#6699FF',
  text:      '#FFFFFF',
  textDim:   '#9999CC',
  textFaint: '#334466',
}

const ROLE_COLOR: Record<string, string> = {
  teal:    C.teal,
  purple:  C.purple,
  amber:   C.amber,
  magenta: C.magenta,
  slate:   C.textDim,
  green:   C.green,
}

const STAIN_PALETTE = [
  C.teal, C.cyan, C.purple, C.amber, C.green, C.ltBlue, C.magenta, C.textDim,
]

const STAGE_COLORS: Record<string, string> = {
  scanner:    C.teal,
  babelshark: C.orange,
  qc:         C.cyan,
  dicom:      C.ltBlue,
  upload:     C.green,
}

const STAGE_LETTER: Record<string, string> = {
  scanner: 'S', babelshark: 'B', qc: 'Q', dicom: 'D', upload: 'U',
}

const MONO = '"JetBrains Mono", "Courier New", monospace'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtClock(d: Date): string {
  return d.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}

function fmtDate(d: Date): string {
  return d.toLocaleDateString('en-GB', {
    weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
  }).toUpperCase()
}

function formatCountdown(iso: string | null | undefined, now: Date): string {
  if (!iso) return ''
  const target = new Date(iso)
  const diffMs = target.getTime() - now.getTime()
  if (diffMs <= 0) return 'NOW'
  const totalMin = Math.floor(diffMs / 60000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  const timeStr = target.toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', hour12: false,
    timeZone: 'Europe/Copenhagen',
  })
  return h > 0 ? `${timeStr} (${h}h ${m}m)` : `${timeStr} (${m}m)`
}

function opDayLabel(iso: string | null | undefined): string {
  if (!iso) return '07:00'
  return new Date(iso).toLocaleTimeString('en-GB', {
    hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Europe/Copenhagen',
  })
}

// ── Hooks ─────────────────────────────────────────────────────────────────────

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

function useRefreshCountdown(trigger: unknown, seconds = 10) {
  const [n, setN] = useState(seconds)
  useEffect(() => {
    setN(seconds)
    const t = setInterval(() => setN(c => Math.max(0, c - 1)), 1000)
    return () => clearInterval(t)
  }, [trigger, seconds])
  return n
}

// ── WallboardHeader ───────────────────────────────────────────────────────────

interface HeaderProps {
  data: WallboardResponse | undefined
  now: Date
  isError: boolean
}

function WallboardHeader({ data, now, isError }: HeaderProps) {
  const status = isError ? 'OFFLINE'
    : data?.system_status === 'degraded' ? 'DEGRADED'
    : 'ONLINE'
  const statusBg = isError ? C.red : status === 'DEGRADED' ? C.amber : C.green

  const modeName = data?.active_mode
    ? data.active_mode.replace(/_/g, ' ').toUpperCase()
    : 'STANDBY'

  const nextLabel = data?.next_mode_switch_at
    ? formatCountdown(data.next_mode_switch_at, now)
    : null
  const nextName = data?.next_mode_name
    ? data.next_mode_name.replace(/_/g, ' ').toUpperCase()
    : ''

  return (
    <div style={{
      background: C.orange,
      display: 'flex',
      alignItems: 'stretch',
      height: '100%',
      overflow: 'hidden',
      borderRadius: 4,
    }}>
      {/* Left — logo + brand */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14,
        padding: '0 18px 0 14px',
        borderRight: '2px solid rgba(0,0,0,0.15)',
        flexShrink: 0, minWidth: 230,
      }}>
        <div style={{
          background: 'rgba(0,0,0,0.16)',
          border: '1.5px solid rgba(160,110,30,0.55)',
          borderRadius: 6,
          width: 84, height: 72,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          flexShrink: 0,
          overflow: 'hidden',
        }}>
          <img
            src="/dpars-logo.png"
            alt="DPARS"
            style={{ maxWidth: 76, maxHeight: 64, objectFit: 'contain' }}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        </div>
        <div>
          <div style={{
            fontSize: 28, fontWeight: 900, color: 'rgba(0,0,0,0.80)',
            letterSpacing: '0.05em', fontFamily: MONO, lineHeight: 1,
          }}>
            DPARS
          </div>
          <div style={{
            fontSize: 9, color: 'rgba(0,0,0,0.55)',
            letterSpacing: '0.14em', marginTop: 4,
            fontFamily: MONO, textTransform: 'uppercase', lineHeight: 1.4,
          }}>
            Digital Pathology<br />Archiving System
          </div>
        </div>
      </div>

      {/* Center — title + active mode + next switch */}
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 5,
      }}>
        <div style={{
          fontSize: 11, fontWeight: 700, color: 'rgba(0,0,0,0.48)',
          letterSpacing: '0.22em', fontFamily: MONO, textTransform: 'uppercase',
        }}>
          LIVE LABORATORY OPERATIONS WALLBOARD
        </div>
        <div style={{
          fontSize: 28, fontWeight: 900, color: 'rgba(0,0,0,0.82)',
          letterSpacing: '0.05em', fontFamily: MONO,
        }}>
          {modeName}
        </div>
        {nextLabel && (
          <div style={{
            fontSize: 10, color: 'rgba(0,0,0,0.52)',
            fontFamily: MONO, letterSpacing: '0.10em',
          }}>
            NEXT: {nextName} @ {nextLabel}
          </div>
        )}
      </div>

      {/* Right — clock + date + status */}
      <div style={{
        display: 'flex', flexDirection: 'column',
        alignItems: 'flex-end', justifyContent: 'center',
        padding: '0 18px 0 14px',
        borderLeft: '2px solid rgba(0,0,0,0.15)',
        gap: 5, flexShrink: 0, minWidth: 210,
      }}>
        <div style={{
          fontSize: 38, fontWeight: 900, fontFamily: MONO,
          color: 'rgba(0,0,0,0.82)', letterSpacing: '0.04em', lineHeight: 1,
        }}>
          {fmtClock(now)}
        </div>
        <div style={{
          fontSize: 10, color: 'rgba(0,0,0,0.52)',
          fontFamily: MONO, letterSpacing: '0.12em',
        }}>
          {fmtDate(now)}
        </div>
        <div style={{
          background: statusBg,
          color: C.bg,
          fontSize: 10, fontWeight: 900, letterSpacing: '0.16em',
          fontFamily: MONO, padding: '2px 10px', borderRadius: 3,
          animation: isError ? 'lcBlink 1s ease-in-out infinite' : undefined,
        }}>
          ● {status}
        </div>
      </div>
    </div>
  )
}

// ── KPI Strip ─────────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string
  value: string
  color: string
  subtitle?: string
}

function KpiCard({ label, value, color, subtitle }: KpiCardProps) {
  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: C.panel,
      border: `1px solid ${color}2A`,
      borderTop: `3px solid ${color}`,
      borderRadius: 4,
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      padding: '8px 4px', gap: 4,
    }}>
      <div style={{
        fontSize: 48, fontWeight: 900, color,
        fontFamily: MONO, lineHeight: 1,
        letterSpacing: '-0.02em',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        maxWidth: '100%', textAlign: 'center',
      }}>
        {value}
      </div>
      <div style={{
        fontSize: 8, color: C.textDim, letterSpacing: '0.16em',
        fontFamily: MONO, textAlign: 'center', textTransform: 'uppercase',
      }}>
        {label}
      </div>
      {subtitle && (
        <div style={{
          fontSize: 8, color: C.textFaint, letterSpacing: '0.10em', fontFamily: MONO,
        }}>
          {subtitle}
        </div>
      )}
    </div>
  )
}

function KpiStrip({ data }: { data: WallboardResponse | undefined }) {
  const k = data?.kpis
  const peak = data?.peak_upload_hour ?? '—'

  return (
    <div style={{ display: 'flex', gap: 6, height: '100%', alignItems: 'stretch' }}>
      <KpiCard
        label="Uploaded Today"
        value={String(k?.uploaded_today ?? 0)}
        color={C.teal}
      />
      <KpiCard
        label="Scanned Today"
        value={String(k?.slides_scanned_today ?? 0)}
        color={C.cyan}
      />
      <KpiCard
        label="Queue Depth"
        value={String(k?.queue_depth ?? 0)}
        color={(k?.queue_depth ?? 0) >= 20 ? C.red : C.ltBlue}
      />
      <KpiCard
        label="Active Processing"
        value={String(k?.active_processing ?? 0)}
        color={C.orange}
      />
      <KpiCard
        label="Failed Today"
        value={String(k?.failed ?? 0)}
        color={(k?.failed ?? 0) >= 3 ? C.red : C.textDim}
      />
      <KpiCard
        label="Recovery Backlog"
        value={String(k?.recovery_backlog ?? 0)}
        color={(k?.recovery_backlog ?? 0) >= 5 ? C.amber : C.purple}
      />
      <KpiCard
        label="Avg Slides / Hour"
        value={String(k?.avg_slides_per_hour ?? 0)}
        color={C.green}
      />
      <KpiCard
        label="Peak Upload Hour"
        value={peak}
        color={C.amber}
        subtitle="highest volume"
      />
    </div>
  )
}

// ── Scanner Fleet ─────────────────────────────────────────────────────────────

function ScannerCard({ sc }: { sc: WallboardResponse['scanners'][0] }) {
  const color = ROLE_COLOR[sc.role_color] ?? C.textDim
  const stateColor = sc.operational_state === 'active' ? C.green
    : sc.operational_state === 'idle' ? C.amber
    : C.textFaint

  return (
    <div style={{
      flex: 1, minWidth: 0,
      background: C.panel,
      border: `1px solid ${color}2A`,
      borderTop: `2px solid ${color}`,
      borderRadius: 4,
      padding: '10px 12px',
      display: 'flex', flexDirection: 'column', gap: 5,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center',
        justifyContent: 'space-between', gap: 6,
      }}>
        <div style={{
          fontSize: 14, fontWeight: 900, color, letterSpacing: '0.08em',
          fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap', flex: 1,
        }}>
          {sc.scanner_id}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, flexShrink: 0 }}>
          <div style={{
            width: 9, height: 9, borderRadius: '50%', background: stateColor,
            flexShrink: 0,
            animation: sc.operational_state === 'active'
              ? 'lcPulse 2s ease-in-out infinite' : undefined,
          }} />
          <div style={{
            fontSize: 9, color: stateColor, fontWeight: 700,
            letterSpacing: '0.14em', fontFamily: MONO,
          }}>
            {sc.operational_state.toUpperCase()}
          </div>
        </div>
      </div>

      <div style={{
        fontSize: 9, color: C.textDim, letterSpacing: '0.12em',
        fontFamily: MONO, textTransform: 'uppercase',
      }}>
        {sc.role}
      </div>

      <div style={{ display: 'flex', gap: 16, marginTop: 2 }}>
        <div>
          <div style={{
            fontSize: 32, fontWeight: 900, color, lineHeight: 1, fontFamily: MONO,
          }}>
            {sc.slides_today}
          </div>
          <div style={{
            fontSize: 8, color: C.textFaint, letterSpacing: '0.12em', fontFamily: MONO,
          }}>
            SCANNED
          </div>
        </div>
        <div>
          <div style={{
            fontSize: 32, fontWeight: 900, color: C.teal, lineHeight: 1, fontFamily: MONO,
          }}>
            {sc.uploaded_today}
          </div>
          <div style={{
            fontSize: 8, color: C.textFaint, letterSpacing: '0.12em', fontFamily: MONO,
          }}>
            UPLOADED
          </div>
        </div>
      </div>

      {sc.destination && sc.destination !== '—' && (
        <div style={{
          fontSize: 8, color: C.textFaint, letterSpacing: '0.10em', fontFamily: MONO,
          textTransform: 'uppercase', overflow: 'hidden', textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}>
          → {sc.destination}
        </div>
      )}
    </div>
  )
}

function ScannerFleet({ data }: { data: WallboardResponse | undefined }) {
  const scanners = data?.scanners ?? []
  if (scanners.length === 0) {
    return (
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%', color: C.textFaint, fontSize: 11, fontFamily: MONO,
      }}>
        NO SCANNER DATA
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', gap: 6, height: '100%', alignItems: 'stretch' }}>
      {scanners.map(sc => <ScannerCard key={sc.scanner_id} sc={sc} />)}
    </div>
  )
}

// ── Pipeline Flow ─────────────────────────────────────────────────────────────

function PipelineFlow({ data }: { data: WallboardResponse | undefined }) {
  const stages = data?.pipeline ?? []

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${C.textFaint}33`,
      borderRadius: 4, height: '100%',
      display: 'flex', flexDirection: 'column',
      padding: '12px 10px',
    }}>
      <div style={{
        fontSize: 8, color: C.orange, letterSpacing: '0.20em',
        fontFamily: MONO, marginBottom: 8, textTransform: 'uppercase',
      }}>
        Pipeline Flow
      </div>
      <div style={{
        flex: 1, display: 'flex', flexDirection: 'column',
        justifyContent: 'space-evenly',
      }}>
        {stages.map((stage, i) => {
          const color = STAGE_COLORS[stage.name] ?? C.textDim
          const hasActive = stage.active > 0
          const hasFailed = stage.failed > 0
          return (
            <div key={stage.name} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{
                display: 'flex', flexDirection: 'column',
                alignItems: 'center', width: 38, flexShrink: 0,
              }}>
                {i > 0 && (
                  <div style={{ width: 2, height: 10, background: `${C.textFaint}55` }} />
                )}
                <div style={{
                  width: 38, height: 38, borderRadius: '50%',
                  background: hasActive ? `${color}18` : C.panel2,
                  border: `2px solid ${color}`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: hasActive ? 16 : 12, fontWeight: 900, color,
                  fontFamily: MONO,
                  animation: hasActive ? 'lcPulse 2s ease-in-out infinite' : undefined,
                  flexShrink: 0,
                }}>
                  {hasActive ? stage.active : STAGE_LETTER[stage.name] ?? '•'}
                </div>
              </div>
              <div>
                <div style={{
                  fontSize: 10, fontWeight: 700, color, letterSpacing: '0.12em',
                  fontFamily: MONO, textTransform: 'uppercase',
                }}>
                  {stage.label}
                </div>
                <div style={{
                  fontSize: 9, color: C.textDim, marginTop: 1, fontFamily: MONO,
                }}>
                  {stage.today} today
                  {hasFailed && (
                    <span style={{ color: C.red, marginLeft: 6 }}>{stage.failed} failed</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Stain Analysis ────────────────────────────────────────────────────────────

function StainAnalysis({ data }: { data: WallboardResponse | undefined }) {
  const stains = data?.stain_distribution ?? []

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${C.textFaint}33`,
      borderRadius: 4, height: '100%',
      display: 'flex', flexDirection: 'column',
      padding: '12px 20px',
    }}>
      <div style={{
        fontSize: 8, color: C.orange, letterSpacing: '0.20em',
        fontFamily: MONO, marginBottom: 10, textTransform: 'uppercase',
      }}>
        Stain Distribution — Operational Day
      </div>
      {stains.length === 0 ? (
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: C.textFaint, fontSize: 11, fontFamily: MONO,
        }}>
          NO STAIN DATA THIS PERIOD
        </div>
      ) : (
        <div style={{
          flex: 1, display: 'flex', flexDirection: 'column',
          justifyContent: 'space-evenly',
        }}>
          {stains.map((s, i) => {
            const barColor = STAIN_PALETTE[i % STAIN_PALETTE.length]
            return (
              <div key={s.stain} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <div style={{
                  width: 58, textAlign: 'right', flexShrink: 0,
                  fontSize: 14, fontWeight: 700, color: barColor,
                  fontFamily: MONO, overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {s.stain}
                </div>
                <div style={{
                  flex: 1, height: 16, borderRadius: 3,
                  background: C.panel2, overflow: 'hidden', position: 'relative',
                }}>
                  <div style={{
                    position: 'absolute', left: 0, top: 0, bottom: 0,
                    width: `${s.percentage}%`,
                    background: barColor, borderRadius: 3,
                    transition: 'width 1s ease-out',
                  }} />
                </div>
                <div style={{
                  width: 50, textAlign: 'right', flexShrink: 0,
                  fontSize: 18, fontWeight: 900, color: C.text, fontFamily: MONO,
                }}>
                  {s.percentage}%
                </div>
                <div style={{
                  width: 38, textAlign: 'right', flexShrink: 0,
                  fontSize: 12, color: C.textDim, fontFamily: MONO,
                }}>
                  {s.count}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── Upload Chart ──────────────────────────────────────────────────────────────

function UploadChart({ data }: { data: WallboardResponse | undefined }) {
  const hours = data?.uploads_by_hour ?? []
  const chartData = hours.map(h => ({ name: h.hour_label, count: h.count }))
  const lastIdx = chartData.length - 1

  return (
    <div style={{
      background: C.panel,
      border: `1px solid ${C.textFaint}33`,
      borderRadius: 4, height: '100%',
      display: 'flex', flexDirection: 'column',
      padding: '12px 10px',
    }}>
      <div style={{
        fontSize: 8, color: C.orange, letterSpacing: '0.20em',
        fontFamily: MONO, marginBottom: 6, textTransform: 'uppercase',
      }}>
        Uploads / Hour
      </div>
      <div style={{ flex: 1 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={chartData}
            margin={{ top: 4, right: 4, bottom: 0, left: -24 }}
            barSize={7}
          >
            <XAxis
              dataKey="name"
              tick={{ fill: C.textFaint, fontSize: 7, fontFamily: MONO }}
              axisLine={{ stroke: C.textFaint }}
              tickLine={false}
              interval={3}
            />
            <YAxis
              tick={{ fill: C.textFaint, fontSize: 7, fontFamily: MONO }}
              axisLine={false}
              tickLine={false}
              width={24}
            />
            <Tooltip
              contentStyle={{
                background: C.panel2, border: `1px solid ${C.teal}`,
                borderRadius: 4, fontSize: 10, fontFamily: MONO,
              }}
              labelStyle={{ color: C.textDim }}
              itemStyle={{ color: C.teal }}
            />
            <Bar dataKey="count" radius={[2, 2, 0, 0]}>
              {chartData.map((_, idx) => (
                <Cell key={idx} fill={idx === lastIdx ? C.teal : `${C.teal}55`} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Alert Strip ───────────────────────────────────────────────────────────────

interface AlertStripProps {
  data: WallboardResponse | undefined
  isError: boolean
  countdown: number
  opDayStart: string
}

function AlertStrip({ data, isError, countdown, opDayStart }: AlertStripProps) {
  if (isError) {
    return (
      <div style={{
        background: C.red, borderRadius: 4,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        height: '100%',
        animation: 'lcBlink 1s ease-in-out infinite',
      }}>
        <span style={{
          fontSize: 12, fontWeight: 900, color: C.text,
          letterSpacing: '0.20em', fontFamily: MONO,
        }}>
          ⚡ DPARS WALLBOARD DATA CONNECTION LOST — ATTEMPTING RECONNECT
        </span>
      </div>
    )
  }

  const allAlerts = data?.alerts ?? []
  const hasAlerts = allAlerts.length > 0

  return (
    <div style={{
      background: hasAlerts ? `${C.amber}18` : C.panel,
      borderTop: `1px solid ${hasAlerts ? C.amber : C.textFaint}44`,
      borderRadius: 4,
      display: 'flex', alignItems: 'center',
      justifyContent: 'space-between',
      padding: '0 16px', height: '100%', gap: 12,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{
          width: 8, height: 8, borderRadius: '50%',
          background: hasAlerts ? C.amber : C.green, flexShrink: 0,
          animation: hasAlerts ? 'lcPulse 2s ease-in-out infinite' : undefined,
        }} />
        {hasAlerts ? (
          <span style={{
            fontSize: 10, color: C.amber, fontWeight: 700,
            letterSpacing: '0.14em', fontFamily: MONO,
          }}>
            {allAlerts[0].message.toUpperCase()}
            {allAlerts.length > 1 && ` (+${allAlerts.length - 1})`}
          </span>
        ) : (
          <span style={{
            fontSize: 10, color: C.textDim, letterSpacing: '0.14em', fontFamily: MONO,
          }}>
            ALL SYSTEMS NOMINAL
          </span>
        )}
      </div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 20,
        fontSize: 9, color: C.textFaint, letterSpacing: '0.12em', fontFamily: MONO,
      }}>
        <span>OP DAY FROM {opDayStart}</span>
        <span style={{ color: countdown <= 3 ? C.amber : C.textFaint }}>
          REFRESH {countdown}s
        </span>
        <span>DPARS LIVE OPERATIONS MONITOR</span>
      </div>
    </div>
  )
}

// ── Root ──────────────────────────────────────────────────────────────────────

export function Wallboard() {
  const { data, isError, dataUpdatedAt } = useWallboard()
  const now = useClock()
  const countdown = useRefreshCountdown(dataUpdatedAt, 10)
  const opDayStart = opDayLabel(data?.operational_day_start)

  return (
    <>
      <style>{`
        @keyframes lcPulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.5; }
        }
        @keyframes lcBlink {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.35; }
        }
        * { box-sizing: border-box; }
        body { margin: 0; overflow: hidden; background: ${C.bg}; }
      `}</style>
      <div style={{
        background: C.bg,
        width: '100vw', height: '100vh',
        display: 'grid',
        gridTemplateRows: '110px 116px 148px 1fr 52px',
        gap: 6,
        padding: 6,
        overflow: 'hidden',
      }}>
        <WallboardHeader data={data} now={now} isError={isError} />
        <KpiStrip data={data} />
        <ScannerFleet data={data} />
        <div style={{
          display: 'grid',
          gridTemplateColumns: '220px 1fr 280px',
          gap: 6,
          minHeight: 0,
        }}>
          <PipelineFlow data={data} />
          <StainAnalysis data={data} />
          <UploadChart data={data} />
        </div>
        <AlertStrip
          data={data}
          isError={isError}
          countdown={countdown}
          opDayStart={opDayStart}
        />
      </div>
    </>
  )
}
