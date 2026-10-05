import { useEffect, useMemo, useState } from 'react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  api,
  ApiError,
  fmtMoney,
  fmtPct,
  type AbcItem,
  type AiSummary,
  type Alert,
  type DailyPoint,
  type Overview,
  type ProductMetrics,
} from '../api'

const isoDay = (d: Date) => d.toISOString().slice(0, 10)
const daysAgo = (n: number) => isoDay(new Date(Date.now() - n * 86_400_000))
const ALERT_LABEL: Record<Alert['kind'], string> = {
  low_stock: 'Low stock',
  stalled_product: 'Stalled',
  low_margin: 'Low margin',
}
const axisTick = { fill: 'var(--text-muted)', fontSize: 12 }
const compactBrl = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })

function Delta({ value }: { value: number | null }) {
  if (value === null) return <span className="delta">no previous data</span>
  const cls = value > 0 ? 'up' : value < 0 ? 'down' : ''
  const arrow = value > 0 ? '▲' : value < 0 ? '▼' : '■'
  return (
    <span className={`delta ${cls}`}>
      {arrow} {Math.abs(value).toFixed(1)}% vs previous period
    </span>
  )
}

function Kpi({ label, value, delta }: { label: string; value: string; delta?: number | null }) {
  return (
    <div className="card kpi">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {delta !== undefined && <Delta value={delta} />}
    </div>
  )
}

