import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip } from 'recharts'
import { getChartColors } from '../../utils/colors'
import { useTheme } from '../layout/ThemeProvider'

interface Props { pending: number; running: number; failed: number; completed: number }

const TOOLTIP_STYLE = {
  background:   'var(--tooltip-bg)',
  border:       '1px solid var(--tooltip-border)',
  borderRadius: '6px',
  fontSize:     '10px',
  color:        'var(--tooltip-text)',
}

export function ServiceQueueMiniChart({ pending, running, failed, completed }: Props) {
  const { theme } = useTheme()
  const C = getChartColors()

  const data = [
    { name: 'Pending', value: pending,   color: C.amber   },
    { name: 'Running', value: running,   color: C.cyan    },
    { name: 'Failed',  value: failed,    color: C.rose    },
    { name: 'Done',    value: completed, color: C.emerald },
  ].filter(d => d.value > 0)

  if (!data.length) return null

  return (
    <ResponsiveContainer width="100%" height={44}>
      <BarChart data={data} barSize={10} barGap={3}>
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'var(--accent-faint)' }} />
        <Bar dataKey="value" radius={[2,2,0,0]}>
          {data.map((d, i) => <Cell key={`${i}-${theme}`} fill={d.color} opacity={0.85} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
