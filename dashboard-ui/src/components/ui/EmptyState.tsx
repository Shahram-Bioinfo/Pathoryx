interface Props {
  title?: string
  description?: string
  icon?: string
}

export function EmptyState({ title = 'No data', description = 'Nothing to display yet.', icon = '◎' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <div
        className="text-3xl mb-4"
        style={{ color: 'var(--border-strong)' }}
      >
        {icon}
      </div>
      <p
        className="text-xs font-semibold uppercase tracking-[0.2em]"
        style={{ color: 'var(--text-muted)' }}
      >
        {title}
      </p>
      <p className="text-xs mt-1.5 max-w-xs" style={{ color: 'var(--text-faint)' }}>
        {description}
      </p>
    </div>
  )
}
