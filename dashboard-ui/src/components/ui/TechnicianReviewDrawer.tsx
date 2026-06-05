/**
 * TechnicianReviewDrawer — Technician Review & Manual Rename
 *
 * Three tabs:
 *   Inspect Label      — full label image + extraction metadata
 *   Correct Filename   — Quick Rename OR Structured Builder (two modes)
 *   Audit Trail        — chronological change + event history
 *
 * Correct Filename tab modes:
 *   Quick Rename       — single text input, live validation, preserve existing workflow
 *   Structured Builder — field-by-field form, auto-assembled filename, same validation/API
 *
 * Both rename modes use identical backend validation (POST /validate-filename)
 * and the same safe rename API (POST /technician-rename).
 * Every action creates audit events + TechnicianChange records + requeues QC on success.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, ChevronRight, Clock, X, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import {
  fetchAuditTrail,
  fetchLabelPreview,
  patchReviewState,
  postTechnicianRename,
  postValidateFilename,
} from '../../api/watchFolders'
import type {
  AuditChangeItem,
  AuditEventItem,
  FilenameValidationResponse,
  LabelPreviewResponse,
  MonitoredFileItem,
  TechnicianRenameResponse,
} from '../../types/api'
import { fmtBytes, fmtDatetime, fmtEventType, fmtRelative } from '../../utils/formatters'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const SUPPORTED_EXTENSIONS = ['.svs', '.ndpi', '.mrxs', '.tiff', '.tif', '.scn', '.czi', '.vsi', '.bif']

// Common stains at the top; full list available via free text.
// Mirrors resources/stain_list.json (backend authoritative source).
const STAIN_LIST = [
  'H&E', 'HE', 'PAS', 'MT', 'EVG', 'Gomorri', 'Grocott', 'Ziehl', 'Giemsa',
  'AE1/3', 'AE1-3', 'CK7', 'CK5/6', 'CK5-6', 'CK14', 'CK18', 'CK19', 'CK20',
  'CD3', 'CD4', 'CD5', 'CD8', 'CD10', 'CD15', 'CD19', 'CD20', 'CD21', 'CD22',
  'CD23', 'CD30', 'CD31', 'CD34', 'CD38', 'CD43', 'CD45', 'CD56', 'CD57', 'CD61',
  'CD68', 'CD79a', 'CD99', 'CD117', 'CD123', 'CD138', 'CD163', 'CD207',
  'ER', 'PR', 'HER2', 'KI-67', 'P53', 'P63', 'p40', 'p16',
  'BCL2', 'BCL6', 'SOX-11', 'SOX11', 'SOX-10', 'GATA3', 'PAX5', 'PAX8',
  'S100', 'GFAP', 'NSE', 'SYN', 'CHRA', 'VIM', 'Desmin',
  'TTF1', 'NKX3.1', 'PSMA', 'PSA', 'AFP', 'CEA', 'CA125', 'CA19-9',
  'ALK1', 'BRAF', 'EGFR', 'PD-1', 'PD-L1',
  'MLH1', 'MSH2', 'MSH6', 'PMS2',
  'ERG', 'EMA', 'MUC2', 'MUC4', 'SALL4', 'STAT6',
  'TdT', 'MPO', 'MUM1', 'LEF1',
  'IgG4', 'Kappa', 'Lambda',
  'MG', 'PAP', 'MGG', 'FE', 'Afog', 'DIA-PAS', 'NASDCL',
  'CMV', 'SV40', 'HSV1', 'HSV2', 'HHV-8', 'HHV8', 'LMP',
  'ASMA', 'Aktin',
]

// ---------------------------------------------------------------------------
// Review state helpers
// ---------------------------------------------------------------------------

const REVIEW_STATE_LABELS: Record<string, string> = {
  detected:      'Detected',
  unlinked:      'Unlinked',
  linked:        'Linked',
  investigating: 'Investigating',
  corrected:     'Corrected',
  requeued:      'Requeued',
  reviewed:      'Reviewed',
  dismissed:     'Dismissed',
}

const REVIEW_STATE_COLOR: Record<string, string> = {
  detected:      'var(--chart-amber)',
  unlinked:      'var(--chart-amber)',
  investigating: 'var(--accent)',
  corrected:     'var(--chart-teal)',
  requeued:      'var(--chart-teal)',
  reviewed:      'var(--text-muted)',
  dismissed:     'var(--text-faint)',
  linked:        'var(--accent)',
}

// ---------------------------------------------------------------------------
// Sub-component: compact evidence strip (shown in Correct Filename tab)
// ---------------------------------------------------------------------------

function CompactEvidenceStrip({ fileId }: { fileId: number }) {
  const BASE = '/dashboard/api'
  const labelImageUrl = `${BASE}/recovery/files/${fileId}/label-image`
  const [imageError, setImageError] = useState(false)

  const { data } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn: () => fetchLabelPreview(fileId),
    staleTime: 60_000,
  })

  const facts: { label: string; value: string; accent?: string }[] = [
    data?.case_id         ? { label: 'Case',     value: data.case_id }                             : null,
    (data?.stain_matched ?? data?.stain_type)
                          ? { label: 'Stain',    value: data!.stain_matched ?? data!.stain_type! }  : null,
    data?.scanner_id      ? { label: 'Scanner',  value: data.scanner_id }                          : null,
    data?.suggested_filename
                          ? { label: 'Suggested', value: data.suggested_filename, accent: 'var(--accent)' } : null,
  ].filter(Boolean) as { label: string; value: string; accent?: string }[]

  // Render nothing if there's no label data at all
  if (!data && imageError) return null
  if (!data?.available && facts.length === 0 && imageError) return null

  return (
    <div
      className="rounded flex gap-2.5 items-start"
      style={{
        background: 'var(--surface-inset)',
        border: '1px solid var(--border-faint)',
        padding: '7px 10px',
      }}
    >
      {!imageError && (
        <img
          src={labelImageUrl}
          alt="Label"
          onError={() => setImageError(true)}
          style={{
            height: 46,
            width: 'auto',
            borderRadius: 3,
            flexShrink: 0,
            border: '1px solid var(--border-faint)',
            opacity: 0.88,
          }}
        />
      )}
      <div className="flex-1 min-w-0">
        <p
          className="text-[8px] uppercase tracking-widest mb-1.5"
          style={{ color: 'var(--text-faint)' }}
        >
          Label Evidence
        </p>
        {facts.length > 0 ? (
          <div className="flex flex-wrap gap-x-3 gap-y-0.5">
            {facts.map(({ label, value, accent }) => (
              <span key={label} className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
                {label}:{' '}
                <span
                  className="font-mono"
                  style={{ color: accent ?? 'var(--text-secondary)' }}
                >
                  {value}
                </span>
              </span>
            ))}
          </div>
        ) : (
          <p className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
            No extraction data available for this file.
          </p>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: label metadata panel (full — Inspect tab)
// ---------------------------------------------------------------------------

function LabelMetadataPanel({ fileId }: { fileId: number }) {
  const BASE = '/dashboard/api'
  const { data, isPending } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn: () => fetchLabelPreview(fileId),
    staleTime: 60_000,
  })

  const labelImageUrl = `${BASE}/recovery/files/${fileId}/label-image`
  const [imageError, setImageError] = useState(false)

  if (isPending) {
    return (
      <div
        className="rounded-lg px-4 py-3 text-[10px] font-mono animate-pulse"
        style={{ background: 'var(--surface-inset)', color: 'var(--text-faint)' }}
      >
        Loading extraction data…
      </div>
    )
  }

  const rows: Array<{ label: string; value: string | null | undefined; accent?: string }> = [
    { label: 'Slide ID',          value: data?.slide_id },
    { label: 'Case ID',           value: data?.case_id },
    { label: 'Scanner',           value: data?.scanner_id },
    { label: 'Vendor',            value: data?.scanner_vendor },
    { label: 'Stain (extracted)', value: data?.stain_matched ?? data?.stain_type },
    { label: 'Stain OCR raw',     value: data?.stain_ocr_raw },
    { label: 'DataMatrix',        value: data?.datamatrix_raw },
    { label: 'DM decode',         value: data?.datamatrix_decode_status },
    { label: 'DM error',          value: data?.datamatrix_error, accent: 'var(--chart-rose)' },
    { label: 'ROI case',          value: data?.roi_case_number },
    { label: 'ROI lab',           value: data?.roi_lab_id },
    { label: 'ROI stain',         value: data?.roi_stain },
    { label: 'Routed as',         value: data?.routing_type, accent: data?.routing_type === 'failed' ? 'var(--chart-rose)' : 'var(--chart-amber)' },
    { label: 'Routing reason',    value: data?.routing_reason },
    { label: 'Suggested name',    value: data?.suggested_filename, accent: 'var(--accent)' },
  ].filter(r => r.value)

  return (
    <div
      className="rounded-lg overflow-hidden"
      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
    >
      {!imageError && (
        <div className="px-4 pt-3 pb-2" style={{ borderBottom: '1px solid var(--border-faint)' }}>
          <p className="text-[9px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-faint)' }}>
            Label Image
          </p>
          <img
            src={labelImageUrl}
            alt="Label preview"
            onError={() => setImageError(true)}
            className="rounded max-h-24 w-auto"
            style={{ border: '1px solid var(--border-faint)', opacity: 0.9 }}
          />
        </div>
      )}
      <div className="px-4 py-3 space-y-1">
        <p className="text-[9px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-faint)' }}>
          Extraction Data
        </p>
        {rows.length === 0 ? (
          <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
            {data?.unavailable_reason
              ? `No data — ${data.unavailable_reason.replace(/_/g, ' ')}`
              : 'No extraction data found for this file.'}
          </p>
        ) : (
          rows.map(({ label, value, accent }) => (
            <div key={label} className="flex gap-2 text-[10px]">
              <span className="w-28 flex-shrink-0 font-medium" style={{ color: 'var(--text-faint)' }}>
                {label}
              </span>
              <span className="font-mono break-all" style={{ color: accent ?? 'var(--text-secondary)' }}>
                {value}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: live filename validation panel
// ---------------------------------------------------------------------------

function ValidationPanel({
  filename,
  serverResult,
  isPending: validating,
}: {
  filename: string
  serverResult: FilenameValidationResponse | null
  isPending: boolean
}) {
  if (!filename.trim()) return null

  if (validating) {
    return (
      <div className="text-[10px] animate-pulse" style={{ color: 'var(--text-faint)' }}>
        Validating…
      </div>
    )
  }

  if (!serverResult) return null

  const cls = serverResult.classification
  const color =
    cls === 'valid'           ? 'var(--chart-teal)'  :
    cls === 'partially_valid' ? 'var(--chart-amber)' :
                                'var(--chart-rose)'
  const Icon =
    cls === 'valid'           ? CheckCircle :
    cls === 'partially_valid' ? Clock       :
                                XCircle

  return (
    <div
      className="rounded-lg px-3 py-2 space-y-1.5"
      style={{ background: 'var(--surface-inset)', border: `1px solid ${color}22` }}
    >
      <div className="flex items-center gap-1.5">
        <Icon className="h-3 w-3 flex-shrink-0" style={{ color }} aria-hidden />
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color }}>
          {cls === 'valid' ? 'Valid' : cls === 'partially_valid' ? 'Partially valid' : 'Invalid'}
        </span>
      </div>

      {serverResult.components && (
        <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
          {[
            { label: 'Case ID',   value: serverResult.components.case_id },
            { label: 'Pot',       value: serverResult.components.pot },
            { label: 'Block',     value: serverResult.components.block },
            { label: 'Section',   value: serverResult.components.section },
            { label: 'Stain',     value: serverResult.components.stain },
            { label: 'Timestamp', value: serverResult.components.timestamp ?? '(missing — will extract)' },
          ].map(({ label, value }) => value ? (
            <div key={label} className="flex gap-1 text-[9px]">
              <span style={{ color: 'var(--text-faint)' }}>{label}</span>
              <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{value}</span>
            </div>
          ) : null)}
        </div>
      )}

      {serverResult.errors.map(e => (
        <p key={e.code} className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>{e.message}</p>
      ))}
      {serverResult.warnings.map(w => (
        <p key={w.code} className="text-[10px]" style={{ color: 'var(--chart-amber)' }}>{w.message}</p>
      ))}
      {serverResult.suggested_correction && cls !== 'valid' && (
        <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
          Suggestion:{' '}
          <span className="font-mono" style={{ color: 'var(--accent)' }}>
            {serverResult.suggested_correction}
          </span>
        </p>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: structured rename builder
// ---------------------------------------------------------------------------

interface BuilderState {
  prefix: string
  year: string
  caseNum: string
  pot: string
  block: string
  section: string
  stain: string
  extension: string
  addTimestamp: boolean
  tsDate: string
  tsTime: string
}

function assembleName(s: BuilderState): string {
  const paddedCase = s.caseNum.replace(/\D/g, '').padStart(6, '0')
  const base = `${s.prefix}${s.year}${paddedCase}${s.pot}-${s.block}-${s.section}-${s.stain}`
  if (!base.includes('-')) return `${base}${s.extension}`
  if (s.addTimestamp && s.tsDate && s.tsTime) {
    const parts = s.tsTime.split(':')
    const hh = (parts[0] ?? '00').padStart(2, '0')
    const mm = (parts[1] ?? '00').padStart(2, '0')
    const ss = (parts[2] ?? '00').padStart(2, '0')
    return `${base}_UTC${s.tsDate}T${hh}_${mm}_${ss}Z${s.extension}`
  }
  return `${base}${s.extension}`
}

function parseCaseId(caseId: string | null | undefined): { prefix: string; year: string; caseNum: string } | null {
  if (!caseId) return null
  const m = /^([A-Z])(\d{4})(\d{6})$/.exec(caseId)
  if (!m) return null
  return { prefix: m[1], year: m[2], caseNum: m[3] }
}

function todayDate(): string {
  return new Date().toISOString().slice(0, 10)
}

function StructuredBuilderForm({
  file,
  onFilenameChange,
}: {
  file: MonitoredFileItem
  onFilenameChange: (filename: string) => void
}) {
  const { data: lp } = useQuery<LabelPreviewResponse>({
    queryKey: ['labelPreview', file.file_id],
    queryFn: () => fetchLabelPreview(file.file_id),
    staleTime: 60_000,
  })

  // Pre-populate from label preview or file metadata
  const parsed = parseCaseId(lp?.case_id ?? file.case_id)

  const [state, setState] = useState<BuilderState>(() => ({
    prefix:       parsed?.prefix   ?? 'N',
    year:         parsed?.year     ?? String(new Date().getFullYear()),
    caseNum:      parsed?.caseNum  ?? '',
    pot:          'SA',
    block:        '1',
    section:      '1',
    stain:        lp?.stain_matched ?? lp?.stain_type ?? '',
    extension:    file.extension   ?? (file.filename.includes('.') ? `.${file.filename.split('.').pop()}` : '.svs'),
    addTimestamp: false,
    tsDate:       todayDate(),
    tsTime:       '00:00:00',
  }))

  // Re-populate stain when label preview loads
  const hasPrePopulated = useRef(false)
  useEffect(() => {
    if (hasPrePopulated.current) return
    if (!lp) return
    const filledParsed = parseCaseId(lp.case_id ?? file.case_id)
    setState(prev => ({
      ...prev,
      ...(filledParsed ? { prefix: filledParsed.prefix, year: filledParsed.year, caseNum: filledParsed.caseNum } : {}),
      stain: lp.stain_matched ?? lp.stain_type ?? prev.stain,
    }))
    hasPrePopulated.current = true
  }, [lp, file.case_id])

  const set = (key: keyof BuilderState, value: string | boolean) =>
    setState(prev => ({ ...prev, [key]: value }))

  // Emit assembled filename whenever state changes
  useEffect(() => {
    const assembled = assembleName(state)
    // Only emit if the assembled name looks plausible (has at least the separator)
    if (assembled && assembled !== state.extension) {
      onFilenameChange(assembled)
    }
  }, [state]) // eslint-disable-line react-hooks/exhaustive-deps

  const paddedPreview = state.caseNum.replace(/\D/g, '').padStart(6, '0')
  const caseIdPreview = `${state.prefix}${state.year}${paddedPreview}`
  const assembled = assembleName(state)

  const inputCls = "rounded px-2.5 py-1.5 text-[11px] font-mono w-full outline-none"
  const inputStyle = {
    background: 'var(--surface-inset)',
    border: '1px solid var(--border-default)',
    color: 'var(--text-primary)',
  } as const
  const labelCls = "text-[9px] uppercase tracking-wider block mb-1"
  const labelStyle = { color: 'var(--text-faint)' } as const

  return (
    <div className="space-y-3">
      {/* Case ID fields — 3-column row */}
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className={labelCls} style={labelStyle}>Prefix</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.prefix}
            maxLength={2}
            placeholder="N"
            onChange={e => set('prefix', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label className={labelCls} style={labelStyle}>Year</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.year}
            maxLength={4}
            placeholder="2024"
            onChange={e => set('year', e.target.value.replace(/\D/g, ''))}
          />
        </div>
        <div>
          <label className={labelCls} style={labelStyle}>Case № (auto-pads)</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.caseNum}
            maxLength={6}
            placeholder="002863"
            onChange={e => set('caseNum', e.target.value.replace(/\D/g, ''))}
          />
        </div>
      </div>

      {/* Case ID preview chip */}
      {caseIdPreview.length > 1 && (
        <div className="flex items-center gap-1.5">
          <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>Case ID →</span>
          <span
            className="font-mono text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: 'var(--accent-faint)', color: 'var(--accent)' }}
          >
            {caseIdPreview}
          </span>
        </div>
      )}

      {/* Pot / Block / Section — 3-column */}
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className={labelCls} style={labelStyle}>Pot</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.pot}
            maxLength={4}
            placeholder="SA"
            onChange={e => set('pot', e.target.value.toUpperCase())}
          />
        </div>
        <div>
          <label className={labelCls} style={labelStyle}>Block</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.block}
            maxLength={3}
            placeholder="1"
            onChange={e => set('block', e.target.value.replace(/\D/g, ''))}
          />
        </div>
        <div>
          <label className={labelCls} style={labelStyle}>Section / Slide</label>
          <input
            className={inputCls}
            style={inputStyle}
            value={state.section}
            maxLength={3}
            placeholder="1"
            onChange={e => set('section', e.target.value.replace(/\D/g, ''))}
          />
        </div>
      </div>

      {/* Stain combobox + Extension — 2-column */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={labelCls} style={labelStyle}>Stain</label>
          <input
            className={inputCls}
            style={inputStyle}
            list="pathoryx-stain-list"
            value={state.stain}
            placeholder="H&E"
            onChange={e => set('stain', e.target.value)}
          />
          <datalist id="pathoryx-stain-list">
            {STAIN_LIST.map(s => <option key={s} value={s} />)}
          </datalist>
        </div>
        <div>
          <label className={labelCls} style={labelStyle}>Extension</label>
          <select
            className={inputCls}
            style={{ ...inputStyle, cursor: 'pointer' }}
            value={state.extension}
            onChange={e => set('extension', e.target.value)}
          >
            {SUPPORTED_EXTENSIONS.map(ext => (
              <option key={ext} value={ext}>{ext}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Timestamp toggle */}
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={() => set('addTimestamp', !state.addTimestamp)}
          className="flex items-center gap-2 text-[10px]"
          style={{ color: 'var(--text-secondary)' }}
        >
          <span
            className="inline-flex items-center justify-center rounded-full flex-shrink-0"
            style={{
              width: 14, height: 14,
              border: `2px solid ${state.addTimestamp ? 'var(--accent)' : 'var(--border-default)'}`,
              background: state.addTimestamp ? 'var(--accent)' : 'transparent',
            }}
          />
          Include UTC scan timestamp
        </button>
      </div>

      {/* Timestamp fields — shown only when toggled */}
      {state.addTimestamp && (
        <div className="grid grid-cols-2 gap-2 pl-4">
          <div>
            <label className={labelCls} style={labelStyle}>Date (yyyy-MM-dd)</label>
            <input
              type="date"
              className={inputCls}
              style={inputStyle}
              value={state.tsDate}
              onChange={e => set('tsDate', e.target.value)}
            />
          </div>
          <div>
            <label className={labelCls} style={labelStyle}>Time (HH:mm:ss)</label>
            <input
              type="time"
              step="1"
              className={inputCls}
              style={inputStyle}
              value={state.tsTime}
              onChange={e => set('tsTime', e.target.value)}
            />
          </div>
        </div>
      )}

      {/* Assembled preview */}
      <div
        className="rounded px-3 py-2"
        style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
      >
        <p className="text-[9px] uppercase tracking-wider mb-1" style={{ color: 'var(--text-faint)' }}>
          Assembled filename
        </p>
        <p
          className="font-mono text-[11px] break-all"
          style={{ color: assembled === state.extension ? 'var(--text-faint)' : 'var(--text-primary)' }}
        >
          {assembled || <span style={{ color: 'var(--text-faint)' }}>Fill in the fields above</span>}
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: audit trail timeline
// ---------------------------------------------------------------------------

const CHANGE_TYPE_LABEL: Record<string, string> = {
  rename:          'renamed',
  replace:         'replaced',
  move:            'moved',
  new_file:        'first seen',
  removed:         'removed',
  checksum_change: 'content changed',
  size_change:     'size changed',
  metadata_update: 'metadata updated',
}

function AuditTimeline({ fileId }: { fileId: number }) {
  const { data, isPending } = useQuery({
    queryKey: ['auditTrail', fileId],
    queryFn:  () => fetchAuditTrail(fileId),
  })

  if (isPending) {
    return (
      <p className="text-[10px] animate-pulse" style={{ color: 'var(--text-faint)' }}>
        Loading history…
      </p>
    )
  }

  const changes: AuditChangeItem[] = data?.changes ?? []
  const events:  AuditEventItem[]  = data?.events  ?? []

  type TimelineItem =
    | { kind: 'change'; ts: string | null; item: AuditChangeItem }
    | { kind: 'event';  ts: string | null; item: AuditEventItem }

  const items: TimelineItem[] = [
    ...changes.map(c => ({ kind: 'change' as const, ts: c.detected_at, item: c })),
    ...events.map(e => ({ kind: 'event' as const,  ts: e.occurred_at,  item: e })),
  ].sort((a, b) => (a.ts ?? '').localeCompare(b.ts ?? ''))

  if (items.length === 0) {
    return (
      <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
        No audit records found for this file.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {items.map(entry => {
        if (entry.kind === 'change') {
          const c = entry.item as AuditChangeItem
          const src = c.inferred_action === 'dashboard_correction' ? 'dashboard' : 'filesystem'
          return (
            <div key={`c-${c.change_id}`} className="flex gap-2 text-[10px]">
              <span
                className="text-[9px] font-mono tabular mt-0.5 flex-shrink-0 w-16 text-right"
                style={{ color: 'var(--text-faint)' }}
                title={fmtDatetime(c.detected_at)}
              >
                {fmtRelative(c.detected_at)}
              </span>
              <div className="flex-1 min-w-0">
                <span style={{ color: 'var(--text-secondary)' }}>
                  {CHANGE_TYPE_LABEL[c.change_type] ?? c.change_type.replace(/_/g, ' ')}
                </span>
                {c.new_filename && c.old_filename && c.new_filename !== c.old_filename && (
                  <span style={{ color: 'var(--text-faint)' }}>
                    {' '}{c.old_filename} →{' '}
                    <span className="font-mono" style={{ color: 'var(--accent)' }}>{c.new_filename}</span>
                  </span>
                )}
                <span className="ml-1" style={{ color: 'var(--text-faint)' }}>
                  via {src}
                </span>
                {c.review_status && (
                  <span
                    className="ml-1.5 px-1 py-0.5 rounded text-[9px] font-medium"
                    style={{
                      color:      REVIEW_STATE_COLOR[c.review_status] ?? 'var(--text-faint)',
                      background: 'var(--accent-faint)',
                    }}
                  >
                    {REVIEW_STATE_LABELS[c.review_status] ?? c.review_status}
                  </span>
                )}
                {c.technician_notes && (
                  <p className="mt-0.5 italic" style={{ color: 'var(--text-faint)' }}>
                    "{c.technician_notes}"
                  </p>
                )}
              </div>
            </div>
          )
        } else {
          const e = entry.item as AuditEventItem
          const isReview = e.event_type.startsWith('dashboard.')
          return (
            <div key={`e-${e.event_id}`} className="flex gap-2 text-[10px]">
              <span
                className="text-[9px] font-mono tabular mt-0.5 flex-shrink-0 w-16 text-right"
                style={{ color: 'var(--text-faint)' }}
                title={fmtDatetime(e.occurred_at)}
              >
                {fmtRelative(e.occurred_at)}
              </span>
              <span
                className={isReview ? 'font-medium' : ''}
                style={{ color: isReview ? 'var(--accent)' : 'var(--text-faint)' }}
              >
                {fmtEventType(e.event_type)}
              </span>
            </div>
          )
        }
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Sub-component: confirm dialog
// ---------------------------------------------------------------------------

function ConfirmRenameDialog({
  filename,
  onConfirm,
  onCancel,
  isPending,
}: {
  filename: string
  onConfirm: () => void
  onCancel: () => void
  isPending: boolean
}) {
  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center"
      style={{ background: 'rgba(0,0,0,0.60)' }}
    >
      <div
        className="rounded-xl p-5 w-[440px] max-w-[94vw]"
        style={{ background: 'var(--surface-1)', border: '1px solid var(--border-default)' }}
      >
        <p
          className="text-[9px] uppercase tracking-[0.15em] mb-2"
          style={{ color: 'var(--text-faint)' }}
        >
          Confirm Recovery Action
        </p>
        <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
          Apply filename correction
        </p>
        <p className="text-[11px] leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
          This will rename and move the file, update the artifact record, and requeue QC.
          The original filename is preserved in the audit history.
        </p>
        <div
          className="rounded px-3 py-2 mb-4 font-mono text-[10px] break-all"
          style={{ background: 'var(--surface-inset)', color: 'var(--accent)', border: '1px solid var(--border-faint)' }}
        >
          {filename}
        </div>
        <div className="flex gap-3 justify-end">
          <button
            type="button"
            onClick={onCancel}
            disabled={isPending}
            className="px-4 py-1.5 rounded text-[11px]"
            style={{ color: 'var(--text-muted)', border: '1px solid var(--border-default)' }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={isPending}
            className="px-4 py-1.5 rounded text-[11px] font-semibold"
            style={{
              color:      'var(--surface-1)',
              background: 'var(--accent)',
              opacity:    isPending ? 0.6 : 1,
            }}
          >
            {isPending ? 'Applying…' : 'Apply Recovery'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main drawer
// ---------------------------------------------------------------------------

type DrawerTab   = 'inspect' | 'rename' | 'history'
type RenameMode  = 'quick' | 'builder'

interface Props {
  file: MonitoredFileItem
  onClose: () => void
}

export function TechnicianReviewDrawer({ file, onClose }: Props) {
  const queryClient = useQueryClient()

  // ── Core rename state (shared between both modes) ──────────────────────
  const [proposedFilename, setProposedFilename] = useState(file.filename)
  const [note, setNote]                         = useState('')
  const [confirming, setConfirming]             = useState(false)
  const [renameResult, setRenameResult]         = useState<TechnicianRenameResponse | null>(null)

  // ── Tab and mode state ─────────────────────────────────────────────────
  const [tab, setTab]             = useState<DrawerTab>('inspect')
  const [renameMode, setRenameMode] = useState<RenameMode>('quick')

  const inputRef = useRef<HTMLInputElement>(null)

  // ── Server-side validation (debounced 250 ms) ──────────────────────────
  const [validationResult, setValidationResult] = useState<FilenameValidationResponse | null>(null)
  const [validating, setValidating]             = useState(false)

  useEffect(() => {
    const proposed = proposedFilename.trim()
    if (!proposed) { setValidationResult(null); return }
    const timer = setTimeout(async () => {
      setValidating(true)
      try {
        const result = await postValidateFilename(proposed)
        setValidationResult(result)
      } catch {
        setValidationResult(null)
      } finally {
        setValidating(false)
      }
    }, 250)
    return () => clearTimeout(timer)
  }, [proposedFilename])

  // ── Rename mutation ─────────────────────────────────────────────────────
  const renameMutation = useMutation({
    mutationFn: () =>
      postTechnicianRename(file.file_id, {
        proposed_filename: proposedFilename.trim(),
        technician_note:   note.trim() || undefined,
        confirm: true,
      }),
    onSuccess: data => {
      setRenameResult(data)
      setConfirming(false)
      queryClient.invalidateQueries({ queryKey: ['monitoredFiles'] })
      queryClient.invalidateQueries({ queryKey: ['watchFolders'] })
      queryClient.invalidateQueries({ queryKey: ['recovery'] })
      queryClient.invalidateQueries({ queryKey: ['failures'] })
      queryClient.invalidateQueries({ queryKey: ['auditTrail', file.file_id] })
    },
    onError: () => setConfirming(false),
  })

  // ── Review state mutation ───────────────────────────────────────────────
  const reviewMutation = useMutation({
    mutationFn: ({ status, note: n }: { status: string; note?: string }) => {
      if (!file.change_id) throw new Error('No TechnicianChange linked to this file')
      return patchReviewState(file.change_id, { review_status: status, technician_note: n })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['monitoredFiles'] })
      queryClient.invalidateQueries({ queryKey: ['auditTrail', file.file_id] })
    },
  })

  // ── Derived ─────────────────────────────────────────────────────────────
  const canRename =
    validationResult !== null &&
    validationResult.classification !== 'invalid' &&
    proposedFilename.trim() !== ''

  const folderDisplay: Record<string, string> = {
    failed:        'Failed',
    suspicious:    'Suspicious',
    manual_review: 'Manual Review',
  }

  const workflowSource =
    file.inferred_action === 'dashboard_correction' ? 'Dashboard correction'         :
    file.change_type === 'rename'                   ? 'Manual folder rename'          :
    file.change_type === 'new_file'                 ? 'New file detected'             :
    file.change_type                                ? `Detected: ${file.change_type.replace(/_/g, ' ')}` :
                                                      'Awaiting technician'

  const reviewStatus = file.review_status ?? null

  // ── Label preview (fetched once; cached for both Inspect and Rename tabs) ─
  const { data: labelPreview } = useQuery<LabelPreviewResponse>({
    queryKey: ['labelPreview', file.file_id],
    queryFn:  () => fetchLabelPreview(file.file_id),
    staleTime: 60_000,
  })

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.35)' }}
        onClick={onClose}
        aria-hidden
      />

      {/* Drawer */}
      <div
        className="fixed top-0 right-0 h-full z-50 flex flex-col"
        style={{
          width:      'min(540px, 96vw)',
          background: 'var(--surface-1)',
          borderLeft: '1px solid var(--border-default)',
          boxShadow:  '-8px 0 40px rgba(0,0,0,0.45)',
        }}
      >
        {/* ── Header ── */}
        <div
          className="flex items-start gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
          <div className="flex-1 min-w-0">
            <p
              className="text-[9px] font-semibold uppercase tracking-[0.15em]"
              style={{ color: 'var(--text-faint)' }}
            >
              Technician Review — {folderDisplay[file.folder_label] ?? file.folder_label}
            </p>
            <p
              className="text-xs font-medium truncate mt-0.5"
              style={{ color: 'var(--text-primary)' }}
              title={file.filename}
            >
              {file.filename}
            </p>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 mt-1">
              {file.case_id && (
                <span className="text-[9px] font-mono" style={{ color: 'var(--accent)' }}>
                  {file.case_id}
                </span>
              )}
              {file.file_size != null && (
                <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
                  {fmtBytes(file.file_size)}
                </span>
              )}
              <span className="text-[9px]" style={{ color: 'var(--text-faint)' }}>
                {workflowSource}
              </span>
              {reviewStatus && (
                <span
                  className="text-[9px] font-medium px-1.5 py-0.5 rounded"
                  style={{
                    color:      REVIEW_STATE_COLOR[reviewStatus] ?? 'var(--text-faint)',
                    background: 'var(--accent-faint)',
                  }}
                >
                  {REVIEW_STATE_LABELS[reviewStatus] ?? reviewStatus}
                </span>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="flex-shrink-0 p-1 rounded mt-0.5"
            style={{ color: 'var(--text-faint)' }}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* ── Review state actions ── */}
        {file.change_id && !renameResult && (
          <div
            className="flex items-center gap-2 px-5 py-2.5 flex-shrink-0"
            style={{ borderBottom: '1px solid var(--border-faint)', background: 'var(--accent-faint)' }}
          >
            <span className="text-[9px] uppercase tracking-wider mr-1" style={{ color: 'var(--text-faint)' }}>
              Mark as
            </span>
            {(
              [
                { status: 'investigating', label: 'Investigating' },
                { status: 'dismissed',     label: 'Dismiss' },
              ] as const
            ).map(({ status, label }) => {
              const current = file.review_status
              const isActive = current === status
              const blocked =
                reviewMutation.isPending ||
                isActive ||
                (status === 'dismissed'     && current === 'dismissed') ||
                (status === 'investigating' && current === 'investigating')
              return (
                <button
                  key={status}
                  type="button"
                  disabled={blocked}
                  onClick={() => reviewMutation.mutate({ status, note: undefined })}
                  className="px-2.5 py-0.5 rounded text-[10px] font-medium"
                  style={{
                    color:      isActive ? 'var(--surface-1)' : REVIEW_STATE_COLOR[status],
                    background: isActive ? REVIEW_STATE_COLOR[status] : 'transparent',
                    border:     `1px solid ${REVIEW_STATE_COLOR[status]}44`,
                    opacity:    blocked ? 0.4 : 1,
                    cursor:     blocked ? 'not-allowed' : 'pointer',
                  }}
                >
                  {label}
                </button>
              )
            })}
            {reviewMutation.isError && (
              <span className="text-[9px] ml-1" style={{ color: 'var(--chart-rose)' }}>
                {(reviewMutation.error as Error)?.message ?? 'Update failed'}
              </span>
            )}
          </div>
        )}

        {/* ── Tabs ── */}
        <div
          className="flex gap-0 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
          {(
            [
              { id: 'inspect'  as const, label: 'Inspect Label' },
              { id: 'rename'   as const, label: 'Correct Filename' },
              { id: 'history'  as const, label: 'Audit Trail' },
            ]
          ).map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => { setTab(id); if (id === 'rename') setTimeout(() => inputRef.current?.focus(), 50) }}
              className="px-4 py-2.5 text-[10px] font-medium tracking-wide"
              style={{
                color:        tab === id ? 'var(--accent)'       : 'var(--text-muted)',
                borderBottom: tab === id ? '2px solid var(--accent)' : '2px solid transparent',
                background:   'transparent',
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {/* ── Body ── */}
        <div className="flex-1 overflow-y-auto px-5 py-4">

          {/* ── INSPECT TAB ── */}
          {tab === 'inspect' && (
            <div className="space-y-4">
              {file.recovery_reason && (
                <div
                  className="rounded px-3 py-2 flex items-start gap-2 text-[10px]"
                  style={{ background: 'rgba(217,119,6,0.06)', border: '1px solid rgba(217,119,6,0.20)' }}
                >
                  <span className="font-semibold flex-shrink-0" style={{ color: 'var(--chart-amber)' }}>
                    Failure reason
                  </span>
                  <span style={{ color: 'var(--text-secondary)' }}>
                    {file.recovery_reason.replace(/_/g, ' ')}
                  </span>
                </div>
              )}
              <LabelMetadataPanel fileId={file.file_id} />
            </div>
          )}

          {/* ── CORRECT FILENAME TAB ── */}
          {tab === 'rename' && (
            <div className="space-y-4">
              {renameResult ? (
                /* ── Outcome panel ── */
                <div
                  className="rounded-lg px-4 py-3 space-y-2"
                  style={{
                    background: renameResult.outcome === 'auto_recovered'
                      ? 'rgba(52,211,153,0.06)' : 'rgba(217,119,6,0.06)',
                    border: `1px solid ${renameResult.outcome === 'auto_recovered'
                      ? 'rgba(52,211,153,0.20)' : 'rgba(217,119,6,0.20)'}`,
                  }}
                >
                  <p
                    className="text-xs font-semibold"
                    style={{ color: renameResult.outcome === 'auto_recovered' ? 'var(--chart-teal)' : 'var(--chart-amber)' }}
                  >
                    {renameResult.outcome === 'auto_recovered'
                      ? 'File recovered — QC requeued'
                      : renameResult.outcome === 'manual_review_required'
                      ? 'Manual review still required'
                      : renameResult.outcome}
                  </p>
                  {renameResult.reason && (
                    <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                      {renameResult.reason.replace(/_/g, ' ')}
                    </p>
                  )}
                  {renameResult.destination_path && (
                    <p className="text-[10px] font-mono break-all" style={{ color: 'var(--text-muted)' }}>
                      → {renameResult.destination_path}
                    </p>
                  )}
                  {renameResult.validation_error && (
                    <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>
                      {renameResult.validation_error}
                    </p>
                  )}
                </div>
              ) : (
                <>
                  {/* ── Compact evidence strip ── */}
                  <CompactEvidenceStrip fileId={file.file_id} />

                  {/* ── Mode toggle ── */}
                  <div
                    className="flex rounded-md overflow-hidden flex-shrink-0 self-start"
                    style={{ border: '1px solid var(--border-default)' }}
                  >
                    {(
                      [
                        { id: 'quick'   as const, label: 'Quick Rename' },
                        { id: 'builder' as const, label: 'Structured Builder' },
                      ]
                    ).map(({ id, label }) => (
                      <button
                        key={id}
                        type="button"
                        onClick={() => setRenameMode(id)}
                        className="px-3 py-1.5 text-[10px] font-medium"
                        style={{
                          background: renameMode === id ? 'var(--accent)' : 'transparent',
                          color:      renameMode === id ? 'var(--surface-1)' : 'var(--text-muted)',
                          borderRight: id === 'quick' ? '1px solid var(--border-default)' : 'none',
                        }}
                      >
                        {label}
                      </button>
                    ))}
                  </div>

                  {/* ── QUICK RENAME (preserved exactly) ── */}
                  {renameMode === 'quick' && (
                    <div className="space-y-3">
                      <div>
                        <label
                          htmlFor="proposed-filename"
                          className="block text-[9px] uppercase tracking-wider mb-1.5"
                          style={{ color: 'var(--text-faint)' }}
                        >
                          Proposed filename
                        </label>
                        <input
                          id="proposed-filename"
                          ref={inputRef}
                          type="text"
                          value={proposedFilename}
                          onChange={e => setProposedFilename(e.target.value)}
                          spellCheck={false}
                          className="w-full rounded px-3 py-2 text-[11px] font-mono"
                          style={{
                            background: 'var(--surface-inset)',
                            border:     `1px solid ${validationResult?.classification === 'invalid' ? 'rgba(225,29,72,0.40)' : 'var(--border-default)'}`,
                            color:      'var(--text-primary)',
                            outline:    'none',
                          }}
                          placeholder="N2024002863SA-1-1-H&E.svs"
                        />
                        {/* Use suggested filename shortcut */}
                        {labelPreview?.suggested_filename &&
                          labelPreview.suggested_filename !== proposedFilename && (
                          <button
                            type="button"
                            onClick={() => setProposedFilename(labelPreview.suggested_filename!)}
                            className="mt-1 text-[9px]"
                            style={{ color: 'var(--accent)' }}
                          >
                            ↑ Use suggested: {labelPreview.suggested_filename}
                          </button>
                        )}
                      </div>
                    </div>
                  )}

                  {/* ── STRUCTURED BUILDER (new) ── */}
                  {renameMode === 'builder' && (
                    <StructuredBuilderForm
                      file={file}
                      onFilenameChange={setProposedFilename}
                    />
                  )}

                  {/* ── Shared: validation feedback ── */}
                  <ValidationPanel
                    filename={proposedFilename}
                    serverResult={validationResult}
                    isPending={validating}
                  />

                  {/* ── Shared: destination preview ── */}
                  {canRename && validationResult?.components?.case_id && (
                    <div
                      className="rounded px-3 py-2 text-[10px] font-mono"
                      style={{ background: 'var(--accent-faint)', border: '1px solid var(--border-default)' }}
                    >
                      <span style={{ color: 'var(--text-faint)' }}>Destination → </span>
                      <span style={{ color: 'var(--accent)' }}>
                        final/{validationResult.components.case_id}/{proposedFilename.trim()}
                      </span>
                    </div>
                  )}

                  {/* ── Shared: technician note ── */}
                  <div>
                    <label
                      htmlFor="tech-note"
                      className="block text-[9px] uppercase tracking-wider mb-1"
                      style={{ color: 'var(--text-faint)' }}
                    >
                      Technician note (optional)
                    </label>
                    <textarea
                      id="tech-note"
                      value={note}
                      onChange={e => setNote(e.target.value)}
                      rows={2}
                      className="w-full rounded px-3 py-2 text-[11px] resize-none"
                      style={{
                        background: 'var(--surface-inset)',
                        border:     '1px solid var(--border-default)',
                        color:      'var(--text-secondary)',
                        outline:    'none',
                      }}
                      placeholder="Corrected from OCR label reading…"
                    />
                  </div>

                  {renameMutation.isError && (
                    <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>
                      Server error — rename could not be applied. Check audit trail.
                    </p>
                  )}

                  {/* ── Shared: submit ── */}
                  <button
                    type="button"
                    disabled={!canRename || renameMutation.isPending}
                    onClick={() => setConfirming(true)}
                    className="w-full py-2 rounded text-xs font-semibold flex items-center justify-center gap-2"
                    style={{
                      background: canRename ? 'var(--accent)' : 'var(--surface-inset)',
                      color:      canRename ? 'var(--surface-1)' : 'var(--text-faint)',
                      border:     canRename ? 'none' : '1px solid var(--border-default)',
                      cursor:     canRename ? 'pointer' : 'not-allowed',
                    }}
                  >
                    {renameMode === 'builder' ? 'Apply Structured Rename' : 'Correct Filename'}
                    <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </>
              )}
            </div>
          )}

          {/* ── HISTORY TAB ── */}
          {tab === 'history' && (
            <div className="space-y-3">
              <p className="section-label">Recovery & Review History</p>
              <AuditTimeline fileId={file.file_id} />
            </div>
          )}
        </div>
      </div>

      {/* Confirmation dialog */}
      {confirming && (
        <ConfirmRenameDialog
          filename={proposedFilename.trim()}
          onConfirm={() => renameMutation.mutate()}
          onCancel={() => setConfirming(false)}
          isPending={renameMutation.isPending}
        />
      )}
    </>
  )
}
