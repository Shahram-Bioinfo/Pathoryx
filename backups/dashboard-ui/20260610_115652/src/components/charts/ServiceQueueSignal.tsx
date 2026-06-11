import { getChartColors } from '../../utils/colors'
import { useTheme } from '../layout/ThemeProvider'

interface Props { pending: number; running: number; failed: number; completed: number }

export function ServiceQueueSignal({ pending, running, failed, completed }: Props) {
  useTheme()
  const C = getChartColors()

  const total = pending + running + failed + completed
  if (total === 0) return null

  const segs = [
    { key: 'pending',   v: pending,   color: C.amber   },
    { key: 'running',   v: running,   color: C.cyan    },
    { key: 'failed',    v: failed,    color: C.rose    },
    { key: 'completed', v: completed, color: C.emerald },
  ].filter(s => s.v > 0)

  return (
    <div
      className="flex rounded-sm overflow-hidden w-full"
      style={{ height: '4px', background: 'var(--accent-faint)' }}
    >
      {segs.map(s => (
        <div
          key={s.key}
          style={{
            width: `${(s.v / total) * 100}%`,
            background: s.color,
            opacity: 0.82,
            minWidth: '2px',
          }}
        />
      ))}
    </div>
  )
}
