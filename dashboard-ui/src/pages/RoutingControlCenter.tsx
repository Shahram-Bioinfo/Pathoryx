/**
 * Phase 4.8 — Routing Control Center
 *
 * Stage 1 (dry-run): displays routing policy configuration, live mode,
 * active overrides, routing preview, and audit trail.
 *
 * This page is read-mostly — operators can create/cancel temporary overrides,
 * but the config file remains the primary source of truth.
 */
import { useState } from 'react'
import { format, parseISO } from 'date-fns'
import { AlertTriangle, ChevronRight, Clock, Eye, GitBranch, Info, Layers, Plus, RefreshCw, Shield, Trash2, X, Zap } from 'lucide-react'
import {
  useCreateOverride,
  useDecisionChain,
  useDeleteOverride,
  useRoutingDecisions,
  useRoutingOverrides,
  useRoutingPreview,
  useRoutingStatus,
} from '../hooks/useRouting'
import type { CreateOverrideRequest, RoutingDecisionItem, RoutingModeInfo, RoutingOverrideItem, RoutingPreviewItem } from '../types/api'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtDateShort(iso: string | null | undefined): string {
  if (!iso) return '—'
  try { return format(parseISO(iso), 'MM-dd HH:mm') } catch { return iso }
}

function reasonColor(reason: string): string {
  if (reason.startsWith('manual_override')) return 'var(--chart-amber)'
  if (reason.startsWith('color_dot')) return 'var(--chart-violet)'
  if (reason.startsWith('scanner_policy')) return 'var(--chart-cyan)'
  if (reason.startsWith('mode_default')) return 'var(--chart-teal)'
  if (reason === 'fallback') return 'var(--chart-slate)'
  if (reason === 'no_policy') return 'var(--chart-rose)'
  return 'var(--text-muted)'
}

function colorDotSwatch(color: string): string {
  const map: Record<string, string> = {
    red: '#ef4444', blue: '#3b82f6', green: '#22c55e',
    yellow: '#eab308', orange: '#f97316', purple: '#a855f7',
  }
  return map[color.toLowerCase()] ?? 'var(--text-muted)'
}

// ── Sub-components ────────────────────────────────────────────────────────────

function SectionHeader({ icon: Icon, title, count, color }: {
  icon: React.ComponentType<{ style?: React.CSSProperties }>
  title: string
  count?: number
  color?: string
}) {
  return (
    <div className="flex items-center gap-2 mb-4">
      <Icon style={{ width: 14, height: 14, color: color ?? 'var(--accent)', flexShrink: 0 }} />
      <h2 style={{
        fontSize: 11, fontWeight: 700, textTransform: 'uppercase',
        letterSpacing: '0.18em', color: color ?? 'var(--accent)',
      }}>
        {title}
      </h2>
      {count !== undefined && (
        <span style={{
          marginLeft: 6, fontSize: 10, fontWeight: 700,
          color: 'var(--text-muted)',
          background: 'var(--surface-inset)',
          border: '1px solid var(--border-default)',
          borderRadius: 4, padding: '1px 6px',
        }}>{count}</span>
      )}
    </div>
  )
}

function DryRunBadge() {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: '3px 10px', borderRadius: 4,
      background: 'rgba(234,179,8,0.10)',
      border: '1px solid rgba(234,179,8,0.30)',
      fontSize: 10, fontWeight: 700, letterSpacing: '0.12em',
      color: 'var(--chart-amber)', textTransform: 'uppercase',
    }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--chart-amber)', display: 'inline-block', animation: 'lcBlink 2s ease-in-out infinite' }} />
      DRY-RUN — STAGE 1
    </span>
  )
}

