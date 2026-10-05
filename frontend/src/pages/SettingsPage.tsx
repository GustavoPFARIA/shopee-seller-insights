import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import { useToast } from '../components/Toast'
import { useSession } from '../session'

export default function SettingsPage() {
  const { settings, canEdit, reload, switchShop } = useSession()
  const notify = useToast()
  const [name, setName] = useState(settings?.name ?? '')
  const [stalledDays, setStalledDays] = useState(String(settings?.stalled_days ?? 30))
  const [minMargin, setMinMargin] = useState(settings?.min_margin_pct ?? '15')
  const [maxReturns, setMaxReturns] = useState(settings?.max_return_rate_pct ?? '10')
  const [weekly, setWeekly] = useState(settings?.weekly_email ?? false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [newShop, setNewShop] = useState('')

  if (!settings) return <p className="muted">Loading…</p>

  async function save(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.updateSettings({
        name,
        stalled_days: Number(stalledDays),
        min_margin_pct: minMargin,
        max_return_rate_pct: maxReturns,
        weekly_email: weekly,
      })
      await reload()
      notify('Settings saved')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save')
    } finally {
      setBusy(false)
    }
  }

  async function preview() {
    try {
      await api.sendDigestPreview()
      notify('Preview sent to your e-mail')
    } catch (err) {
      notify(err instanceof ApiError ? err.message : 'Could not send', 'error')
    }
  }

  async function createShop(e: FormEvent) {
    e.preventDefault()
    try {
      const shop = await api.createShop(newShop)
      notify(`Shop "${shop.name}" created`)
      setNewShop('')
      switchShop(shop.id)
    } catch (err) {
      notify(err instanceof ApiError ? err.message : 'Could not create the shop', 'error')
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Settings</h2>
          <p className="muted">Applies to this shop only{canEdit ? '' : ' (read-only for viewers)'}.</p>
        </div>
      </div>
      <form className="card form" onSubmit={save}>
        <fieldset disabled={!canEdit || busy}>
          <h3>Shop</h3>
          <label>
            Shop name
            <input required minLength={2} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
          </label>

          <h3>Alerts</h3>
          <div className="grid-3">
            <label>
              Stalled after (days without sales)
              <input type="number" min={1} max={365} required value={stalledDays} onChange={(e) => setStalledDays(e.target.value)} />
            </label>
            <label>
              Minimum margin (%)
              <input type="number" min={-100} max={100} step="0.5" required value={minMargin} onChange={(e) => setMinMargin(e.target.value)} />
            </label>
            <label>
              Maximum return rate (%)
              <input type="number" min={0} max={100} step="0.5" required value={maxReturns} onChange={(e) => setMaxReturns(e.target.value)} />
            </label>
          </div>
          <p className="hint">Margin and return alerts look at the last 30 days. Return alerts need at least 2 returned units.</p>

          <h3>Weekly e-mail</h3>
          <label className="check">
            <input
              type="checkbox"
              checked={weekly}
              disabled={!settings.email_available}
              onChange={(e) => setWeekly(e.target.checked)}
            />
            Send a weekly summary to owners and managers
          </label>
          {!settings.email_available && <p className="hint">E-mail is not configured on this server (SMTP_HOST).</p>}
          {error && <p className="error">{error}</p>}
          <div className="toolbar">
            <button className="primary">{busy ? 'Saving…' : 'Save settings'}</button>
            {settings.email_available && (
              <button type="button" className="secondary" onClick={preview}>
                Send me a preview
              </button>
            )}
          </div>
        </fieldset>
      </form>

      <form className="card form" onSubmit={createShop}>
        <h3>Another shop</h3>
        <p className="muted small">
          Manage several shops with one login (for example one per country or brand). Switch between
          them in the menu.
        </p>
        <div className="toolbar">
          <input
            aria-label="New shop name"
            placeholder="New shop name"
            required
            minLength={2}
            maxLength={120}
            value={newShop}
            onChange={(e) => setNewShop(e.target.value)}
          />
          <button className="secondary">Create shop</button>
        </div>
      </form>
    </>
  )
}
