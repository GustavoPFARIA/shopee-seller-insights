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
  type AbcItem,
  type AiSummary,
  type Alert,
  type AlertKind,
  type DailyPoint,
  type Overview,
  type Period,
  type Product,
  type ProductMetrics,
  type ShopeeStatus,
  type UploadRecord,
} from '../api'
import EmptyState from '../components/EmptyState'
import KpiCard from '../components/KpiCard'
import { useToast } from '../components/Toast'
import { fmtAgo, fmtCompact, fmtMoney, fmtPct, lastDays } from '../format'
import { navigate } from '../router'
import { useSession } from '../session'

const PRESETS = [7, 30, 90] as const
const ALERT_LABEL: Record<AlertKind, string> = {
  low_stock: 'Low stock',
  stalled_product: 'Stalled',
  low_margin: 'Low margin',
  high_returns: 'High returns',
}
const axisTick = { fill: 'var(--text-muted)', fontSize: 12 }
const tooltipStyle = { background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8 }

interface Data {
  overview: Overview
  daily: DailyPoint[]
  products: ProductMetrics[]
  abc: AbcItem[]
  alerts: Alert[]
  catalog: Product[]
  uploads: UploadRecord[]
  shopee: ShopeeStatus | null
}

export default function Dashboard() {
  const { canEdit } = useSession()
  const notify = useToast()
  const [days, setDays] = useState<number | null>(30)
  const [period, setPeriod] = useState<Period>(() => lastDays(30))
  const [data, setData] = useState<Data | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.overview(period),
      api.daily(period),
      api.products(period),
      api.abc(period),
      api.alerts(),
      api.catalog(),
      api.uploads(),
      api.shopeeStatus().catch(() => null),
    ])
      .then(([overview, daily, products, abc, alerts, catalog, uploads, shopee]) => {
        if (!cancelled) {
          setData({ overview, daily, products, abc, alerts, catalog, uploads, shopee })
          setError(null)
        }
      })
      .catch((e) => !cancelled && setError(e instanceof ApiError ? e.message : 'Could not load the dashboard'))
    return () => {
      cancelled = true
    }
  }, [period])

  const choose = (n: number) => {
    setDays(n)
    setPeriod(lastDays(n))
  }

  if (error) return <p className="error">{error}</p>
  if (!data) return <DashboardSkeleton />

  const hasAnyData = data.catalog.length > 0
  if (!hasAnyData) return <Onboarding canEdit={canEdit} />

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Dashboard</h2>
          <p className="muted">
            {period.start} to {period.end}, compared with the {days ?? 'same number of'} days before
          </p>
        </div>
        <div className="toolbar" role="group" aria-label="Period">
          {PRESETS.map((n) => (
            <button key={n} className={`chip ${days === n ? 'on' : ''}`} onClick={() => choose(n)}>
              {n} days
            </button>
          ))}
          <input
            type="date"
            aria-label="Start date"
            value={period.start}
            max={period.end}
            onChange={(e) => e.target.value && (setDays(null), setPeriod({ ...period, start: e.target.value }))}
          />
          <input
            type="date"
            aria-label="End date"
            value={period.end}
            min={period.start}
            onChange={(e) => e.target.value && (setDays(null), setPeriod({ ...period, end: e.target.value }))}
          />
          <button
            className="secondary"
            onClick={() => api.exportCsv(period).catch(() => notify('Export failed', 'error'))}
          >
            Export CSV
          </button>
        </div>
      </div>

      <DataHealth data={data} />
      <Kpis overview={data.overview} />

      <div className="grid-2">
        <section className="card">
          <h3>Daily revenue</h3>
          <RevenueChart daily={data.daily} />
        </section>
        <section className="card">
          <h3>Top products by revenue</h3>
          <TopProductsChart products={data.products} />
        </section>
      </div>

      <div className="grid-2">
        <AlertsCard alerts={data.alerts} />
        {canEdit ? <AiSummaryCard /> : <AbcCard abc={data.abc} />}
      </div>

      <ProductsTable products={data.products} abc={data.abc} />
      {canEdit && <AbcCard abc={data.abc} />}
    </>
  )
}

function DashboardSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading dashboard">
      <div className="grid-kpi">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card kpi skeleton" />
        ))}
      </div>
      <div className="grid-2">
        <div className="card skeleton tall" />
        <div className="card skeleton tall" />
      </div>
    </div>
  )
}

