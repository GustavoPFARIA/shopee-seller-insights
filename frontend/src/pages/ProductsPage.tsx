import { useEffect, useState } from 'react'
import { api, ApiError, type Product } from '../api'
import ErrorDetails from '../components/ErrorDetails'

function ProductRow({ product, onSaved }: { product: Product; onSaved: (p: Product) => void }) {
  const [cost, setCost] = useState(product.unit_cost ?? '')
  const [stock, setStock] = useState(product.stock_quantity?.toString() ?? '')
  const [threshold, setThreshold] = useState(product.low_stock_threshold.toString())
  const [status, setStatus] = useState<string | null>(null)

  async function save() {
    setStatus('Saving…')
    try {
      const saved = await api.updateProduct(product.id, {
        unit_cost: cost === '' ? null : cost,
        stock_quantity: stock === '' ? null : Number(stock),
        low_stock_threshold: Number(threshold),
      })
      onSaved(saved)
      setStatus('Saved')
    } catch (e) {
      setStatus(e instanceof ApiError && e.status === 422 ? 'Invalid value' : 'Error')
    }
  }

  return (
    <tr>
      <td>{product.name} <span className="muted">{product.sku}</span></td>
      <td><input type="number" min="0" step="0.01" value={cost} placeholder="not set" onChange={(e) => setCost(e.target.value)} style={{ width: 100 }} /></td>
      <td><input type="number" min="0" step="1" value={stock} placeholder="not tracked" onChange={(e) => setStock(e.target.value)} style={{ width: 100 }} /></td>
      <td><input type="number" min="0" step="1" value={threshold} onChange={(e) => setThreshold(e.target.value)} style={{ width: 80 }} /></td>
      <td>
        <button className="secondary" onClick={save}>Save</button> <span className="muted">{status}</span>
      </td>
    </tr>
  )
}

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([])
  const [error, setError] = useState<string | null>(null)
  const [importError, setImportError] = useState<ApiError | null>(null)
  const [importMsg, setImportMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Remount rows after a bulk import so their inputs show the new values.
  const [version, setVersion] = useState(0)

  const load = () =>
    api
      .catalog()
      .then(setProducts)
      .catch(() => setError('Could not load products'))

  useEffect(() => {
    load()
  }, [])

  async function onImport(file: File | undefined) {
    if (!file) return
    setBusy(true)
    setImportError(null)
    setImportMsg(null)
    try {
      const r = await api.importCatalog(file)
      setImportMsg(`${r.updated} updated, ${r.created} created, ${r.unchanged} unchanged.`)
      await load()
      setVersion((v) => v + 1)
    } catch (e) {
      setImportError(e instanceof ApiError ? e : new ApiError(0, 'Import failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Products</h2>
      <p className="muted">
        Shopee reports do not include your product cost or stock. Fill them in to unlock real
        margin and stock alerts.
      </p>
      <div className="filters">
        <button className="secondary" onClick={() => api.downloadCatalog()}>
          Download spreadsheet
        </button>
        <label className="secondary file-button">
          {busy ? 'Importing…' : 'Import spreadsheet'}
          <input
            type="file"
            accept=".csv,.xlsx"
            hidden
            disabled={busy}
            onChange={(e) => {
              onImport(e.target.files?.[0])
              e.target.value = ''
            }}
          />
        </label>
        <span className="muted">Download, fill in cost and stock, upload. Empty cells are left unchanged.</span>
      </div>
      {importMsg && <p>{importMsg}</p>}
      {importError && <ErrorDetails error={importError} />}
      {error && <p className="error">{error}</p>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Product</th>
              <th>Unit cost (R$)</th>
              <th>Stock</th>
              <th>Low-stock threshold</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {products.map((p) => (
              <ProductRow
                key={`${p.id}-${version}`}
                product={p}
                onSaved={(saved) => setProducts((all) => all.map((x) => (x.id === saved.id ? saved : x)))}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