function ModeCard({ mode }: { mode: RoutingModeInfo }) {
  return (
    <div className="mission-card" style={{
      padding: '14px 16px',
      borderLeft: `3px solid ${mode.is_active ? 'var(--chart-emerald)' : 'var(--border-default)'}`,
      position: 'relative',
    }}>
      {mode.is_active && (
        <span style={{
          position: 'absolute', top: 8, right: 10,
          fontSize: 8, fontWeight: 700, letterSpacing: '0.18em',
          textTransform: 'uppercase',
          color: 'var(--chart-emerald)',
          display: 'flex', alignItems: 'center', gap: 4,
        }}>
          <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--chart-emerald)', display: 'inline-block', animation: 'lcBlink 2.2s ease-in-out infinite' }} />
          ACTIVE
        </span>
      )}
      <div style={{ fontSize: 13, fontWeight: 700, color: mode.is_active ? 'var(--text-primary)' : 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
        {mode.name.replace(/_/g, ' ')}
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
        {mode.active_start} → {mode.active_end}
        {mode.is_overnight && <span style={{ color: 'var(--chart-amber)', marginLeft: 6, fontSize: 9 }}>OVERNIGHT</span>}
      </div>
      <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 2 }}>
        Profile: <span style={{ color: 'var(--accent)', fontWeight: 600 }}>{mode.profile}</span>
        &nbsp;·&nbsp;
        Default: <span style={{ color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{mode.default_destination}</span>
      </div>
      {mode.scanner_destinations.length > 0 && (
        <div style={{ marginTop: 8, borderTop: '1px solid var(--border-faint)', paddingTop: 8 }}>
          <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--text-faint)', marginBottom: 4 }}>
            Scanner routes
          </div>
          <div className="space-y-1">
            {mode.scanner_destinations.map(s => (
              <div key={s.scanner_id} className="flex justify-between">
                <span style={{ fontSize: 10, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{s.scanner_id}</span>
                <span style={{ fontSize: 10, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>→ {s.destination}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function OverrideRow({ override, onDelete }: {
  override: RoutingOverrideItem
  onDelete: (id: number) => void
}) {
  const remaining = override.expires_at
    ? Math.max(0, Math.round((new Date(override.expires_at).getTime() - Date.now()) / 60_000))
    : null

  return (
    <div className="flex items-center gap-3 py-2.5 px-3" style={{
      borderBottom: '1px solid var(--border-faint)',
      background: override.is_active ? 'var(--accent-faint)' : 'transparent',
    }}>
      <div style={{
        width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
        background: override.is_active ? 'var(--chart-amber)' : 'var(--chart-slate)',
        animation: override.is_active ? 'lcBlink 1.8s ease-in-out infinite' : 'none',
        boxShadow: override.is_active ? '0 0 5px var(--chart-amber)' : 'none',
      }} />
      <div className="flex-1 min-w-0">
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>
          <span style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--chart-amber)' }}>{override.target_type}</span>
          <span style={{ color: 'var(--text-muted)', margin: '0 5px' }}>:</span>
          {override.target_value}
          <span style={{ color: 'var(--text-muted)', margin: '0 5px' }}>→</span>
          <span style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--chart-teal)' }}>{override.destination}</span>
        </div>
        {override.reason && (
          <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 2 }}>{override.reason}</div>
        )}
      </div>
      {remaining !== null && (
        <div style={{ fontSize: 10, color: remaining < 10 ? 'var(--chart-rose)' : 'var(--text-muted)', flexShrink: 0, fontFamily: '"JetBrains Mono", monospace' }}>
          {remaining}m left
        </div>
      )}
      {override.created_by && (
        <div style={{ fontSize: 9, color: 'var(--text-faint)', flexShrink: 0 }}>{override.created_by}</div>
      )}
      <button
        onClick={() => onDelete(override.id)}
        style={{
          padding: '3px 6px', borderRadius: 3, cursor: 'pointer',
          background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.20)',
          color: 'var(--chart-rose)', flexShrink: 0,
          display: 'flex', alignItems: 'center',
        }}
        title="Deactivate override"
      >
        <Trash2 style={{ width: 11, height: 11 }} />
      </button>
    </div>
  )
}

function PreviewRow({ item }: { item: RoutingPreviewItem }) {
  return (
    <tr>
      <td style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace', maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {item.original_filename ?? item.slide_id ?? '—'}
      </td>
      <td style={{ padding: '6px 10px', fontSize: 11, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>
        {item.scanner_id ?? '—'}
      </td>
      <td style={{ padding: '6px 10px', fontSize: 11 }}>
        {item.color_dot ? (
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: colorDotSwatch(item.color_dot), display: 'inline-block' }} />
            <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>{item.color_dot}</span>
          </span>
        ) : <span style={{ color: 'var(--text-faint)' }}>—</span>}
      </td>
      <td style={{ padding: '6px 10px', fontSize: 11, color: 'var(--chart-teal)', fontFamily: '"JetBrains Mono", monospace' }}>
        {item.predicted_destination}
      </td>
      <td style={{ padding: '6px 10px' }}>
        <span style={{
          fontSize: 9, fontWeight: 600, letterSpacing: '0.08em',
          textTransform: 'uppercase', color: reasonColor(item.routing_reason),
          background: 'var(--surface-inset)', border: '1px solid var(--border-faint)',
          borderRadius: 3, padding: '2px 6px', display: 'inline-block',
          maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {item.routing_reason.replace(/_/g, ' ')}
        </span>
      </td>
      <td style={{ padding: '6px 10px', fontSize: 10, color: 'var(--text-faint)' }}>
        {item.current_status?.replace(/_/g, ' ')}
      </td>
    </tr>
  )
}

// ── Create Override modal ─────────────────────────────────────────────────────

function CreateOverrideForm({ onClose }: { onClose: () => void }) {
  const { mutate, isPending } = useCreateOverride()
  const [form, setForm] = useState<CreateOverrideRequest>({
    target_type: 'scanner',
    target_value: '',
    destination: '',
    reason: '',
    created_by: '',
    expires_at: null,
  })
  const [durationMinutes, setDurationMinutes] = useState<number>(120)

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const expires_at = durationMinutes > 0
      ? new Date(Date.now() + durationMinutes * 60_000).toISOString()
      : null
    mutate({ ...form, expires_at }, { onSuccess: onClose })
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '7px 10px', fontSize: 12,
    background: 'var(--surface-inset)', border: '1px solid var(--border-default)',
    borderRadius: 5, color: 'var(--text-primary)', outline: 'none',
  }

  const labelStyle: React.CSSProperties = {
    fontSize: 10, fontWeight: 600, textTransform: 'uppercase',
    letterSpacing: '0.14em', color: 'var(--text-muted)', marginBottom: 4, display: 'block',
  }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div className="mission-card" style={{ width: 440, padding: 24, position: 'relative' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 16 }}>
          Create Routing Override
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label style={labelStyle}>Target type</label>
            <select
              value={form.target_type}
              onChange={e => setForm(f => ({ ...f, target_type: e.target.value as 'scanner' | 'file' | 'case' }))}
              style={{ ...inputStyle, cursor: 'pointer' }}
            >
              <option value="scanner">Scanner</option>
              <option value="file">File (artifact ID)</option>
              <option value="case">Case</option>
            </select>
          </div>
          <div>
            <label style={labelStyle}>Target value ({form.target_type} ID)</label>
            <input
              type="text"
              required
              placeholder={form.target_type === 'scanner' ? 'e.g. HOMEONE' : 'identifier'}
              value={form.target_value}
              onChange={e => setForm(f => ({ ...f, target_value: e.target.value }))}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Destination</label>
            <input
              type="text"
              required
              placeholder="e.g. research_storage_B"
              value={form.destination}
              onChange={e => setForm(f => ({ ...f, destination: e.target.value }))}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Duration (minutes, 0 = no expiry)</label>
            <input
              type="number"
              min={0}
              value={durationMinutes}
              onChange={e => setDurationMinutes(Number(e.target.value))}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Reason</label>
            <input
              type="text"
              placeholder="e.g. Project ABC emergency"
              value={form.reason ?? ''}
              onChange={e => setForm(f => ({ ...f, reason: e.target.value || null }))}
              style={inputStyle}
            />
          </div>
          <div>
            <label style={labelStyle}>Created by</label>
            <input
              type="text"
              placeholder="Operator name"
              value={form.created_by ?? ''}
              onChange={e => setForm(f => ({ ...f, created_by: e.target.value || null }))}
              style={inputStyle}
            />
          </div>
          <div className="flex gap-3 pt-2">
            <button
              type="submit"
              disabled={isPending}
              style={{
                flex: 1, padding: '8px 0', borderRadius: 5, cursor: isPending ? 'not-allowed' : 'pointer',
                background: 'var(--accent-faint)', border: '1px solid var(--border-strong)',
                color: 'var(--accent)', fontSize: 12, fontWeight: 700,
              }}
            >
              {isPending ? 'Creating…' : 'Create Override'}
            </button>
            <button
              type="button"
              onClick={onClose}
              style={{
                padding: '8px 16px', borderRadius: 5, cursor: 'pointer',
                background: 'transparent', border: '1px solid var(--border-default)',
                color: 'var(--text-muted)', fontSize: 12,
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

// ── Decision chain detail panel ───────────────────────────────────────────────

function DecisionChainPanel({ decisionId, onClose }: { decisionId: number; onClose: () => void }) {
  const { data: chain, isLoading, isError } = useDecisionChain(decisionId)

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 50,
      background: 'rgba(0,0,0,0.55)', backdropFilter: 'blur(4px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        background: 'var(--surface-card)', border: '1px solid var(--border-default)',
        borderRadius: 10, width: '100%', maxWidth: 560,
        maxHeight: '80vh', overflowY: 'auto', padding: 24,
      }}>
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2">
            <Info style={{ width: 14, height: 14, color: 'var(--chart-indigo)' }} />
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--text-primary)' }}>
              Why This Slide?
            </span>
            <span style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace' }}>
              decision #{decisionId}
            </span>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-faint)', padding: 4 }}>
            <X style={{ width: 14, height: 14 }} />
          </button>
        </div>

        {isLoading && <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>Loading…</p>}
        {isError && <p style={{ fontSize: 11, color: 'var(--chart-rose)' }}>Failed to load decision chain.</p>}

        {chain && (
          <>
            {/* Summary */}
            <div style={{ background: 'var(--surface-inset)', borderRadius: 6, padding: '10px 14px', marginBottom: 16 }}>
              <div className="grid gap-y-1" style={{ gridTemplateColumns: '1fr 1fr' }}>
                {[
                  ['Slide', chain.slide_id ?? '—'],
                  ['Scanner', chain.scanner_id ?? '—'],
                  ['Mode', chain.mode?.replace(/_/g, ' ') ?? '—'],
                  ['Color Dot', chain.color_dot ?? 'none'],
                ].map(([label, value]) => (
                  <div key={label}>
                    <span style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--text-faint)' }}>{label} </span>
                    <span style={{ fontSize: 11, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Decision chain */}
            <div style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--text-faint)', marginBottom: 8 }}>Decision Chain</div>
              <div className="space-y-2">
                {chain.chain.map((step) => (
                  <div key={step.step} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                    padding: '8px 12px', borderRadius: 6,
                    background: step.applied ? 'rgba(99,102,241,0.08)' : 'var(--surface-inset)',
                    border: `1px solid ${step.applied ? 'rgba(99,102,241,0.30)' : 'var(--border-faint)'}`,
                  }}>
                    <div style={{
                      width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                      background: step.applied ? 'var(--chart-indigo)' : 'var(--surface-raised)',
                      color: step.applied ? '#fff' : 'var(--text-faint)',
                      fontSize: 10, fontWeight: 700,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}>
                      {step.step}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="flex items-center gap-2">
                        <span style={{ fontSize: 11, fontWeight: step.applied ? 700 : 500, color: step.applied ? 'var(--text-primary)' : 'var(--text-muted)' }}>
                          {step.label}
                        </span>
                        {step.applied && (
                          <span style={{ fontSize: 8, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: 'var(--chart-indigo)', background: 'rgba(99,102,241,0.12)', borderRadius: 3, padding: '1px 5px' }}>
                            APPLIED
                          </span>
                        )}
                      </div>
                      {step.value && (
                        <div style={{ fontSize: 11, color: step.applied ? 'var(--chart-teal)' : 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace', marginTop: 2 }}>
                          {step.value}
                        </div>
                      )}
                      {step.detail && (
                        <div style={{ fontSize: 10, color: 'var(--text-faint)', marginTop: 1 }}>{step.detail}</div>
                      )}
                    </div>
                    {step.applied && <ChevronRight style={{ width: 12, height: 12, color: 'var(--chart-indigo)', flexShrink: 0, marginTop: 4 }} />}
                  </div>
                ))}
              </div>
            </div>

            {/* Final decision */}
            <div style={{
              padding: '12px 14px', borderRadius: 6,
              background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.25)',
            }}>
              <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--chart-indigo)', marginBottom: 6 }}>Final Decision</div>
              <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--chart-teal)', fontFamily: '"JetBrains Mono", monospace' }}>
                {chain.final_destination}
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted)', marginTop: 4 }}>
                reason: {chain.final_reason.replace(/_/g, ' ')}
              </div>
              <div style={{ fontSize: 9, color: 'var(--chart-amber)', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.10em', marginTop: 6 }}>
                {chain.dry_run ? 'DRY-RUN — actual destination unchanged' : 'LIVE — destination applied'}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export function RoutingControlCenter() {
  const { data: status, isLoading: statusLoading, refetch: refetchStatus } = useRoutingStatus()
  const { data: overrides, isLoading: ovLoading, refetch: refetchOv } = useRoutingOverrides()
  const { data: preview, isLoading: pvLoading, refetch: refetchPv } = useRoutingPreview(100)
  const { data: decisions } = useRoutingDecisions(50)
  const { mutate: deleteOv } = useDeleteOverride()
  const [showCreateForm, setShowCreateForm] = useState(false)
  const [selectedDecisionId, setSelectedDecisionId] = useState<number | null>(null)

  function handleRefresh() {
    refetchStatus()
    refetchOv()
    refetchPv()
  }

  return (
    <div className="space-y-6">
      {showCreateForm && <CreateOverrideForm onClose={() => setShowCreateForm(false)} />}
      {selectedDecisionId !== null && (
        <DecisionChainPanel
          decisionId={selectedDecisionId}
          onClose={() => setSelectedDecisionId(null)}
        />
      )}

      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 style={{
              fontSize: 20, fontWeight: 800, letterSpacing: '0.06em',
              color: 'var(--text-primary)', textTransform: 'uppercase',
            }}>
              Routing Control Center
            </h1>
            <DryRunBadge />
          </div>
          <p style={{ fontSize: 11, marginTop: 4, color: 'var(--text-muted)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
            Phase 4.8 Stage 1 — Config-First Policy Engine — Predictions Only
          </p>
        </div>
        <button
          className="btn-ghost-ops"
          onClick={handleRefresh}
          disabled={statusLoading}
        >
          <RefreshCw style={{ width: 12, height: 12 }} />
          Refresh
        </button>
      </div>

      {/* Validation issues banner */}
      {status && status.validation_issues.some(i => i.severity === 'error') && (
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: 10,
          padding: '12px 16px', borderRadius: 6,
          background: 'rgba(239,68,68,0.07)', border: '1px solid rgba(239,68,68,0.25)',
        }}>
          <AlertTriangle style={{ width: 14, height: 14, color: 'var(--chart-rose)', flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--chart-rose)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>
              Config Validation Errors
            </div>
            {status.validation_issues.map((issue, i) => (
              <div key={i} style={{ fontSize: 11, color: issue.severity === 'error' ? 'var(--chart-rose)' : 'var(--chart-amber)', marginTop: 3 }}>
                [{issue.severity.toUpperCase()}] {issue.message}
                {issue.field && <span style={{ color: 'var(--text-faint)', marginLeft: 6, fontFamily: '"JetBrains Mono", monospace', fontSize: 10 }}>({issue.field})</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Status strip */}
      <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
        {[
          {
            label: 'Active Mode',
            value: status?.active_mode?.replace(/_/g, ' ') ?? (statusLoading ? '…' : 'No Policy'),
            accent: 'var(--chart-emerald)',
            color: status?.active_mode ? 'var(--chart-emerald)' : 'var(--chart-rose)',
          },
          {
            label: 'Profile',
            value: status?.active_profile ?? '—',
            accent: 'var(--chart-cyan)',
            color: 'var(--chart-cyan)',
          },
          {
            label: 'Next Mode Switch',
            value: status?.next_mode ? `${status.next_mode.name.replace(/_/g, ' ')} @ ${status.next_mode.starts_at}` : '—',
            accent: 'var(--chart-amber)',
          },
          {
            label: 'Timezone',
            value: status?.timezone ?? '—',
            accent: 'var(--chart-indigo)',
          },
        ].map(({ label, value, accent, color }) => (
          <div key={label} className="mission-card" style={{ padding: '14px 16px', borderTop: `3px solid ${accent}` }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: color ?? 'var(--text-primary)', fontFamily: '"JetBrains Mono", monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</div>
            <div style={{ fontSize: 9, marginTop: 6, textTransform: 'uppercase', letterSpacing: '0.16em', color: 'var(--text-faint)' }}>{label}</div>
          </div>
        ))}
      </div>

      {/* Mode cards + color dot rules */}
      <div className="grid gap-5" style={{ gridTemplateColumns: '1fr 1fr' }}>

        {/* Operational Modes */}
        <div className="mission-card p-5">
          <SectionHeader icon={Clock} title="Operational Modes" count={status?.modes.length} />
          {statusLoading ? (
            <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>Loading…</p>
          ) : status?.modes.length === 0 ? (
            <p style={{ fontSize: 11, color: 'var(--chart-rose)' }}>No modes configured. Add routing_policies to babelshark_config.yaml.</p>
          ) : (
            <div className="space-y-3">
              {status?.modes.map(mode => (
                <ModeCard key={mode.name} mode={mode} />
              ))}
            </div>
          )}
        </div>

        {/* Color Dot Rules */}
        <div className="mission-card p-5">
          <SectionHeader icon={Layers} title="Color Dot Rules" count={status?.color_dot_rules.length} color="var(--chart-violet)" />
          {(status?.color_dot_rules ?? []).length === 0 ? (
            <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>No color-dot rules configured.</p>
          ) : (
            <div className="space-y-2">
              {status?.color_dot_rules.map(rule => (
                <div key={rule.color} className="flex items-center gap-3 py-2" style={{ borderBottom: '1px solid var(--border-faint)' }}>
                  <span style={{ width: 14, height: 14, borderRadius: '50%', background: colorDotSwatch(rule.color), flexShrink: 0, display: 'inline-block', boxShadow: `0 0 6px ${colorDotSwatch(rule.color)}60` }} />
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.10em', flex: 1 }}>
                    {rule.color}
                  </span>
                  <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>→</span>
                  <span style={{ fontSize: 12, color: 'var(--chart-teal)', fontFamily: '"JetBrains Mono", monospace' }}>
                    {rule.destination}
                  </span>
                </div>
              ))}
            </div>
          )}

          {/* Default destination + fallback */}
          <div style={{ marginTop: 20, paddingTop: 12, borderTop: '1px solid var(--border-faint)' }}>
            <SectionHeader icon={Shield} title="Fallback" color="var(--chart-slate)" />
            <div className="flex justify-between">
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Global fallback destination</span>
              <span style={{ fontSize: 11, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace' }}>
                {status?.fallback_destination || '—'}
              </span>
            </div>
            <div className="flex justify-between mt-1">
              <span style={{ fontSize: 11, color: 'var(--text-muted)' }}>Current default destination</span>
              <span style={{ fontSize: 11, color: 'var(--chart-cyan)', fontFamily: '"JetBrains Mono", monospace' }}>
                {status?.active_default_destination || '—'}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Active Overrides */}
      <div className="mission-card p-5">
        <div className="flex items-center justify-between mb-4">
          <SectionHeader icon={Zap} title="Active Overrides" count={overrides?.total_active} color="var(--chart-amber)" />
          <button
            className="btn-ops"
            onClick={() => setShowCreateForm(true)}
            style={{ fontSize: 11 }}
          >
            <Plus style={{ width: 12, height: 12 }} />
            New Override
          </button>
        </div>
        {ovLoading ? (
          <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>Loading…</p>
        ) : (overrides?.active ?? []).length === 0 ? (
          <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>No active overrides. System following config policy.</p>
        ) : (
          <div style={{ border: '1px solid var(--border-default)', borderRadius: 6, overflow: 'hidden' }}>
            {overrides?.active.map(ov => (
              <OverrideRow
                key={ov.id}
                override={ov}
                onDelete={id => deleteOv(id)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Routing Preview */}
      <div className="mission-card p-5">
        <div className="flex items-center justify-between mb-4">
          <SectionHeader icon={Eye} title="Routing Preview" count={preview?.total} color="var(--chart-teal)" />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 10, color: 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace' }}>
              Mode: {preview?.active_mode?.replace(/_/g, ' ') ?? '—'}
            </span>
            <DryRunBadge />
          </div>
        </div>
        {pvLoading ? (
          <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>Computing preview…</p>
        ) : (preview?.items ?? []).length === 0 ? (
          <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>No slides currently in pipeline for preview.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }} className="ops-table">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border-default)' }}>
                  {['Slide / Filename', 'Scanner', 'Color Dot', 'Predicted Destination', 'Routing Reason', 'Status'].map(h => (
                    <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--text-faint)', fontWeight: 600 }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview?.items.map((item, i) => (
                  <PreviewRow key={item.slide_id ?? i} item={item} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Recorded Routing Decisions */}
      <div className="mission-card p-5">
        <div className="flex items-center justify-between mb-4">
          <SectionHeader icon={GitBranch} title="Recorded Routing Decisions" count={decisions?.total} color="var(--chart-indigo)" />
          <span style={{ fontSize: 9, color: 'var(--text-faint)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>
            Click any row to see decision chain
          </span>
        </div>

        {(decisions?.items ?? []).length === 0 ? (
          <p style={{ fontSize: 11, color: 'var(--text-faint)' }}>
            No routing decisions recorded yet. Decisions appear here as real slides complete BabelShark intake.
          </p>
        ) : (
          <>
            {/* Stats row */}
            {decisions?.stats && (
              <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: 'repeat(4, 1fr)' }}>
                {[
                  { label: 'Total decisions', value: String(decisions.stats.total ?? 0) },
                  { label: 'With override', value: String(decisions.stats.override_count ?? 0) },
                  { label: 'Unique scanners', value: String(decisions.stats.unique_scanners ?? 0) },
                  { label: 'Destinations used', value: String(decisions.stats.unique_destinations ?? 0) },
                ].map(({ label, value }) => (
                  <div key={label} style={{ padding: '8px 12px', background: 'var(--surface-inset)', borderRadius: 6, border: '1px solid var(--border-faint)' }}>
                    <div style={{ fontSize: 16, fontWeight: 800, color: 'var(--text-primary)', fontFamily: '"JetBrains Mono", monospace' }}>{value}</div>
                    <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--text-faint)', marginTop: 4 }}>{label}</div>
                  </div>
                ))}
              </div>
            )}

            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }} className="ops-table">
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border-default)' }}>
                    {['Time', 'Slide', 'Scanner', 'Mode', 'Color / Conf', 'Predicted Destination', 'Actual Destination', 'Reason', 'DR', ''].map(h => (
                      <th key={h} style={{ padding: '6px 10px', textAlign: 'left', fontSize: 9, textTransform: 'uppercase', letterSpacing: '0.14em', color: 'var(--text-faint)', fontWeight: 600 }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {decisions?.items.map((d: RoutingDecisionItem) => (
                    <tr
                      key={d.id}
                      style={{ borderBottom: '1px solid var(--border-faint)', cursor: 'pointer', transition: 'background 100ms' }}
                      onClick={() => setSelectedDecisionId(d.id)}
                      onMouseEnter={e => (e.currentTarget.style.background = 'var(--surface-hover)')}
                      onMouseLeave={e => (e.currentTarget.style.background = '')}
                    >
                      <td style={{ padding: '5px 10px', fontSize: 10, color: 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace', whiteSpace: 'nowrap' }}>{fmtDateShort(d.created_at)}</td>
                      <td style={{ padding: '5px 10px', fontSize: 10, color: 'var(--text-secondary)', fontFamily: '"JetBrains Mono", monospace', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={d.slide_id ?? undefined}>
                        {d.slide_id ?? '—'}
                      </td>
                      <td style={{ padding: '5px 10px', fontSize: 10, color: 'var(--text-muted)', fontFamily: '"JetBrains Mono", monospace' }}>{d.scanner_id ?? '—'}</td>
                      <td style={{ padding: '5px 10px', fontSize: 10, color: 'var(--text-muted)' }}>{d.mode?.replace(/_/g, ' ') ?? '—'}</td>
                      <td style={{ padding: '5px 10px' }}>
                        {d.color_dot ? (
                          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                            <span style={{ width: 7, height: 7, borderRadius: '50%', background: colorDotSwatch(d.color_dot), display: 'inline-block', flexShrink: 0 }} />
                            <span style={{ fontSize: 9, color: 'var(--text-muted)' }}>{d.color_dot}</span>
                            {d.color_dot_confidence !== null && d.color_dot_confidence !== undefined ? (
                              <span style={{ fontSize: 9, color: 'var(--text-faint)', fontFamily: '"JetBrains Mono", monospace' }}>
                                {(d.color_dot_confidence * 100).toFixed(0)}%
                              </span>
                            ) : null}
                          </span>
                        ) : <span style={{ color: 'var(--text-faint)', fontSize: 10 }}>—</span>}
                      </td>
                      <td style={{ padding: '5px 10px', fontSize: 11, color: 'var(--chart-teal)', fontFamily: '"JetBrains Mono", monospace' }}>{d.destination}</td>
                      <td style={{ padding: '5px 10px', fontSize: 10, color: 'var(--text-faint)', fontStyle: 'italic' }}>
                        {d.dry_run ? 'unchanged' : d.destination}
                      </td>
                      <td style={{ padding: '5px 10px' }}>
                        <span style={{ fontSize: 9, fontWeight: 600, letterSpacing: '0.08em', textTransform: 'uppercase', color: reasonColor(d.routing_reason), background: 'var(--surface-inset)', border: '1px solid var(--border-faint)', borderRadius: 3, padding: '1px 5px' }}>
                          {d.routing_reason.replace(/_/g, ' ')}
                        </span>
                      </td>
                      <td style={{ padding: '5px 10px' }}>
                        <span style={{ fontSize: 9, color: d.dry_run ? 'var(--chart-amber)' : 'var(--chart-rose)', fontWeight: 700, textTransform: 'uppercase' }}>
                          {d.dry_run ? 'YES' : 'LIVE'}
                        </span>
                      </td>
                      <td style={{ padding: '5px 10px' }}>
                        <Info style={{ width: 10, height: 10, color: 'var(--text-faint)' }} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
