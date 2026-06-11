import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArtifactInspectorPanel } from '../components/ui/ArtifactInspectorPanel'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { LiveIndicator } from '../components/ui/LiveIndicator'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { StatusBadge } from '../components/ui/StatusBadge'
import { useSlides } from '../hooks/useSlides'
import {
  fmtBytes,
  fmtDatetime,
  fmtRelative,
  fmtStatusLabel,
} from '../utils/formatters'
import { DOT_CLASSES, statusVariant } from '../utils/colors'
import { useTheme } from '../components/layout/ThemeProvider'
import type { SlideItem } from '../types/api'

const STATUSES = [
  'detected','intake_running','intake_registered',
  'qc_pending','qc_running','qc_passed','qc_failed',
  'dicom_pending','dicom_running','dicom_done','dicom_failed',
  'upload_pending','upload_running','uploaded','upload_failed',
]

// ─── Compact row (used in split-view list panel) ──────────────────────────────

function CompactArtifactRow({
  slide,
  selected,
  onClick,
}: {
  slide:    SlideItem
  selected: boolean
  onClick:  () => void
}) {
  const variant = statusVariant(slide.status)
  return (
    <button
      type="button"
      className="w-full text-left flex items-start gap-2.5 px-3 py-2.5 transition-colors duration-100"
      style={{
        borderBottom:  '1px solid var(--border-faint)',
        borderLeft:    selected ? '2px solid var(--accent)' : '2px solid transparent',
        background:    selected ? 'var(--accent-faint)' : 'transparent',
        paddingLeft:   selected ? '10px' : '12px',
      }}
      onClick={onClick}
    >
      {/* Status dot */}
      <span
        className={`h-1.5 w-1.5 rounded-full flex-shrink-0 mt-1 ${DOT_CLASSES[variant]}`}
        aria-hidden
      />

      {/* Name + artifact ID */}
      <div className="flex-1 min-w-0">
        <p
          className="text-[11px] font-medium leading-tight truncate"
          style={{ color: selected ? 'var(--text-primary)' : 'var(--text-secondary)' }}
        >
          {slide.original_filename ?? slide.global_artifact_id ?? '—'}
        </p>
        {slide.global_artifact_id && slide.original_filename && (
          <p className="text-[9px] font-mono mt-0.5 truncate" style={{ color: 'var(--text-faint)' }}>
            {slide.global_artifact_id.slice(0, 20)}…
          </p>
        )}
      </div>

      {/* Right column: status + time */}
      <div className="flex flex-col items-end gap-0.5 flex-shrink-0">
        <StatusBadge status={slide.status} />
        <span className="text-[8px] font-mono" style={{ color: 'var(--text-faint)' }}>
          {fmtRelative(slide.created_at)}
        </span>
      </div>
    </button>
  )
}

// ─── Full table row (used in full-width view) ─────────────────────────────────

function FullTableRow({
  slide,
  onClick,
}: {
  slide:   SlideItem
  onClick: () => void
}) {
  return (
    <tr onClick={onClick} className="cursor-pointer">
      <td>
        <p className="text-sm font-medium truncate max-w-[280px]" style={{ color: 'var(--text-primary)' }}>
          {slide.original_filename ?? '—'}
        </p>
        {slide.global_artifact_id && (
          <p className="text-[10px] font-mono mt-0.5 truncate" style={{ color: 'var(--accent)' }}>
            {slide.global_artifact_id}
          </p>
        )}
      </td>
      <td><StatusBadge status={slide.status} /></td>
      <td>
        <span className="text-xs font-mono" style={{ color: 'var(--text-muted)' }}>
          {slide.file_format ?? '—'}
        </span>
      </td>
      <td>
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {slide.scanner_name ?? slide.scanner_id ?? '—'}
        </span>
      </td>
      <td className="text-right">
        <span className="text-xs font-mono tabular" style={{ color: 'var(--text-muted)' }}>
          {fmtBytes(slide.file_size)}
        </span>
      </td>
      <td>
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {fmtDatetime(slide.created_at)}
        </span>
      </td>
    </tr>
  )
}

// ─── Filter bar ───────────────────────────────────────────────────────────────

