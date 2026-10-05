import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'
import PasswordInput from '../components/PasswordInput'

export default function AcceptInvite({
  token,
  onDone,
}: {
  token: string
  onDone: (shopId: number | null) => void
}) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onDone(await api.acceptInvite(token, password))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the server')
      setBusy(false)
    }
  }

  return (
    <div className="auth-page single">
      <div className="card auth-card">
        <h2>Join the shop</h2>
        <p className="muted">
          <strong>New here?</strong> Choose a password (at least 10 characters).
          <br />
          <strong>Already have an account?</strong> Enter your current password: the shop is added
          to your account.
        </p>
        <form className="form" onSubmit={submit}>
          <label>
            Password
            <PasswordInput
              autoComplete="current-password"
              minLength={10}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy ? 'Joining…' : 'Accept invitation'}
          </button>
        </form>
      </div>
    </div>
  )
}
