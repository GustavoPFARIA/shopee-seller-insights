import { useCallback, useEffect, useState } from 'react'
import { api, ApiError, type ShopeeStatus } from '../api'
import { useToast } from '../components/Toast'
import { fmtAgo, fmtDateTime } from '../format'
import { navigate } from '../router'
import { useSession } from '../session'

const CALLBACK_MESSAGES: Record<string, string> = {
  connected: 'Shop connected. The first synchronization imports the last 90 days.',
  'error:invalid_state':
    'The authorization link expired, was already used, or was opened in another browser. Please try again.',
  'error:shop_already_connected': 'This Shopee shop is already linked to another account.',
  'error:exchange_failed': 'Shopee did not accept the authorization. Please try again.',
  'error:missing_params': 'Shopee returned an incomplete response. Please try again.',
}
const TRIGGER_LABEL = { manual: 'Sync now', scheduled: 'Scheduled', push: 'Shopee notification' }

export default function IntegrationsPage({ callback }: { callback: string | null }) {
  const { settings } = useSession()
  return (
    <>
      <div className="page-head">
        <div>
          <h2>Integrations</h2>
          <p className="muted">Connect services that keep your numbers up to date.</p>
        </div>
      </div>
      <ShopeeCard callback={callback} />
      <div className="grid-2">
        <section className="card">
          <h3>E-mail</h3>
          {settings?.email_available ? (
            <p className="ok">Configured. Invitations and the weekly summary are sent by e-mail.</p>
          ) : (
            <p className="muted">
              Not configured on this server (SMTP_HOST). Invitation links are shown on screen to
              copy, and the weekly summary is available on the dashboard.
            </p>
          )}
          <button className="link-button" onClick={() => navigate('/settings')}>
            Weekly e-mail settings →
          </button>
        </section>
        <section className="card">
          <h3>AI weekly summary</h3>
          <p className="muted">
            Written by Claude from aggregated numbers only (no orders or customer data). Enabled
            when the server has an ANTHROPIC_API_KEY; generate it from the dashboard.
          </p>
        </section>
      </div>
    </>
  )
}

function ShopeeCard({ callback }: { callback: string | null }) {
  const { isOwner, canEdit } = useSession()
  const notify = useToast()
  const [status, setStatus] = useState<ShopeeStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const notice = callback ? (CALLBACK_MESSAGES[callback] ?? 'Unexpected response from Shopee.') : null

  const load = useCallback(() => {
    api.shopeeStatus().then(setStatus).catch(() => setError('Could not load the integration status'))
  }, [])
  useEffect(load, [load])

  // Syncs run in the background worker: refresh while one is queued or running.
  const inProgress = status?.runs.some((r) => r.status === 'queued' || r.status === 'running') ?? false
  useEffect(() => {
    if (!inProgress) return
    const timer = window.setTimeout(load, 3000)
    return () => window.clearTimeout(timer)
  }, [inProgress, status, load])

  const act = async (action: () => Promise<unknown>, done?: string) => {
    setBusy(true)
    setError(null)
    try {
      await action()
      if (done) notify(done)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Request failed')
    } finally {
      setBusy(false)
      load()
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h3>Shopee Open Platform</h3>
        {status?.connected && <span className="tag ok-tag">Connected</span>}
      </div>
      {notice && <p className={callback === 'connected' ? 'ok-box' : 'error-box'}>{notice}</p>}
      {!status ? (
        <p className="muted">{error ?? 'Loading…'}</p>
      ) : !status.enabled ? (
        <p className="muted">
          This server has no Shopee partner credentials (SHOPEE_PARTNER_ID, SHOPEE_PARTNER_KEY,
          TOKEN_ENCRYPTION_KEY). Uploading the Seller Centre report keeps working. To try the
          integration locally, start the stack with <code>docker-compose.shopee-demo.yml</code>.
        </p>
      ) : !status.connected ? (
        <>
          <p>
            Connect your shop to import orders, exact Shopee fees (from the payment statement) and
            stock automatically: on every Shopee order notification and every{' '}
            {status.sync_interval_minutes} minutes. Buyer addresses are never requested.
          </p>
          {isOwner ? (
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                act(async () => {
                  const { authorization_url } = await api.shopeeConnect()
                  window.location.assign(authorization_url)
                })
              }
            >
              Connect Shopee shop
            </button>
          ) : (
            <p className="muted">Ask a shop owner to connect the Shopee account.</p>
          )}
        </>
      ) : (
        <>
          <dl className="facts">
            <div>
              <dt>Shop ID</dt>
              <dd>{status.shop_id}</dd>
            </div>
            <div>
              <dt>Connected</dt>
              <dd>{fmtDateTime(status.connected_at)}</dd>
            </div>
            <div>
              <dt>Orders synced</dt>
              <dd>{fmtAgo(status.orders_synced_until)}</dd>
            </div>
            <div>
              <dt>Automatic sync</dt>
              <dd>every {status.sync_interval_minutes} min + Shopee notifications</dd>
            </div>
          </dl>
          <div className="toolbar">
            {canEdit && (
              <button className="primary" onClick={() => act(api.shopeeSync, 'Sync queued')} disabled={busy || inProgress}>
                {inProgress ? 'Syncing…' : 'Sync now'}
              </button>
            )}
            {isOwner && status.runs[0]?.error === 'reauthorization_required' && (
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const { authorization_url } = await api.shopeeConnect()
                    window.location.assign(authorization_url)
                  })
                }
              >
                Reconnect
              </button>
            )}
            {isOwner && (
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  window.confirm('Disconnect this Shopee shop? Imported data is kept.') &&
                  act(api.shopeeDisconnect, 'Shop disconnected')
                }
              >
                Disconnect
              </button>
            )}
          </div>
        </>
      )}
      {error && status && <p className="error">{error}</p>}

      {status && status.runs.length > 0 && (
        <>
          <h4>Recent synchronizations</h4>
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
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {status.runs.map((r) => (
                  <tr key={r.id}>
                    <td>{fmtDateTime(r.started_at)}</td>
                    <td>{TRIGGER_LABEL[r.trigger]}</td>
                    <td>
                      <span className={`tag run-${r.status}`}>{r.status}</span>
                    </td>
                    <td className="num">{r.orders_created}</td>
                    <td className="num">{r.orders_updated}</td>
                    <td className="num">{r.orders_skipped}</td>
                    <td className="num">{r.products_stock_updated}</td>
                    <td className="muted small">
                      {r.error === 'reauthorization_required'
                        ? 'Shopee access expired (30 days): an owner must reconnect the shop.'
                        : (r.error ?? '')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  )
}
