import clsx from 'clsx'

interface Props {
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
}

const SIZES = { xs: 'h-3 w-3', sm: 'h-4 w-4', md: 'h-5 w-5', lg: 'h-8 w-8' }

export function LoadingSpinner({ size = 'md', className }: Props) {
  return (
    <svg
      className={clsx('animate-spin', SIZES[size], className)}
      viewBox="0 0 20 20"
      fill="none"
      aria-label="Loading"
    >
      <circle cx="10" cy="10" r="8" stroke="var(--border-default)" strokeWidth="1.5" />
      <path
        stroke="var(--accent)"
        strokeWidth="1.5"
        strokeLinecap="round"
        d="M10 2a8 8 0 0 1 8 8"
        style={{ opacity: 0.7 }}
      />
    </svg>
  )
}

export function PageLoader() {
  return (
    <div className="flex flex-col items-center justify-center h-64 gap-4" role="status">
      <div className="relative h-12 w-12">
        {/* Outer ring */}
        <svg
          className="absolute inset-0 animate-spin"
          style={{ animationDuration: '2s' }}
          viewBox="0 0 48 48" fill="none"
        >
          <circle cx="24" cy="24" r="20" stroke="var(--border-default)" strokeWidth="1.5" />
          <path
            stroke="var(--accent)"
            strokeWidth="1.5"
            strokeLinecap="round"
            d="M24 4a20 20 0 0 1 20 20"
            style={{ opacity: 0.5 }}
          />
        </svg>
        {/* Inner ring — counter-rotation */}
        <svg
          className="absolute inset-[10px] animate-spin"
          style={{ animationDuration: '1.4s', animationDirection: 'reverse' }}
          viewBox="0 0 28 28" fill="none"
        >
          <circle cx="14" cy="14" r="10" stroke="var(--border-faint)" strokeWidth="1.5" />
          <path
            stroke="var(--chart-teal)"
            strokeWidth="1.5"
            strokeLinecap="round"
            d="M14 4a10 10 0 0 1 10 10"
            style={{ opacity: 0.45 }}
          />
        </svg>
      </div>
      <p
        className="text-[10px] tracking-[0.25em] uppercase"
        style={{ color: 'var(--text-faint)' }}
        aria-live="polite"
      >
        Loading
      </p>
    </div>
  )
}

export function SkeletonRow({ cols = 5 }: { cols?: number }) {
  const widths = [55, 35, 28, 45, 22, 38, 30]
  return (
    <tr aria-hidden>
      {Array.from({ length: cols }, (_, i) => (
        <td key={i} className="px-4 py-3">
          <div className="ops-skeleton h-3 rounded" style={{ width: `${widths[i % widths.length]}%` }} />
        </td>
      ))}
    </tr>
  )
}