function FilterBar({
  search,
  setSearch,
  status,
  setStatus,
  compact,
}: {
  search:    string
  setSearch: (v: string) => void
  status:    string
  setStatus: (v: string) => void
  compact:   boolean
}) {
  return (
    <div
      className="glass rounded-xl p-3 flex flex-wrap items-center gap-2"
      style={{ border: '1px solid var(--border-default)' }}
    >
      <div className={`relative ${compact ? 'flex-1' : 'flex-1 min-w-[200px] max-w-xs'}`}>
        <Search
          className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3 w-3"
          style={{ color: 'var(--text-faint)' }}
          aria-hidden
        />
        <input
          className="ops-input pl-7 text-xs py-1.5"
          placeholder={compact ? 'Search…' : 'Search filename, artifact ID…'}
          value={search}
          onChange={e => setSearch(e.target.value)}
        />
      </div>
      {!compact && (
        <select
          className="ops-select text-xs py-1.5"
          value={status}
          onChange={e => { setStatus(e.target.value) }}
        >
          <option value="">All statuses</option>
          {STATUSES.map(s => (
            <option key={s} value={s}>{fmtStatusLabel(s)}</option>
          ))}
        </select>
      )}
      {compact && (
        <select
          className="ops-select text-xs py-1.5 w-24"
          value={status}
          onChange={e => { setStatus(e.target.value) }}
          title="Filter by status"
        >
          <option value="">All</option>
          {STATUSES.map(s => (
            <option key={s} value={s}>{fmtStatusLabel(s)}</option>
          ))}
        </select>
      )}
    </div>
  )
}

// ─── Pagination bar ───────────────────────────────────────────────────────────

