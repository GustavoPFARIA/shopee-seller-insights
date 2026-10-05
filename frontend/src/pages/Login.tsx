import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import PasswordInput from '../components/PasswordInput'

type Mode = 'signin' | 'register'

export default function Login({ onLoggedIn }: { onLoggedIn: () => void }) {
  const [mode, setMode] = useState<Mode>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [shopName, setShopName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      if (mode === 'signin') await api.login(email, password)
      else await api.register(email, password, shopName)
      onLoggedIn()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the server')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-intro">
        <div className="brand big">
          <span className="brand-mark" aria-hidden>
            S
          </span>
          Seller Insights
        </div>
        <h1>Know which Shopee products really make money.</h1>
        <ul className="ticks">
          <li>Real margin per product after Shopee fees, coupons, shipping and your cost</li>
          <li>ABC curve, period comparison and daily revenue</li>
          <li>Alerts for low stock, stalled products, thin margins and returns</li>
          <li>Upload the Seller Centre report or connect your shop</li>
        </ul>
      </div>
      <div className="card auth-card">
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={mode === 'signin'} className={mode === 'signin' ? 'on' : ''} onClick={() => setMode('signin')}>
            Sign in
          </button>
          <button role="tab" aria-selected={mode === 'register'} className={mode === 'register' ? 'on' : ''} onClick={() => setMode('register')}>
            Create account
          </button>
        </div>
        <form className="form" onSubmit={submit}>
          <label>
            E-mail
            <input type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <PasswordInput
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              required
              minLength={mode === 'register' ? 10 : undefined}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {mode === 'register' && <span className="hint">At least 10 characters.</span>}
          </label>
          {mode === 'register' && (
            <label>
              Shop name
              <input required minLength={2} maxLength={120} value={shopName} onChange={(e) => setShopName(e.target.value)} />
            </label>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy ? 'Please wait…' : mode === 'signin' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        {mode === 'signin' && (
          <p className="muted small">
            Demo: <code>demo@shopee-insights.dev</code> / <code>DemoPassword123!</code>
          </p>
        )}
      </div>
    </div>
  )
}
