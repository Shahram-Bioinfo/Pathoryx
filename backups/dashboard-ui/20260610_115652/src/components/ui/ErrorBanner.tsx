import { AlertOctagon, RefreshCw } from 'lucide-react'

interface Props {
  message?: string
  onRetry?: () => void
}

export function ErrorBanner({ message = 'Failed to load data.', onRetry }: Props) {
  return (
    <div
      className="flex items-center gap-3 rounded-lg px-4 py-3 text-sm"
      style={{
        background:   'rgba(225, 29, 72, 0.06)',
        border:       '1px solid rgba(225, 29, 72, 0.18)',
      }}
    >
      <AlertOctagon
        className="h-4 w-4 flex-shrink-0 text-rose-600 dark:text-rose-400"
        aria-hidden
      />
      <span className="flex-1 text-xs text-rose-700 dark:text-rose-300">{message}</span>
      {onRetry && (
        <button
          onClick={onRetry}
          className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider
                     text-rose-600 dark:text-rose-400 hover:text-rose-500 transition-colors duration-150"
        >
          <RefreshCw className="h-3 w-3" aria-hidden /> Retry
        </button>
      )}
    </div>
  )
}
