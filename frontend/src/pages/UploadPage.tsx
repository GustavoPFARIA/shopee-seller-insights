import { useState, type FormEvent } from 'react'
import { api, ApiError, type UploadResult } from '../api'

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setResult(null)
    setError(null)
    try {
      setResult(await api.upload(file))
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, 'Could not reach the server'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <h2>Upload order report</h2>
      <p className="muted">
        Export your orders from Shopee Seller Centre (CSV or XLSX, up to 5 MB). Re-uploading the
        same report is safe: existing orders are never duplicated, only their status is updated.
        Buyer names, phones and addresses are discarded on import.
      </p>
      <form onSubmit={submit} className="filters">
        <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="primary" disabled={!file || busy}>
          {busy ? 'Importing…' : 'Import'}
        </button>
      </form>
      {result && (
        <p>
          Imported {result.row_count} rows: <strong>{result.orders_created}</strong> new orders,{' '}
          {result.orders_updated} updated, {result.orders_unchanged} unchanged,{' '}
          {result.products_created} new products.
        </p>
      )}
      {error && (
        <div className="error">
          <p>{error.message}</p>
          {error.details.length > 0 && (
            <ul>
              {error.details.map((d, i) => (
                <li key={i}>
                  Row {d.row}, column “{d.field}”: {d.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
