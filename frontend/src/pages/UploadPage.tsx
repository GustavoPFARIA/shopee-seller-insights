import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { api, ApiError, type UploadRecord, type UploadResult } from '../api'
import ErrorDetails from '../components/ErrorDetails'
import { useToast } from '../components/Toast'
import { fmtDateTime } from '../format'
import { navigate } from '../router'

const MAX_MB = 5

export default function UploadPage() {
  const notify = useToast()
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [history, setHistory] = useState<UploadRecord[]>([])
  const input = useRef<HTMLInputElement>(null)

  const loadHistory = useCallback(() => {
    api.uploads().then(setHistory).catch(() => undefined)
  }, [])
  useEffect(loadHistory, [loadHistory])

  async function send(file: File | undefined) {
    if (!file) return
    setResult(null)
    setError(null)
    if (!/\.(csv|xlsx)$/i.test(file.name)) {
      setError(new ApiError(0, 'Choose a .csv or .xlsx file exported from Shopee Seller Centre'))
      return
    }
    if (file.size > MAX_MB * 1024 * 1024) {
      setError(new ApiError(0, `The file is larger than ${MAX_MB} MB`))
      return
    }
    setBusy(true)
    try {
      const r = await api.upload(file)
      setResult(r)
      notify(`${r.orders_created} new orders imported`)
      loadHistory()
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, 'Could not reach the server'))
    } finally {
      setBusy(false)
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    void send(e.dataTransfer.files[0])
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Upload order report</h2>
          <p className="muted">Import orders exported from Shopee Seller Centre (CSV or XLSX, up to {MAX_MB} MB).</p>
        </div>
      </div>

      <div className="grid-2 wide-left">
        <section className="card">
          <div
            className={`dropzone ${dragging ? 'over' : ''} ${busy ? 'busy' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            onClick={() => !busy && input.current?.click()}
            onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
            role="button"
            tabIndex={0}
            aria-label="Choose an order report to upload"
          >
            <div className="drop-icon" aria-hidden>
              ⬆️
            </div>
            <strong>{busy ? 'Importing…' : 'Drop the report here or click to choose'}</strong>
            <span className="muted small">.csv or .xlsx · re-uploading the same report never duplicates orders</span>
            <input
              ref={input}
              type="file"
              accept=".csv,.xlsx"
              hidden
              onChange={(e) => {
                void send(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </div>

          {result && (
            <div className="result ok-box">
              <strong>Import finished</strong>
              <ul>
                <li>{result.row_count} rows read</li>
                <li>{result.orders_created} new orders</li>
                <li>{result.orders_updated} orders with a new status</li>
                <li>{result.orders_unchanged} orders already up to date</li>
                <li>{result.products_created} new products (add their cost in Products)</li>
              </ul>
              <div className="toolbar">
                <button className="primary" onClick={() => navigate('/')}>
                  See dashboard
                </button>
                {result.products_created > 0 && (
                  <button className="secondary" onClick={() => navigate('/products?filter=missing_cost')}>
                    Add costs
                  </button>
                )}
              </div>
            </div>
          )}
          {error && <ErrorDetails error={error} />}
        </section>

        <section className="card">
          <h3>How to export from Shopee</h3>
          <ol className="steps small-steps">
            <li>Open Seller Centre → My Orders.</li>
            <li>Choose the period and click Export.</li>
            <li>Download the file when it is ready and drop it here.</li>
          </ol>
          <p className="muted small">
            Portuguese (Brazil) and English headers are supported. Buyer names, phones and addresses
            are discarded on import; buyer usernames are stored only as an irreversible hash.
          </p>
          <p className="muted small">Tip: connect your shop in Integrations to skip manual uploads.</p>
        </section>
      </div>

      <section className="card">
        <h3>Upload history</h3>
        {history.length === 0 ? (
          <p className="muted">No uploads yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>File</th>
                  <th className="num">Rows</th>
                  <th className="num">New</th>
                  <th className="num">Updated</th>
                  <th className="num">Unchanged</th>
                </tr>
              </thead>
              <tbody>
                {history.map((u) => (
                  <tr key={u.id}>
                    <td>{fmtDateTime(u.created_at)}</td>
                    <td>{u.filename}</td>
                    <td className="num">{u.row_count}</td>
                    <td className="num">{u.orders_created}</td>
                    <td className="num">{u.orders_updated}</td>
                    <td className="num">{u.orders_unchanged}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  )
}
