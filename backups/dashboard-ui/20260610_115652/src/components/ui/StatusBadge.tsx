import clsx from 'clsx'
import { BADGE_CLASSES, statusVariant } from '../../utils/colors'

interface Props {
  status: string | null | undefined
  className?: string
}

export function StatusBadge({ status, className }: Props) {
  if (!status) return <span className="text-slate-600 text-xs font-mono">—</span>

  const variant = statusVariant(status)
  const label = status.replace(/_/g, ' ')

  return (
    <span
      className={clsx(
        'inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold tracking-wide uppercase',
        BADGE_CLASSES[variant],
        className,
      )}
    >
      {label}
    </span>
  )
}
