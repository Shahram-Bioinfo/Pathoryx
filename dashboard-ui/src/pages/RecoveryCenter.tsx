import { FolderOpen, RefreshCcw, Search } from 'lucide-react'
import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { EmptyState } from '../components/ui/EmptyState'
import { ErrorBanner } from '../components/ui/ErrorBanner'
import { PageHeader } from '../components/ui/PageHeader'
import { SkeletonRow } from '../components/ui/LoadingSpinner'
import { StatusBadge } from '../components/ui/StatusBadge'
import { TechnicianReviewDrawer } from '../components/ui/TechnicianReviewDrawer'
import { TelemetryMetricRow } from '../components/ui/TelemetryMetricRow'
import { postOpenFolder } from '../api/watchFolders'
import { useMonitoredFiles } from '../hooks/useMonitoredFiles'
import { useWatchFolders } from '../hooks/useWatchFolders'
import type { MonitoredFileItem } from '../types/api'
import { fmtBytes, fmtDatetime, fmtRelative } from '../utils/formatters'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const FOLDER_TABS = [
  { value: 'failed',        label: 'Failed' },
  { value: 'suspicious',    label: 'Suspicious' },
  { value: 'manual_review', label: 'Manual Review' },
] as const

const FOLDER_DISPLAY: Record<string, string> = {
  failed:        'Failed',
  suspicious:    'Suspicious',
  manual_review: 'Manual Review',
}

const OUTCOME_LABEL: Record<string, string> = {
  auto_recovered:          'auto recovered',
  manual_review_required:  'review required',
  deleted:                 'deleted',
  skipped:                 'skipped',
}

// ---------------------------------------------------------------------------
// File row
// ---------------------------------------------------------------------------

function OpenFolderButton({ item }: { item: MonitoredFileItem }) {
  const [toast, setToast] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: () => postOpenFolder(item.file_id),
    onSuccess: (data) => {
      if (!data.opened) {
        setToast(data.message || 'Could not open folder')
        setTimeout(() => setToast(null), 4000)
      }
    },
    onError: () => {
      setToast('Request failed')
      setTimeout(() => setToast(null), 4000)
    },
  })

  const disabled = !item.folder_exists || mutation.isPending
  const tooltip = item.folder_path ?? 'Unknown path'

  return (
    <div className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => mutation.mutate()}
        title={disabled && !item.folder_exists ? 'Folder no longer exists' : tooltip}
        className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded"
        style={{
          color:      disabled ? 'var(--text-faint)' : 'var(--text-muted)',
          border:     '1px solid var(--border-faint)',
          background: 'transparent',
          cursor:     disabled ? 'not-allowed' : 'pointer',
          opacity:    disabled ? 0.5 : 1,
        }}
      >
        <FolderOpen style={{ width: 11, height: 11, flexShrink: 0 }} aria-hidden />
      </button>
      {toast && (
        <div
          className="absolute bottom-full mb-1 left-0 text-[9px] px-2 py-1 rounded whitespace-nowrap z-50"
          style={{
            background: 'var(--surface-2)',
            border: '1px solid var(--border-default)',
            color: 'var(--chart-rose)',
          }}
        >
          {toast}
        </div>
      )}
    </div>
  )
}

