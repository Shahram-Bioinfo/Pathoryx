import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { DOT_HEX_DARK, statusVariant } from '../../utils/colors'
import { fmtStatusLabel } from '../../utils/formatters'

const TOOLTIP_STYLE = {
  background: 'var(--tooltip-bg)',
  border:     '1px solid var(--tooltip-border)',
  borderRadius: '8px',
  fontSize:   '11px',
  color:      'var(--tooltip-text)',
}

interface Props { byStatus: Record<string, number>; total: number }

export function StatusDonut({ byStatus, total }: Props) {
  const hex = DOT_HEX_DARK

  const data = Object.entries(byStatus)
    .filter(([, v]) => v > 0)
    .map(([status, count]) => ({
      name:  fmtStatusLabel(status),
      value: count,
      color: hex[statusVariant(status)] ?? '#64748b',
    }))

  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={200}>
        <PieChart>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={58}
            outerRadius={80}
            paddingAngle={data.length > 1 ? 2 : 0}
            dataKey="value"
            strokeWidth={0}
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.color} opacity={0.85} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(v: number, name: string) => [v.toLocaleString(), name]}
          />
        </PieChart>
      </ResponsiveContainer>
      {/* Centre label */}
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <span
          className="text-2xl font-semibold tracking-tight tabular"
          style={{ fontFamily: '"JetBrains Mono", monospace', color: 'var(--text-primary)' }}
        >
          {total.toLocaleString()}
        </span>
        <span className="text-[10px] tracking-[0.15em] uppercase mt-0.5" style={{ color: 'var(--text-faint)' }}>
          total
        </span>
      </div>
    </div>
  )
}