export default function Dashboard() {
  const [range, setRange] = useState({ start: daysAgo(29), end: daysAgo(0) })
  const [overview, setOverview] = useState<Overview | null>(null)
  const [daily, setDaily] = useState<DailyPoint[]>([])
  const [products, setProducts] = useState<ProductMetrics[]>([])
  const [abc, setAbc] = useState<AbcItem[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [error, setError] = useState<string | null>(null)
  const [summary, setSummary] = useState<AiSummary | null>(null)
  const [summaryBusy, setSummaryBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.overview(range),
      api.daily(range),
      api.products(range),
      api.abc(range),
      api.alerts(),
    ])
      .then(([o, d, p, a, al]) => {
        if (cancelled) return
        setOverview(o)
        setDaily(d)
        setProducts(p)
        setAbc(a)
        setAlerts(al)
        setError(null)
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : 'Load failed'))
    return () => {
      cancelled = true
    }
  }, [range])

  const dailyChart = useMemo(
    () => daily.map((d) => ({ day: d.day.slice(5), revenue: Number(d.revenue), orders: d.orders })),
    [daily],
  )
  const topChart = useMemo(
    () => products.slice(0, 8).map((p) => ({ name: p.name, revenue: Number(p.revenue) })),
    [products],
  )

  async function loadSummary() {
    setSummaryBusy(true)
    try {
      setSummary(await api.summary())
    } catch (e) {
      setSummary({ enabled: true, summary: e instanceof ApiError ? `Error: ${e.message}` : 'Error' })
    } finally {
      setSummaryBusy(false)
    }
  }

  const preset = (days: number) => setRange({ start: daysAgo(days - 1), end: daysAgo(0) })

  return (
    <>
      <div className="filters">
        <label>
          From
          <input
            type="date"
            value={range.start}
            max={range.end}
            onChange={(e) => e.target.value && setRange({ ...range, start: e.target.value })}
          />
        </label>
        <label>
          To
          <input
            type="date"
            value={range.end}
            min={range.start}
            onChange={(e) => e.target.value && setRange({ ...range, end: e.target.value })}
          />
        </label>
        <button className="secondary" onClick={() => preset(7)}>Last 7 days</button>
        <button className="secondary" onClick={() => preset(30)}>Last 30 days</button>
        <button className="secondary" onClick={() => preset(90)}>Last 90 days</button>
        <button className="secondary" onClick={() => api.exportCsv(range)}>Export CSV</button>
      </div>
      {error && <p className="error">{error}</p>}

      {overview && (
        <div className="grid-kpi">
          <Kpi label="Revenue" value={fmtMoney(overview.current.revenue)} delta={overview.change.revenue_pct} />
          <Kpi label="Orders" value={String(overview.current.orders)} delta={overview.change.orders_pct} />
          <Kpi label="Average ticket" value={fmtMoney(overview.current.avg_ticket)} delta={overview.change.avg_ticket_pct} />
          <Kpi
            label="Net margin (after fees & cost)"
            value={overview.current.net_margin === null ? 'Set product costs' : fmtMoney(overview.current.net_margin)}
          />
        </div>
      )}

      <div className="two-col">
        <div className="card">
          <h2>Daily revenue</h2>
          {dailyChart.length === 0 ? (
            <p className="muted">No sales in this period.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={dailyChart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="day" tick={axisTick} tickLine={false} axisLine={false} minTickGap={24} />
                <YAxis tick={axisTick} tickLine={false} axisLine={false} width={48} tickFormatter={(v) => compactBrl.format(v)} />
                <Tooltip
                  formatter={(v) => [fmtMoney(Number(v)), 'Revenue']}
                  contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
                  labelStyle={{ color: 'var(--text-secondary)' }}
                  cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 3' }}
                />
                <Area type="monotone" dataKey="revenue" stroke="var(--series-1)" strokeWidth={2} fill="var(--series-1)" fillOpacity={0.12} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        <div className="card">
          <h2>Top products by revenue</h2>
          {topChart.length === 0 ? (
            <p className="muted">No sales in this period.</p>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={topChart} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" horizontal={false} />
                <XAxis type="number" tick={axisTick} tickLine={false} axisLine={false} tickFormatter={(v) => compactBrl.format(v)} />
                <YAxis type="category" dataKey="name" tick={axisTick} tickLine={false} axisLine={false} width={150} />
                <Tooltip
                  formatter={(v) => [fmtMoney(Number(v)), 'Revenue']}
                  contentStyle={{ background: 'var(--surface)', border: '1px solid var(--border)' }}
                  cursor={{ fill: 'var(--grid)' }}
                />
                <Bar dataKey="revenue" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={14} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="two-col">
        <div className="card">
          <h2>Alerts ({alerts.length})</h2>
          {alerts.length === 0 ? (
            <p className="muted">Nothing needs your attention.</p>
          ) : (
            <ul className="alerts">
              {alerts.slice(0, 12).map((a) => (
                <li key={`${a.kind}-${a.product_id}`}>
                  <span className={`alert-tag ${a.kind}`}>⚠ {ALERT_LABEL[a.kind]}</span>
                  <span>
                    <strong>{a.name}</strong> <span className="muted">({a.sku})</span> — {a.message}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="card">
          <h2>Weekly AI summary</h2>
          <p className="muted">
            Optional. Only aggregated metrics are sent to the model — never orders or customer data.
          </p>
          <button className="secondary" onClick={loadSummary} disabled={summaryBusy}>
            {summaryBusy ? 'Generating…' : 'Generate summary'}
          </button>
          {summary && !summary.enabled && (
            <p className="muted">AI is disabled (no ANTHROPIC_API_KEY configured). Everything else works without it.</p>
          )}
          {summary?.summary && <p className="summary">{summary.summary}</p>}
        </div>
      </div>

      <div className="card">
        <h2>Real margin per product</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th className="num">Units</th>
                <th className="num">Revenue</th>
                <th className="num">Commission</th>
                <th className="num">Service fee</th>
                <th className="num">Shipping</th>
                <th className="num">Coupons</th>
                <th className="num">Product cost</th>
                <th className="num">Margin</th>
                <th className="num">Margin %</th>
              </tr>
            </thead>
            <tbody>
              {products.map((p) => (
                <tr key={p.product_id}>
                  <td>{p.name} <span className="muted">{p.sku}</span></td>
                  <td className="num">{p.units}</td>
                  <td className="num">{fmtMoney(p.revenue)}</td>
                  <td className="num">{fmtMoney(p.commission_fee)}</td>
                  <td className="num">{fmtMoney(p.service_fee)}</td>
                  <td className="num">{fmtMoney(p.shipping_fee)}</td>
                  <td className="num">{fmtMoney(p.voucher)}</td>
                  <td className="num">{p.product_cost === null ? <span className="muted">not set</span> : fmtMoney(p.product_cost)}</td>
                  <td className="num">{fmtMoney(p.margin)}</td>
                  <td className="num">{fmtPct(p.margin_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <h2>ABC curve</h2>
        <p className="muted">A: products making up ~80% of revenue · B: next 15% · C: the rest.</p>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Class</th>
                <th>Product</th>
                <th className="num">Revenue</th>
                <th className="num">Share</th>
                <th className="num">Cumulative</th>
              </tr>
            </thead>
            <tbody>
              {abc.map((i) => (
                <tr key={i.product_id}>
                  <td><span className={`badge ${i.abc_class}`}>{i.abc_class}</span></td>
                  <td>{i.name} <span className="muted">{i.sku}</span></td>
                  <td className="num">{fmtMoney(i.revenue)}</td>
                  <td className="num">{fmtPct(i.share_pct)}</td>
                  <td className="num">{fmtPct(i.cumulative_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