function MonitoredFileRow({
  item,
  onReview,
}: {
  item: MonitoredFileItem
  onReview: (item: MonitoredFileItem) => void
}) {
  const outcomeColor =
    item.recovery_outcome === 'auto_recovered'
      ? 'var(--chart-teal)'
      : item.recovery_outcome === 'manual_review_required'
      ? 'var(--chart-amber)'
      : 'var(--text-faint)'

  const source =
    item.inferred_action === 'dashboard_correction'
      ? 'dashboard correction'
      : item.change_type
      ? 'manual folder change detected'
      : 'awaiting technician'

  // Subfolder context e.g. "failed / 2026-06-05" or "suspicious"
  const locationLine = item.relative_folder_path
    ? `${item.folder_label} / ${item.relative_folder_path}`
    : item.folder_label

  return (
    <tr>
      {/* Filename + subfolder location */}
      <td>
        <span
          className="text-[11px] font-mono truncate block max-w-[200px]"
          style={{ color: 'var(--text-primary)' }}
          title={item.file_path}
        >
          {item.filename}
        </span>
        <span className="text-[9px] font-mono block mt-0.5" style={{ color: 'var(--text-faint)' }}>
          {locationLine}
        </span>
        {item.slide_id && (
          <span className="text-[9px] font-mono block mt-0.5" style={{ color: 'var(--text-faint)' }}>
            {item.slide_id}
          </span>
        )}
      </td>

      {/* Case */}
      <td>
        {item.case_id ? (
          <span className="text-[10px] font-mono" style={{ color: 'var(--accent)' }}>
            {item.case_id}
          </span>
        ) : (
          <span style={{ color: 'var(--text-faint)' }}>—</span>
        )}
      </td>

      {/* Source / workflow */}
      <td>
        <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {source}
        </span>
      </td>

      {/* Review status */}
      <td>
        {item.review_status ? (
          <StatusBadge status={item.review_status} />
        ) : (
          <span className="text-[10px] font-mono" style={{ color: 'var(--text-faint)' }}>
            unreviewed
          </span>
        )}
      </td>

      {/* Recovery outcome */}
      <td>
        {item.recovery_outcome ? (
          <span className="text-[10px] font-mono" style={{ color: outcomeColor }}>
            {OUTCOME_LABEL[item.recovery_outcome] ?? item.recovery_outcome.replace(/_/g, ' ')}
          </span>
        ) : (
          <span style={{ color: 'var(--text-faint)' }}>—</span>
        )}
      </td>

      {/* Size */}
      <td>
        <span className="text-[10px] tabular-nums" style={{ color: 'var(--text-faint)' }}>
          {fmtBytes(item.file_size)}
        </span>
      </td>

      {/* Last seen */}
      <td>
        <span
          className="text-[10px]"
          style={{ color: 'var(--text-faint)' }}
          title={fmtDatetime(item.last_seen_at)}
        >
          {fmtRelative(item.last_seen_at)}
        </span>
      </td>

      {/* Actions */}
      <td>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onReview(item)}
            className="text-[10px] px-2 py-0.5 rounded font-medium"
            style={{
              color:      'var(--accent)',
              border:     '1px solid var(--border-default)',
              background: 'var(--accent-faint)',
            }}
          >
            Review
          </button>
          <OpenFolderButton item={item} />
        </div>
      </td>
    </tr>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export function RecoveryCenter() {
  const [folderTab, setFolderTab] = useState<string>('failed')
  const [search, setSearch] = useState('')
  const [reviewItem, setReviewItem] = useState<MonitoredFileItem | null>(null)

  const { data: foldersData, isPending: foldersPending } = useWatchFolders()
  const { data, isPending, isError, refetch } = useMonitoredFiles({
    folder_type: folderTab,
    search: search.trim() || undefined,
  })

  const folders = foldersData?.folders ?? []
  const folderByLabel = Object.fromEntries(folders.map(f => [f.label, f]))

  const currentFolder = folderByLabel[folderTab]
  const totalAcrossAll = folders.reduce((s, f) => s + f.total_files, 0)
  const awaitingAcrossAll = folders.reduce((s, f) => s + f.awaiting_review, 0)
  const autoRecoveredAll = folders.reduce((s, f) => s + f.auto_recovered, 0)

  const failedCount        = folderByLabel['failed']?.total_files        ?? 0
  const suspiciousCount    = folderByLabel['suspicious']?.total_files    ?? 0
  const manualReviewCount  = folderByLabel['manual_review']?.total_files ?? 0

  return (
    <>
      <PageHeader
        tag="RecoverySentry"
        title="Recovery Center"
        subtitle="Technician interventions and recovery operations"
        actions={
          <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--chart-teal)' }}>
            <RefreshCcw className="h-3 w-3" aria-hidden />
            <span className="tracking-wider">SENTRY ACTIVE</span>
          </div>
        }
      />

      {isError && <div className="mb-5"><ErrorBanner message="Failed to load recovery data." onRetry={refetch} /></div>}

      {/* Counters */}
      <TelemetryMetricRow
        className="mb-6"
        columns={6}
        metrics={[
          {
            key:    'failed',
            label:  'Failed Files',
            value:  String(failedCount),
            accent: failedCount > 0 ? 'var(--chart-rose)' : undefined,
            loading: foldersPending,
          },
          {
            key:    'suspicious',
            label:  'Suspicious',
            value:  String(suspiciousCount),
            accent: suspiciousCount > 0 ? 'var(--chart-amber)' : undefined,
            loading: foldersPending,
          },
          {
            key:    'manual_review',
            label:  'Manual Review',
            value:  String(manualReviewCount),
            accent: manualReviewCount > 0 ? 'var(--chart-amber)' : undefined,
            loading: foldersPending,
          },
          {
            key:    'auto_recovered',
            label:  'Auto Recovered',
            value:  String(autoRecoveredAll),
            accent: autoRecoveredAll > 0 ? 'var(--chart-teal)' : undefined,
            loading: foldersPending,
          },
          {
            key:    'awaiting',
            label:  'Awaiting Technician',
            value:  String(awaitingAcrossAll),
            accent: awaitingAcrossAll > 0 ? 'var(--chart-amber)' : undefined,
            loading: foldersPending,
          },
          {
            key:    'total',
            label:  'Total Monitored',
            value:  String(totalAcrossAll),
            loading: foldersPending,
          },
        ]}
      />

      {/* Folder tabs */}
      <div
        className="flex flex-wrap gap-1 mb-5 p-1 rounded-lg w-fit"
        style={{ background: 'var(--accent-faint)', border: '1px solid var(--border-default)' }}
      >
        {FOLDER_TABS.map(({ value, label }) => {
          const count = folderByLabel[value]?.total_files ?? 0
          return (
            <button
              key={value}
              onClick={() => setFolderTab(value)}
              className="px-3 py-1.5 rounded-md text-[11px] font-medium tracking-wide transition-colors duration-150"
              style={
                folderTab === value
                  ? { color: 'var(--accent)', background: 'var(--surface-2)', border: '1px solid var(--border-default)' }
                  : { color: 'var(--text-muted)', border: '1px solid transparent' }
              }
            >
              {label}
              {!foldersPending && count > 0 && (
                <span
                  className="ml-1.5 text-[9px] font-mono px-1 py-0.5 rounded"
                  style={{
                    background: folderTab === value ? 'var(--accent-faint)' : 'transparent',
                    color: folderTab === value ? 'var(--accent)' : 'var(--text-faint)',
                  }}
                >
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Search */}
      <div className="relative mb-5 w-64">
        <Search
          className="absolute left-3 top-1/2 -translate-y-1/2 h-3 w-3 pointer-events-none"
          style={{ color: 'var(--text-faint)' }}
          aria-hidden
        />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search filename…"
          className="w-full pl-8 pr-3 py-1.5 rounded text-[11px] font-mono"
          style={{
            background: 'var(--surface-inset)',
            border: '1px solid var(--border-default)',
            color: 'var(--text-primary)',
            outline: 'none',
          }}
        />
      </div>

      {/* Active folder label */}
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs font-semibold uppercase tracking-[0.15em]" style={{ color: 'var(--text-secondary)' }}>
          {FOLDER_DISPLAY[folderTab] ?? folderTab} folder
        </span>
        {currentFolder?.last_scan_time && (
          <span className="text-[10px]" style={{ color: 'var(--text-faint)' }}>
            — last scan {fmtRelative(currentFolder.last_scan_time)}
          </span>
        )}
      </div>

      {/* Table */}
      <div
        className="glass rounded-xl overflow-hidden"
        style={{ border: '1px solid var(--border-default)' }}
      >
        <div className="overflow-x-auto">
          <table className="ops-table">
            <thead>
              <tr>
                <th>Filename</th>
                <th>Case</th>
                <th>Source</th>
                <th>Review Status</th>
                <th>Recovery</th>
                <th>Size</th>
                <th>Last Seen</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {isPending ? (
                Array.from({ length: 6 }, (_, i) => <SkeletonRow key={i} cols={8} />)
              ) : (data?.items.length ?? 0) === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <EmptyState
                      title={`No files in ${FOLDER_DISPLAY[folderTab] ?? folderTab} folder`}
                      description="No files currently monitored in this lane."
                    />
                  </td>
                </tr>
              ) : (
                data?.items.map(item => (
                  <MonitoredFileRow
                    key={item.file_id}
                    item={item}
                    onReview={setReviewItem}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
        {!isPending && data && (
          <div
            className="px-5 py-2.5 text-[10px] font-mono tracking-wider"
            style={{ borderTop: '1px solid var(--border-faint)', color: 'var(--text-faint)' }}
          >
            SHOWING {data.items.length} / {data.total} FILES
          </div>
        )}
      </div>

      {/* Technician Review Drawer */}
      {reviewItem && (
        <TechnicianReviewDrawer
          file={reviewItem}
          onClose={() => setReviewItem(null)}
        />
      )}
    </>
  )
}
