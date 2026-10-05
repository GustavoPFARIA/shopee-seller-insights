import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type Me, type ShopeeStatus } from '../api'

const CALLBACK_MESSAGES: Record<string, string> = {
  connected: 'Shop connected. The first synchronization imports the last 90 days.',
  'error:invalid_state': 'The authorization link expired or was already used. Please try again.',
  'error:shop_already_connected': 'This Shopee shop is already linked to another account.',
  'error:exchange_failed': 'Shopee did not accept the authorization. Please try again.',
  'error:missing_params': 'Shopee returned an incomplete response. Please try again.',
}

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

export default function ShopeePage({ me, callback }: { me: Me; callback: string | null }) {
  const [status, setStatus] = useState<ShopeeStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const notice = callback ? (CALLBACK_MESSAGES[callback] ?? 'Unexpected response from Shopee.') : null

  const load = useCallback(() => {
    api.shopeeStatus().then(setStatus).catch(() => setError('Could not load the integration status'))
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Request failed')
    } finally {
      setBusy(false)
      load()
    }
  }

  const connect = () =>
    act(async () => {
      const { authorization_url } = await api.shopeeConnect()
      window.location.assign(authorization_url)
    })

  if (!status) return <p className="muted">{error ?? 'Loading…'}</p>
  const isOwner = me.role === 'owner'
  const canSync = me.role !== 'viewer'

  return (
    <>
      {notice && <p className={callback === 'connected' ? 'card' : 'card error'}>{notice}</p>}
      <div className="card">
        <h2>Shopee Open Platform</h2>
        {!status.enabled ? (
          <p className="muted">
            This server has no Shopee partner credentials. Set SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY and
            TOKEN_ENCRYPTION_KEY to enable automatic sync. Manual uploads keep working.
          </p>
        ) : !status.connected ? (
          <>
            <p>
              Connect your shop to import orders, exact fees (from the escrow statement) and stock
              automatically every {status.sync_interval_minutes} minutes. Buyer addresses are never requested.
            </p>
            {isOwner ? (
              <button className="primary" onClick={connect} disabled={busy}>
                Connect Shopee shop
              </button>
            ) : (
              <p className="muted">Ask a shop owner to connect the Shopee account.</p>
            )}
          </>
        ) : (
          <>
            <p>
              Connected shop <strong>{status.shop_id}</strong> since {fmt(status.connected_at)}. Orders synced
              up to {fmt(status.orders_synced_until)}; automatic sync every {status.sync_interval_minutes} minutes.
            </p>
            <div className="filters">
              {canSync && (
                <button className="primary" onClick={() => act(api.shopeeSync)} disabled={busy}>
                  {busy ? 'Syncing…' : 'Sync now'}
                </button>
              )}
              {isOwner && (
                <button
                  className="secondary"
                  disabled={busy}
                  onClick={() => window.confirm('Disconnect this Shopee shop?') && act(api.shopeeDisconnect)}
                >
                  Disconnect
                </button>
              )}
            </div>
          </>
        )}
        {error && <p className="error">{error}</p>}
      </div>

      {status.runs.length > 0 && (
        <div className="card">
          <h2>Recent synchronizations</h2>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Started</th>
                  <th>Trigger</th>
                  <th>Status</th>
                  <th className="num">New orders</th>
                  <th className="num">Updated</th>
                  <th className="num">Skipped</th>
                  <th className="num">Stock updates</th>
                  <th>Error</th>
                </tr>
              </thead>
              <tbody>
                {status.runs.map((r) => (
                  <tr key={r.id}>
                    <td>{fmt(r.started_at)}</td>
                    <td>{r.trigger}</td>
                    <td className={r.status === 'error' ? 'error' : ''}>{r.status}</td>
                    <td className="num">{r.orders_created}</td>
                    <td className="num">{r.orders_updated}</td>
                    <td className="num">{r.orders_skipped}</td>
                    <td className="num">{r.products_stock_updated}</td>
                    <td className="muted">{r.error ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </>
  )
}
