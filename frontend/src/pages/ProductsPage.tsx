import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { api, ApiError, type Product } from '../api'
import EmptyState from '../components/EmptyState'
import ErrorDetails from '../components/ErrorDetails'
import Modal from '../components/Modal'
import { useToast } from '../components/Toast'
import { fmtMoney, fmtPct, lastDays } from '../format'
import {
  buildRows,
  countByFilter,
  FILTER_LABELS,
  isLowStock,
  selectRows,
  type ProductFilter,
  type ProductRow,
  type ProductSort,
} from '../productTable'
import { queryParam } from '../router'
import { useSession } from '../session'

const FILTERS = Object.keys(FILTER_LABELS) as ProductFilter[]
const SORTS: Record<ProductSort, string> = {
  revenue: 'Revenue',
  margin_pct: 'Margin %',
  units: 'Units sold',
  stock: 'Stock (lowest first)',
  name: 'Name',
}

export default function ProductsPage() {
  const { canEdit, settings } = useSession()
  const notify = useToast()
  const [rows, setRows] = useState<ProductRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<ProductFilter>(() => {
    const f = queryParam('filter') as ProductFilter | null
    return f && f in FILTER_LABELS ? f : 'all'
  })
  const [search, setSearch] = useState(() => queryParam('search') ?? '')
  const [sort, setSort] = useState<ProductSort>('revenue')
  const [editing, setEditing] = useState<Product | null>(null)
  const [importError, setImportError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const maxReturn = Number(settings?.max_return_rate_pct ?? 10)

  const load = useCallback(() => {
    const period = lastDays(30)
    return Promise.all([api.catalog(), api.products(period), api.abc(period)])
      .then(([catalog, metrics, abc]) => setRows(buildRows(catalog, metrics, abc)))
      .catch(() => setError('Could not load products'))
  }, [])

  useEffect(() => {
    load().catch(() => undefined)
  }, [load])

  const counts = useMemo(() => (rows ? countByFilter(rows, maxReturn) : null), [rows, maxReturn])
  const visible = useMemo(
    () => (rows ? selectRows(rows, { filter, search, sort, maxReturnRatePct: maxReturn }) : []),
    [rows, filter, search, sort, maxReturn],
  )

  async function onImport(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setImportError(null)
    try {
      const r = await api.importCatalog(file)
      notify(`Spreadsheet imported: ${r.updated} updated, ${r.created} created, ${r.unchanged} unchanged`)
      await load()
    } catch (e) {
      setImportError(e instanceof ApiError ? e : new ApiError(0, 'Import failed'))
    } finally {
      setBusy(false)
    }
  }

  if (error) return <p className="error">{error}</p>

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Products</h2>
          <p className="muted">Sales of the last 30 days, with your cost and stock.</p>
        </div>
        <div className="toolbar">
          <button className="secondary" onClick={() => api.downloadCatalog().catch(() => notify('Download failed', 'error'))}>
            Download spreadsheet
          </button>
          {canEdit && (
            <label className={`button primary ${busy ? 'disabled' : ''}`}>
              {busy ? 'Importing…' : 'Import spreadsheet'}
              <input
                type="file"
                accept=".csv,.xlsx"
                hidden
                disabled={busy}
                onChange={(e) => {
                  void onImport(e.target.files?.[0])
                  e.target.value = ''
                }}
              />
            </label>
          )}
        </div>
      </div>
      {canEdit && (
        <p className="muted small">
          Shopee reports do not include cost or stock. Download the spreadsheet, fill in{' '}
          <code>unit_cost</code> and <code>stock_quantity</code> (empty cells are left unchanged)
          and import it back, or edit one product at a time.
        </p>
      )}
      {importError && <ErrorDetails error={importError} />}

      <div className="card">
        <div className="filters-row">
          <div className="chips" role="group" aria-label="Filter products">
            {FILTERS.map((f) => (
              <button key={f} className={`chip ${filter === f ? 'on' : ''}`} onClick={() => setFilter(f)}>
                {FILTER_LABELS[f]}
                {counts && <span className="chip-count">{counts[f]}</span>}
              </button>
            ))}
          </div>
          <div className="toolbar">
            <input
              type="search"
              placeholder="Search name or SKU"
              aria-label="Search products"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            <select aria-label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as ProductSort)}>
              {Object.entries(SORTS).map(([k, label]) => (
                <option key={k} value={k}>
                  Sort: {label}
                </option>
              ))}
            </select>
          </div>
        </div>

        {rows === null ? (
          <div className="skeleton tall" aria-busy="true" />
        ) : rows.length === 0 ? (
          <EmptyState title="No products yet">
            <p>Products appear after your first order upload or Shopee sync.</p>
          </EmptyState>
        ) : visible.length === 0 ? (
          <EmptyState title="No product matches">
            <p>Try another filter or search term.</p>
          </EmptyState>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Product</th>
                  <th>ABC</th>
                  <th className="num">Units</th>
                  <th className="num">Revenue</th>
                  <th className="num">Margin %</th>
                  <th className="num">Returns</th>
                  <th className="num">Unit cost</th>
                  <th className="num">Stock</th>
                  {canEdit && <th aria-label="Actions" />}
                </tr>
              </thead>
              <tbody>
                {visible.map(({ product: p, metrics: m, abc }) => (
                  <tr key={p.id}>
                    <td>
                      <div>{p.name}</div>
                      <div className="muted small">{p.sku}</div>
                    </td>
                    <td>{abc && <span className={`badge ${abc}`}>{abc}</span>}</td>
                    <td className="num">{m?.units ?? 0}</td>
                    <td className="num">{fmtMoney(m?.revenue ?? '0')}</td>
                    <td className={`num ${m?.margin_pct !== null && m?.margin_pct !== undefined && m.margin_pct < 0 ? 'neg' : ''}`}>
                      {fmtPct(m?.margin_pct)}
                    </td>
                    <td className={`num ${(m?.return_rate_pct ?? 0) > maxReturn ? 'warn' : ''}`}>{fmtPct(m?.return_rate_pct)}</td>
                    <td className="num">{p.unit_cost === null ? <span className="warn">not set</span> : fmtMoney(p.unit_cost)}</td>
                    <td className={`num ${isLowStock(p) ? 'warn' : ''}`}>
                      {p.stock_quantity === null ? <span className="muted">not tracked</span> : p.stock_quantity}
                    </td>
                    {canEdit && (
                      <td>
                        <button className="secondary small-button" onClick={() => setEditing(p)}>
                          Edit
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {editing && (
        <EditProduct
          product={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            notify('Product saved')
            void load()
          }}
        />
      )}
    </>
  )
}

function EditProduct({
  product,
  onClose,
  onSaved,
}: {
  product: Product
  onClose: () => void
  onSaved: () => void
}) {
  const [cost, setCost] = useState(product.unit_cost ?? '')
  const [stock, setStock] = useState(product.stock_quantity?.toString() ?? '')
  const [threshold, setThreshold] = useState(String(product.low_stock_threshold))
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.updateProduct(product.id, {
        unit_cost: cost === '' ? null : cost,
        stock_quantity: stock === '' ? null : Number(stock),
        low_stock_threshold: Number(threshold),
      })
      onSaved()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save')
      setBusy(false)
    }
  }

  return (
    <Modal title={`Edit ${product.name}`} onClose={onClose}>
      <form className="form" onSubmit={save}>
        <p className="muted small">SKU {product.sku}</p>
        <label>
          Unit cost (R$)
          <input type="number" min="0" step="0.01" value={cost} placeholder="Not set" onChange={(e) => setCost(e.target.value)} />
          <span className="hint">What you pay per unit. Needed for the real margin.</span>
        </label>
        <label>
          Stock
          <input type="number" min="0" step="1" value={stock} placeholder="Not tracked" onChange={(e) => setStock(e.target.value)} />
          <span className="hint">Leave empty if you do not track stock here.</span>
        </label>
        <label>
          Low-stock alert at
          <input type="number" min="0" step="1" required value={threshold} onChange={(e) => setThreshold(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="toolbar end">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
      </form>
    </Modal>
  )
}
