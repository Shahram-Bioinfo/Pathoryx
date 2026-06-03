/**
 * TechnicianReviewDrawer — Phase 8 Technician Review & Manual Rename
 *
 * Opened from RecoveryCenter and FailureCenter for failed, suspicious, and
 * manual-review artifacts.  Shows everything the technician needs:
 *
 *   - Parsed label metadata (scanner, DataMatrix, stain, ROI data, routing reason)
 *   - Label image thumbnail (if BabelShark saved one)
 *   - Live structured filename validation using the real Pathoryx parser rules
 *   - Rename + requeue workflow with confirmation
 *   - Review state transitions (Investigating / Dismiss)
 *   - Full chronological audit trail
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
  MonitoredFileItem,
  TechnicianRenameResponse,
} from '../../types/api'
import { fmtBytes, fmtDatetime, fmtEventType, fmtRelative } from '../../utils/formatters'

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
// Sub-component: label metadata panel
// ---------------------------------------------------------------------------

function LabelMetadataPanel({ fileId }: { fileId: number }) {
  const BASE = '/dashboard/api'
  const { data, isPending } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn: () => fetchLabelPreview(fileId),
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
      {/* Label image thumbnail */}
      {!imageError && (
        <div
          className="px-4 pt-3 pb-2"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
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

      {/* Metadata rows */}
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
              <span
                className="font-mono break-all"
                style={{ color: accent ?? 'var(--text-secondary)' }}
              >
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
  const icon =
    cls === 'valid'           ? CheckCircle :
    cls === 'partially_valid' ? Clock       :
                                XCircle

  const Icon = icon

  return (
    <div
      className="rounded-lg px-3 py-2 space-y-1.5"
      style={{ background: 'var(--surface-inset)', border: `1px solid ${color}22` }}
    >
      {/* Classification badge */}
      <div className="flex items-center gap-1.5">
        <Icon className="h-3 w-3 flex-shrink-0" style={{ color }} aria-hidden />
        <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color }}>
          {cls === 'valid' ? 'Valid' : cls === 'partially_valid' ? 'Partially valid' : 'Invalid'}
        </span>
      </div>

      {/* Component breakdown */}
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

      {/* Errors */}
      {serverResult.errors.map(e => (
        <p key={e.code} className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>{e.message}</p>
      ))}

      {/* Warnings */}
      {serverResult.warnings.map(w => (
        <p key={w.code} className="text-[10px]" style={{ color: 'var(--chart-amber)' }}>{w.message}</p>
      ))}

      {/* Suggestion */}
      {serverResult.suggested_correction && cls !== 'valid' && (
        <p className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
          Suggestion: <span className="font-mono" style={{ color: 'var(--accent)' }}>
            {serverResult.suggested_correction}
          </span>
        </p>
      )}
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

  // Merge and sort by timestamp
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
          const src =
            c.inferred_action === 'dashboard_correction'
              ? 'dashboard'
              : 'filesystem'
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
                    {' '}{c.old_filename} → <span className="font-mono" style={{ color: 'var(--accent)' }}>{c.new_filename}</span>
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

interface Props {
  file: MonitoredFileItem
  onClose: () => void
}

export function TechnicianReviewDrawer({ file, onClose }: Props) {
  const queryClient = useQueryClient()

  // Rename form state
  const [proposedFilename, setProposedFilename]   = useState(file.filename)
  const [note, setNote]                            = useState('')
  const [confirming, setConfirming]               = useState(false)
  const [renameResult, setRenameResult]           = useState<TechnicianRenameResponse | null>(null)

  // Active tab inside the drawer
  const [tab, setTab] = useState<'inspect' | 'rename' | 'history'>('inspect')

  const inputRef = useRef<HTMLInputElement>(null)

  // Server-side validation (debounced)
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

  // Rename mutation
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

  // Review state mutation
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
    file.inferred_action === 'dashboard_correction'  ? 'Dashboard correction'         :
    file.change_type === 'rename'                    ? 'Manual folder rename'          :
    file.change_type === 'new_file'                  ? 'New file detected'             :
    file.change_type                                 ? `Detected: ${file.change_type.replace(/_/g, ' ')}` :
                                                       'Awaiting technician'

  const reviewStatus = file.review_status ?? null

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
          width:      'min(520px, 96vw)',
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

            {/* File metadata strip */}
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
                // Only show if transition is sensible
                (status === 'dismissed' && current === 'dismissed') ||
                (status === 'investigating' && current === 'investigating')
              return (
                <button
                  key={status}
                  type="button"
                  disabled={blocked}
                  onClick={() =>
                    reviewMutation.mutate({ status, note: undefined })
                  }
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
              { id: 'inspect' as const, label: 'Inspect Label' },
              { id: 'rename'  as const, label: 'Correct Filename' },
              { id: 'history' as const, label: 'Audit Trail' },
            ]
          ).map(({ id, label }) => (
            <button
              key={id}
              type="button"
              onClick={() => { setTab(id); if (id === 'rename') inputRef.current?.focus() }}
              className="px-4 py-2.5 text-[10px] font-medium tracking-wide"
              style={{
                color:      tab === id ? 'var(--accent)'       : 'var(--text-muted)',
                borderBottom: tab === id
                  ? '2px solid var(--accent)'
                  : '2px solid transparent',
                background: 'transparent',
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

          {/* ── RENAME TAB ── */}
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
                  </div>

                  {/* Validation feedback */}
                  <ValidationPanel
                    filename={proposedFilename}
                    serverResult={validationResult}
                    isPending={validating}
                  />

                  {/* Destination preview */}
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

                  {/* Note field */}
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
                    Correct Filename
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