function Onboarding({ canEdit }: { canEdit: boolean }) {
  return (
    <div className="card">
      <EmptyState title="Welcome! Let's get your shop's numbers in">
        <ol className="steps">
          <li>
            <strong>Bring in your orders.</strong> Upload the order report from Shopee Seller Centre
            (My Orders → Export), or connect your shop to sync automatically.
            <div className="toolbar">
              {canEdit && (
                <button className="primary" onClick={() => navigate('/upload')}>
                  Upload a report
                </button>
              )}
              <button className="secondary" onClick={() => navigate('/integrations')}>
                Connect Shopee
              </button>
            </div>
          </li>
          <li>
            <strong>Add your product costs.</strong> Shopee does not know what you paid for each
            product; with costs the app shows your real margin.
          </li>
          <li>
            <strong>Check alerts every day.</strong> Low stock, products that stopped selling, thin
            margins and high return rates appear on this page.
          </li>
        </ol>
        {!canEdit && <p className="muted">You have read-only access: ask an owner to add data.</p>}
      </EmptyState>
    </div>
  )
}

function DataHealth({ data }: { data: Data }) {
  const missingCost = data.catalog.filter((p) => p.unit_cost === null).length
  const lastUpload = data.uploads[0]?.created_at ?? null
  const lastSync = data.shopee?.orders_synced_until ?? null
  const latest = [lastUpload, lastSync].filter(Boolean).sort().at(-1) ?? null
  const source = latest && latest === lastSync ? 'Shopee sync' : 'upload'
  return (
    <div className={`health ${missingCost > 0 ? 'attention' : ''}`} role="note">
      <span>
        Data updated <strong>{fmtAgo(latest)}</strong>
        {latest && <span className="muted"> ({source})</span>}
      </span>
      {missingCost > 0 ? (
        <span className="warn">
          {missingCost} product{missingCost === 1 ? '' : 's'} without cost: net margin is incomplete.{' '}
          <a
            href="/products?filter=missing_cost"
            onClick={(e) => {
              e.preventDefault()
              navigate('/products?filter=missing_cost')
            }}
          >
            Add costs
          </a>
        </span>
      ) : (
        <span className="ok">All products have a cost: margins are complete.</span>
      )}
    </div>
  )
}

function Kpis({ overview }: { overview: Overview }) {
  const { current, change } = overview
  const marginPct =
    current.net_margin !== null && Number(current.revenue) > 0
      ? (Number(current.net_margin) / Number(current.revenue)) * 100
      : null
  return (
    <div className="grid-kpi">
      <KpiCard label="Revenue" value={fmtMoney(current.revenue)} delta={change.revenue_pct} />
      <KpiCard label="Orders" value={String(current.orders)} delta={change.orders_pct} hint={`${current.units} units`} />
      <KpiCard label="Average ticket" value={fmtMoney(current.avg_ticket)} delta={change.avg_ticket_pct} />
      <KpiCard
        label="Net margin"
        value={current.net_margin === null ? 'Incomplete' : fmtMoney(current.net_margin)}
        hint={
          current.net_margin === null
            ? 'Some sold products have no cost'
            : `${fmtPct(marginPct)} of revenue after fees and costs`
        }
      />
    </div>
  )
}

function RevenueChart({ daily }: { daily: DailyPoint[] }) {
  const points = useMemo(
    () => daily.map((d) => ({ day: d.day.slice(5), revenue: Number(d.revenue), orders: d.orders })),
    [daily],
  )
  if (points.length === 0) return <p className="muted">No sales in this period.</p>
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="day" tick={axisTick} tickLine={false} axisLine={false} minTickGap={24} />
        <YAxis tick={axisTick} tickLine={false} axisLine={false} width={48} tickFormatter={fmtCompact} />
        <Tooltip
          formatter={(v, name) => (name === 'revenue' ? [fmtMoney(Number(v)), 'Revenue'] : [v, 'Orders'])}
          contentStyle={tooltipStyle}
          labelStyle={{ color: 'var(--text-secondary)' }}
          cursor={{ stroke: 'var(--text-muted)', strokeDasharray: '3 3' }}
        />
        <Area type="monotone" dataKey="revenue" stroke="var(--series-1)" strokeWidth={2} fill="var(--series-1)" fillOpacity={0.12} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

function TopProductsChart({ products }: { products: ProductMetrics[] }) {
  const top = products.filter((p) => p.units > 0).slice(0, 8).map((p) => ({ name: p.name, revenue: Number(p.revenue) }))
  if (top.length === 0) return <p className="muted">No sales in this period.</p>
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={top} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" horizontal={false} />
        <XAxis type="number" tick={axisTick} tickLine={false} axisLine={false} tickFormatter={fmtCompact} />
        <YAxis type="category" dataKey="name" tick={axisTick} tickLine={false} axisLine={false} width={150} />
        <Tooltip formatter={(v) => [fmtMoney(Number(v)), 'Revenue']} contentStyle={tooltipStyle} cursor={{ fill: 'var(--grid)' }} />
        <Bar dataKey="revenue" fill="var(--series-1)" radius={[0, 4, 4, 0]} barSize={14} />
      </BarChart>
    </ResponsiveContainer>
  )
}

