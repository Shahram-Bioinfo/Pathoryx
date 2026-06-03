import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, ChevronRight, X, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { fetchLabelPreview, postTechnicianRename } from '../../api/watchFolders'
import type { MonitoredFileItem, TechnicianRenameResponse } from '../../types/api'
import { fmtBytes, fmtRelative } from '../../utils/formatters'

// ---------------------------------------------------------------------------
// SlideID pattern — mirrors the backend parser for instant client-side feedback
// ---------------------------------------------------------------------------

const SUPPORTED_EXTS = new Set([
  '.svs', '.ndpi', '.mrxs', '.tiff', '.tif', '.scn', '.czi', '.vsi', '.bif',
])

const SLIDE_ID_RE =
  /^(N\d{10})([A-Z]+)-(\d+)-(\d+)-([A-Za-z][A-Za-z0-9&+\-]*)(?:_(UTC\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}Z))?$/

function validateFilename(filename: string): string | null {
  const trimmed = filename.trim()
  if (!trimmed) return 'Filename cannot be empty'
  if (trimmed !== trimmed.split('/').pop()) return 'Filename must not contain path separators'
  if (trimmed.includes('..')) return "Filename must not contain '..'"
  const lastDot = trimmed.lastIndexOf('.')
  if (lastDot < 0) return 'Filename must have an extension'
  const ext = trimmed.slice(lastDot).toLowerCase()
  if (!SUPPORTED_EXTS.has(ext)) return `Unsupported extension '${ext}'`
  const stem = trimmed.slice(0, lastDot)
  if (!SLIDE_ID_RE.test(stem)) {
    return 'Filename does not match Pathoryx slide ID format (e.g. N2024002863SA-1-1-H&E.svs)'
  }
  return null
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function LabelPreviewPanel({ fileId }: { fileId: number }) {
  const { data, isPending } = useQuery({
    queryKey: ['labelPreview', fileId],
    queryFn: () => fetchLabelPreview(fileId),
  })

  if (isPending) {
    return (
      <div
        className="rounded-lg px-4 py-3 text-[10px] font-mono animate-pulse"
        style={{ background: 'var(--surface-inset)', color: 'var(--text-faint)' }}
      >
        Loading label data…
      </div>
    )
  }

  if (!data?.available) {
    const reason = data?.unavailable_reason ?? 'unavailable'
    return (
      <div
        className="rounded-lg px-4 py-3"
        style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
      >
        <p className="text-[9px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-faint)' }}>
          Label Preview
        </p>
        <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          Unavailable — {reason.replace(/_/g, ' ')}
        </p>
        {data?.slide_id && (
          <p className="text-[10px] mt-1 font-mono" style={{ color: 'var(--accent)' }}>
            Slide ID from filename: {data.slide_id}
          </p>
        )}
      </div>
    )
  }

  return (
    <div
      className="rounded-lg px-4 py-3 space-y-1"
      style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
    >
      <p className="text-[9px] uppercase tracking-wider mb-2" style={{ color: 'var(--text-faint)' }}>
        Label Data
      </p>
      {[
        { label: 'Slide ID',        value: data.slide_id },
        { label: 'Case',            value: data.case_id },
        { label: 'Scanner',         value: data.scanner_id },
        { label: 'Vendor',          value: data.scanner_vendor },
        { label: 'Stain',           value: data.stain_type },
        { label: 'DataMatrix',      value: data.datamatrix_raw },
        { label: 'Suggested name',  value: data.suggested_filename },
      ].map(({ label, value }) =>
        value ? (
          <div key={label} className="flex gap-2 text-[10px]">
            <span className="w-24 flex-shrink-0" style={{ color: 'var(--text-faint)' }}>{label}</span>
            <span className="font-mono break-all" style={{ color: 'var(--text-secondary)' }}>{value}</span>
          </div>
        ) : null
      )}
    </div>
  )
}

function ConfirmDialog({
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
      style={{ background: 'rgba(0,0,0,0.55)' }}
    >
      <div
        className="rounded-xl p-5 w-[420px] max-w-[90vw]"
        style={{ background: 'var(--surface-1)', border: '1px solid var(--border-default)' }}
      >
        <p className="text-xs font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
          Confirm Technician Rename
        </p>
        <p className="text-[11px] leading-relaxed mb-4" style={{ color: 'var(--text-secondary)' }}>
          This will rename / move the file, update the artifact record, and requeue QC.
        </p>
        <div
          className="rounded-lg px-3 py-2 mb-4 font-mono text-[10px] break-all"
          style={{ background: 'var(--surface-inset)', color: 'var(--accent)' }}
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
              color: 'var(--surface-1)',
              background: 'var(--accent)',
              opacity: isPending ? 0.6 : 1,
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
  const [proposedFilename, setProposedFilename] = useState(file.filename)
  const [note, setNote] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<TechnicianRenameResponse | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  // Validate as the user types
  useEffect(() => {
    setValidationError(validateFilename(proposedFilename))
  }, [proposedFilename])

  const mutation = useMutation({
    mutationFn: () =>
      postTechnicianRename(file.file_id, {
        proposed_filename: proposedFilename.trim(),
        technician_note: note.trim() || undefined,
        confirm: true,
      }),
    onSuccess: (data) => {
      setResult(data)
      setConfirming(false)
      // Invalidate relevant queries so the UI refreshes
      queryClient.invalidateQueries({ queryKey: ['monitoredFiles'] })
      queryClient.invalidateQueries({ queryKey: ['watchFolders'] })
      queryClient.invalidateQueries({ queryKey: ['recovery'] })
      queryClient.invalidateQueries({ queryKey: ['failures'] })
    },
    onError: () => {
      setConfirming(false)
    },
  })

  const canSubmit = !validationError && proposedFilename.trim() !== ''

  const folderLabelDisplay: Record<string, string> = {
    failed:        'Failed',
    suspicious:    'Suspicious',
    manual_review: 'Manual Review',
  }

  const workflowSource =
    file.inferred_action === 'dashboard_correction'
      ? 'dashboard correction'
      : file.change_type
      ? 'manual folder change detected'
      : 'awaiting technician'

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-40"
        style={{ background: 'rgba(0,0,0,0.35)' }}
        onClick={onClose}
        aria-hidden
      />

      {/* Drawer panel */}
      <div
        className="fixed top-0 right-0 h-full z-50 flex flex-col overflow-y-auto"
        style={{
          width: 'min(480px, 95vw)',
          background: 'var(--surface-1)',
          borderLeft: '1px solid var(--border-default)',
          boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-3 px-5 py-4 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-faint)' }}
        >
          <div className="flex-1 min-w-0">
            <p
              className="text-[9px] font-semibold uppercase tracking-[0.15em]"
              style={{ color: 'var(--text-faint)' }}
            >
              Technician Review
            </p>
            <p
              className="text-xs font-medium truncate mt-0.5"
              style={{ color: 'var(--text-primary)' }}
            >
              {file.filename}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex-shrink-0 p-1 rounded"
            style={{ color: 'var(--text-faint)' }}
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 px-5 py-4 space-y-5">

          {/* File info */}
          <section>
            <p className="section-label mb-2">File Details</p>
            <div
              className="rounded-lg px-4 py-3 space-y-1.5"
              style={{ background: 'var(--surface-inset)', border: '1px solid var(--border-faint)' }}
            >
              {[
                { label: 'Folder',      value: folderLabelDisplay[file.folder_label] ?? file.folder_label },
                { label: 'Current name', value: file.filename },
                { label: 'Slide ID',    value: file.slide_id },
                { label: 'Case',        value: file.case_id },
                { label: 'Size',        value: fmtBytes(file.file_size) },
                { label: 'First seen',  value: fmtRelative(file.first_seen_at) },
                { label: 'Last seen',   value: fmtRelative(file.last_seen_at) },
                { label: 'Source',      value: workflowSource },
              ].map(({ label, value }) =>
                value && value !== '—' ? (
                  <div key={label} className="flex gap-2 text-[10px]">
                    <span className="w-24 flex-shrink-0" style={{ color: 'var(--text-faint)' }}>{label}</span>
                    <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{value}</span>
                  </div>
                ) : null
              )}
              {file.recovery_reason && (
                <div className="flex gap-2 text-[10px] pt-1" style={{ borderTop: '1px solid var(--border-faint)' }}>
                  <span className="w-24 flex-shrink-0" style={{ color: 'var(--chart-amber)' }}>Reason</span>
                  <span className="font-mono" style={{ color: 'var(--chart-amber)' }}>
                    {file.recovery_reason.replace(/_/g, ' ')}
                  </span>
                </div>
              )}
            </div>
          </section>

          {/* Label preview */}
          <section>
            <p className="section-label mb-2">Validate Slide Identity</p>
            <LabelPreviewPanel fileId={file.file_id} />
          </section>

          {/* Rename form — hidden if we already have a result */}
          {!result && (
            <section>
              <p className="section-label mb-2">Correct Filename</p>

              <div className="space-y-3">
                <div>
                  <label
                    htmlFor="proposed-filename"
                    className="block text-[9px] uppercase tracking-wider mb-1"
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
                      border: `1px solid ${validationError ? 'rgba(225,29,72,0.40)' : 'var(--border-default)'}`,
                      color: 'var(--text-primary)',
                      outline: 'none',
                    }}
                    placeholder="N2024002863SA-1-1-H&E.svs"
                  />
                  {validationError ? (
                    <p className="text-[10px] mt-1 flex items-center gap-1" style={{ color: 'var(--chart-rose)' }}>
                      <XCircle className="h-3 w-3 flex-shrink-0" aria-hidden />
                      {validationError}
                    </p>
                  ) : proposedFilename.trim() ? (
                    <p className="text-[10px] mt-1 flex items-center gap-1" style={{ color: 'var(--chart-teal)' }}>
                      <CheckCircle className="h-3 w-3 flex-shrink-0" aria-hidden />
                      Valid Pathoryx slide ID
                    </p>
                  ) : null}
                </div>

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
                      border: '1px solid var(--border-default)',
                      color: 'var(--text-secondary)',
                      outline: 'none',
                    }}
                    placeholder="Corrected from OCR label…"
                  />
                </div>

                {/* Destination preview */}
                {canSubmit && (
                  <div
                    className="rounded px-3 py-2 text-[10px] font-mono"
                    style={{ background: 'var(--accent-faint)', border: '1px solid var(--border-default)' }}
                  >
                    <span style={{ color: 'var(--text-faint)' }}>Destination preview  </span>
                    <span style={{ color: 'var(--accent)' }}>
                      final/{proposedFilename.trim().replace(/^(N\d{10}).*/, '$1')}/
                      {proposedFilename.trim()}
                    </span>
                  </div>
                )}

                {mutation.isError && (
                  <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>
                    Server error — rename could not be applied.
                  </p>
                )}

                <button
                  type="button"
                  disabled={!canSubmit || mutation.isPending}
                  onClick={() => setConfirming(true)}
                  className="w-full py-2 rounded text-xs font-semibold flex items-center justify-center gap-2"
                  style={{
                    background: canSubmit ? 'var(--accent)' : 'var(--surface-inset)',
                    color: canSubmit ? 'var(--surface-1)' : 'var(--text-faint)',
                    border: canSubmit ? 'none' : '1px solid var(--border-default)',
                    cursor: canSubmit ? 'pointer' : 'not-allowed',
                  }}
                >
                  Correct Filename
                  <ChevronRight className="h-3.5 w-3.5" aria-hidden />
                </button>
              </div>
            </section>
          )}

          {/* Result panel */}
          {result && (
            <section>
              <p className="section-label mb-2">Recovery Outcome</p>
              <div
                className="rounded-lg px-4 py-3 space-y-1.5"
                style={{
                  background: result.outcome === 'auto_recovered'
                    ? 'rgba(52,211,153,0.06)'
                    : result.outcome === 'validation_failed'
                    ? 'rgba(225,29,72,0.06)'
                    : 'rgba(217,119,6,0.06)',
                  border: `1px solid ${
                    result.outcome === 'auto_recovered'
                      ? 'rgba(52,211,153,0.20)'
                      : result.outcome === 'validation_failed'
                      ? 'rgba(225,29,72,0.20)'
                      : 'rgba(217,119,6,0.20)'
                  }`,
                }}
              >
                <p className="text-xs font-semibold" style={{
                  color: result.outcome === 'auto_recovered'
                    ? 'var(--chart-teal)'
                    : result.outcome === 'validation_failed'
                    ? 'var(--chart-rose)'
                    : 'var(--chart-amber)',
                }}>
                  {result.outcome === 'auto_recovered'   ? 'Auto Recovered — QC Requeued'  :
                   result.outcome === 'validation_failed' ? 'Validation Failed'             :
                   result.outcome === 'manual_review_required' ? 'Manual Review Required'  :
                   result.outcome}
                </p>
                {result.validation_error && (
                  <p className="text-[10px]" style={{ color: 'var(--chart-rose)' }}>{result.validation_error}</p>
                )}
                {result.reason && (
                  <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    Reason: {result.reason.replace(/_/g, ' ')}
                  </p>
                )}
                {result.destination_path && (
                  <p className="text-[10px] font-mono break-all" style={{ color: 'var(--text-muted)' }}>
                    {result.destination_path}
                  </p>
                )}
              </div>
            </section>
          )}
        </div>
      </div>

      {/* Confirmation dialog */}
      {confirming && (
        <ConfirmDialog
          filename={proposedFilename.trim()}
          onConfirm={() => mutation.mutate()}
          onCancel={() => setConfirming(false)}
          isPending={mutation.isPending}
        />
      )}
    </>
  )
}