function PaginationBar({
  page,
  totalPages,
  total,
  setPage,
  compact,
}: {
  page:       number
  totalPages: number
  total:      number
  setPage:    (fn: (p: number) => number) => void
  compact:    boolean
}) {
  if (total === 0) return null

  return (
    <div
      className="flex items-center justify-between px-3 py-2"
      style={{ borderTop: '1px solid var(--border-faint)' }}
    >
      {!compact && (
        <span className="text-[9px] tracking-wider font-mono" style={{ color: 'var(--text-faint)' }}>
          {page}/{totalPages} — {total.toLocaleString()}
        </span>
      )}
      <div className={`flex items-center gap-0.5 ${compact ? 'w-full justify-between' : ''}`}>
        <button
          onClick={() => setPage(p => Math.max(1, p - 1))}
          disabled={page === 1}
          className="btn-ghost-ops p-1 disabled:opacity-30"
        >
          <ChevronLeft className="h-3.5 w-3.5" aria-hidden />
        </button>
        {!compact && Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
          const pg = Math.max(1, Math.min(page - 2 + i, totalPages - 4 + i))
          return (
            <button
              key={pg}
              onClick={() => setPage(() => pg)}
              className="text-[9px] font-mono px-2 py-1 rounded transition-colors duration-150"
              style={
                pg === page
                  ? { color: 'var(--accent)', background: 'var(--accent-faint)', border: '1px solid var(--border-default)' }
                  : { color: 'var(--text-muted)' }
              }
            >
              {pg}
            </button>
          )
        })}
        {compact && (
          <span className="text-[9px] font-mono" style={{ color: 'var(--text-faint)' }}>
            {page}/{totalPages}
          </span>
        )}
        <button
          onClick={() => setPage(p => Math.min(totalPages, p + 1))}
          disabled={page === totalPages}
          className="btn-ghost-ops p-1 disabled:opacity-30"
        >
          <ChevronRight className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export function SlideExplorer() {
  useTheme()
  const navigate = useNavigate()

  const [page, setPage]         = useState(1)
  const [pageSize]              = useState(50)
  const [status, setStatus]     = useState('')
  const [search, setSearch]     = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const { data, isPending, isError, refetch, isFetching } = useSlides({
    page, page_size: pageSize, status: status || undefined,
  })

  const totalPages = data ? Math.ceil(data.total / pageSize) : 1

  const rows: SlideItem[] = search
    ? (data?.items ?? []).filter(r =>
        [r.original_filename, r.global_artifact_id, r.scanner_name]
          .some(v => v?.toLowerCase().includes(search.toLowerCase()))
      )
    : (data?.items ?? [])

  const isSplit = !!selectedId

  function handleRowClick(slide: SlideItem) {
    if (!slide.global_artifact_id) return
    const isDesktop = typeof window !== 'undefined' && window.innerWidth >= 1024
    if (isDesktop) {
      setSelectedId(prev => prev === slide.global_artifact_id ? null : slide.global_artifact_id)
    } else {
      navigate(`/slides/${encodeURIComponent(slide.global_artifact_id)}`)
    }
  }

  function handleStatusChange(v: string) {
    setStatus(v)
    setPage(1)
  }

  return (
    <>
      <PageHeader
        tag="Slide Registry"
        title="Slide Explorer"
        subtitle={data ? `${data.total.toLocaleString()} artifacts indexed` : undefined}
        actions={isFetching && !isPending ? <LiveIndicator status="active" label="SYNCING" /> : undefined}
      />

      {isError && (
        <div className="mb-5">
          <ErrorBanner message="Failed to load slides." onRetry={refetch} />
        </div>
      )}

      {/* ── Split layout ───────────────────────────────────────────────────── */}
      {isSplit ? (
        <div className="flex gap-4 items-start">

          {/* Left: compact list panel */}
          <div className="flex-shrink-0 flex flex-col gap-3" style={{ width: '22rem' }}>
            <FilterBar
              search={search}
              setSearch={setSearch}
              status={status}
              setStatus={handleStatusChange}
              compact
            />

            <div
              className="glass rounded-xl overflow-hidden flex flex-col"
              style={{ border: '1px solid var(--border-default)' }}
            >
              {/* List */}
              <div className="overflow-y-auto scrollbar-none" style={{ maxHeight: 'calc(100vh - 18rem)' }}>
                {isPending ? (
                  <div className="p-3 space-y-2">
                    {Array.from({ length: 8 }, (_, i) => (
                      <div key={i} className="ops-skeleton h-10 rounded" />
                    ))}
                  </div>
                ) : rows.length === 0 ? (
                  <div className="p-4">
                    <EmptyState title="No slides found" description="Adjust filters." />
                  </div>
                ) : (
                  rows.map(slide => (
                    <CompactArtifactRow
                      key={slide.internal_id}
                      slide={slide}
                      selected={slide.global_artifact_id === selectedId}
                      onClick={() => handleRowClick(slide)}
                    />
                  ))
                )}
              </div>

              {/* Pagination */}
              {!isPending && data && (
                <PaginationBar
                  page={page}
                  totalPages={totalPages}
                  total={data.total}
                  setPage={setPage}
                  compact
                />
              )}
            </div>
          </div>

          {/* Right: artifact inspector */}
          <div className="flex-1 min-w-0 sticky" style={{ top: '3.5rem' }}>
            <ArtifactInspectorPanel
              artifactId={selectedId}
              onClose={() => setSelectedId(null)}
            />
          </div>
        </div>

      ) : (
        /* ── Full-width table view ─────────────────────────────────────────── */
        <>
          <FilterBar
            search={search}
            setSearch={setSearch}
            status={status}
            setStatus={handleStatusChange}
            compact={false}
          />

          <div
            className="glass rounded-xl overflow-hidden mt-5"
            style={{ border: '1px solid var(--border-default)' }}
          >
            <div className="overflow-x-auto">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>Artifact / Filename</th>
                    <th>Status</th>
                    <th>Format</th>
                    <th>Scanner</th>
                    <th className="text-right">Size</th>
                    <th>Registered</th>
                  </tr>
                </thead>
                <tbody>
                  {isPending ? (
                    Array.from({ length: 8 }, (_, i) => <SkeletonRow key={i} cols={6} />)
                  ) : rows.length === 0 ? (
                    <tr>
                      <td colSpan={6}>
                        <EmptyState
                          title="No slides found"
                          description="Adjust filters or wait for new slides to be acquired."
                        />
                      </td>
                    </tr>
                  ) : (
                    rows.map(slide => (
                      <FullTableRow
                        key={slide.internal_id}
                        slide={slide}
                        onClick={() => handleRowClick(slide)}
                      />
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {!isPending && data && data.total > 0 && (
              <PaginationBar
                page={page}
                totalPages={totalPages}
                total={data.total}
                setPage={setPage}
                compact={false}
              />
            )}
          </div>
        </>
      )}
    </>
  )
}
