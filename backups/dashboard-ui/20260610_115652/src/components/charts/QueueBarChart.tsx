import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { QueueRow } from '../../types/api'
import { getChartColors } from '../../utils/colors'
import { useTheme } from '../layout/ThemeProvider'
import { fmtServiceName } from '../../utils/formatters'

interface Props { queues: QueueRow[] }

const TOOLTIP_STYLE = {
  background: 'var(--tooltip-bg)',
  border:     '1px solid var(--tooltip-border)',
  borderRadius: '8px',
  fontSize:   '11px',
  color:      'var(--tooltip-text)',
}

export function QueueBarChart({ queues }: Props) {
  const { theme } = useTheme()
  const C = getChartColors()   /* recalculates on theme change */

  const data = queues.map(q => ({
    name:      fmtServiceName(q.target_service),
    Pending:   q.pending,
    Running:   q.running,
    Failed:    q.failed,
    Completed: q.completed,
  }))

  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} barSize={12} barGap={3}>
        <CartesianGrid strokeDasharray="2 4" vertical={false} />
        <XAxis dataKey="name" tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
        <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: 'var(--accent-faint)' }} />
        <Legend iconType="circle" iconSize={6} wrapperStyle={{ fontSize: '10px', paddingTop: '12px' }} />
        <Bar key={`pending-${theme}`}   dataKey="Pending"   fill={C.amber}   radius={[3,3,0,0]} opacity={0.85} />
        <Bar key={`running-${theme}`}   dataKey="Running"   fill={C.cyan}    radius={[3,3,0,0]} opacity={0.85} />
        <Bar key={`failed-${theme}`}    dataKey="Failed"    fill={C.rose}    radius={[3,3,0,0]} opacity={0.85} />
        <Bar key={`completed-${theme}`} dataKey="Completed" fill={C.emerald} radius={[3,3,0,0]} opacity={0.85} />
      </BarChart>
    </ResponsiveContainer>
  )
}