function AlertsCard({ alerts }: { alerts: Alert[] }) {
  const open = (sku: string) => navigate(`/products?search=${encodeURIComponent(sku)}`)
  return (
    <section className="card">
      <h3>
        Alerts <span className="count">{alerts.length}</span>
      </h3>
      {alerts.length === 0 ? (
        <p className="ok">Nothing needs your attention.</p>
      ) : (
        <ul className="list">
          {alerts.slice(0, 10).map((a) => (
            <li key={`${a.kind}-${a.product_id}`}>
              <span className={`tag ${a.kind}`}>⚠ {ALERT_LABEL[a.kind]}</span>
              <span className="grow">
                <button className="link-button strong" onClick={() => open(a.sku)}>
                  {a.name}
                </button>{' '}
                <span className="muted">{a.message}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {alerts.length > 10 && <p className="muted small">and {alerts.length - 10} more</p>}
      <p className="muted small">Thresholds are set in Settings.</p>
    </section>
  )
}

function AiSummaryCard() {
  const [summary, setSummary] = useState<AiSummary | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const load = async () => {
    setBusy(true)
    setError(null)
    try {
      setSummary(await api.summary())
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Could not generate the summary')
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="card">
      <h3>Weekly AI summary</h3>
      <p className="muted small">
        Only aggregated numbers are sent to the model, never orders or customer data.
      </p>
      <button className="secondary" onClick={load} disabled={busy}>
        {busy ? 'Writing…' : 'Generate summary'}
      </button>
      {error && <p className="error">{error}</p>}
      {summary && !summary.enabled && (
        <p className="muted">
          AI is not configured on this server (ANTHROPIC_API_KEY). Everything else works without it.
        </p>
      )}
      {summary?.summary && <p className="summary">{summary.summary}</p>}
    </section>
  )
}

function ProductsTable({ products, abc }: { products: ProductMetrics[]; abc: AbcItem[] }) {
  const classOf = new Map(abc.map((a) => [a.product_id, a.abc_class]))
  const rows = products.slice(0, 8)
  return (
    <section className="card">
      <div className="card-head">
        <h3>Product performance</h3>
        <button className="link-button" onClick={() => navigate('/products')}>
          See all products →
        </button>
      </div>
      {rows.length === 0 ? (
        <p className="muted">No sales in this period.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Product</th>
                <th>ABC</th>
                <th className="num">Units</th>
                <th className="num">Revenue</th>
                <th className="num">Fees</th>
                <th className="num">Cost</th>
                <th className="num">Margin</th>
                <th className="num">Margin %</th>
                <th className="num">Returns</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((p) => {
                const fees = Number(p.commission_fee) + Number(p.service_fee) + Number(p.shipping_fee) + Number(p.voucher)
                const cls = classOf.get(p.product_id)
                return (
                  <tr key={p.product_id}>
                    <td>
                      {p.name} <span className="muted small">{p.sku}</span>
                    </td>
                    <td>{cls && <span className={`badge ${cls}`}>{cls}</span>}</td>
                    <td className="num">{p.units}</td>
                    <td className="num">{fmtMoney(p.revenue)}</td>
                    <td className="num">{fmtMoney(fees)}</td>
                    <td className="num">{p.product_cost === null ? <span className="warn">not set</span> : fmtMoney(p.product_cost)}</td>
                    <td className={`num ${p.margin !== null && Number(p.margin) < 0 ? 'neg' : ''}`}>{fmtMoney(p.margin)}</td>
                    <td className="num">{fmtPct(p.margin_pct)}</td>
                    <td className="num">{fmtPct(p.return_rate_pct)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function AbcCard({ abc }: { abc: AbcItem[] }) {
  const counts = { A: 0, B: 0, C: 0 }
  const revenue = { A: 0, B: 0, C: 0 }
  for (const i of abc) {
    counts[i.abc_class] += 1
    revenue[i.abc_class] += i.share_pct
  }
  return (
    <section className="card">
      <h3>ABC curve</h3>
      <p className="muted small">Where your revenue comes from: protect class A stock first.</p>
      <div className="abc-row">
        {(['A', 'B', 'C'] as const).map((c) => (
          <div key={c} className="abc-cell">
            <span className={`badge ${c}`}>{c}</span>
            <div>
              <strong>{counts[c]}</strong> products
            </div>
            <div className="muted small">{fmtPct(revenue[c], 0)} of revenue</div>
          </div>
        ))}
      </div>
      <button className="link-button" onClick={() => navigate('/products?filter=class_a')}>
        See class A products →
      </button>
    </section>
  )
}
