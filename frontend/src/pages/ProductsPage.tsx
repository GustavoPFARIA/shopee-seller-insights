import { useEffect, useState } from 'react'
import { api, ApiError, type Product } from '../api'

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

  useEffect(() => {
    api.catalog().then(setProducts).catch(() => setError('Could not load products'))
  }, [])

  return (
    <div className="card">
      <h2>Products</h2>
      <p className="muted">
        Shopee reports do not include your product cost or stock. Fill them in to unlock real
        margin and stock alerts.
      </p>
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
                key={p.id}
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
