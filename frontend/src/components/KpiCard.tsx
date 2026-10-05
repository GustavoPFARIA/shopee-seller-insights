export function Delta({ value, label = 'vs previous period' }: { value: number | null; label?: string }) {
  if (value === null) return <span className="delta">No data for the previous period</span>
  const dir = value > 0 ? 'up' : value < 0 ? 'down' : 'flat'
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : '■'
  return (
    <span className={`delta ${dir}`}>
      {arrow} {Math.abs(value).toFixed(1)}% {label}
    </span>
  )
}

export default function KpiCard({
  label,
  value,
  delta,
  hint,
}: {
  label: string
  value: string
  delta?: number | null
  hint?: string
}) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {delta !== undefined && <Delta value={delta} />}
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  )
}
