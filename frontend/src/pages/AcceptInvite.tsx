import { useState, type FormEvent } from 'react'
import { api, ApiError } from '../api'

export default function AcceptInvite({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (password !== confirm) {
      setError('Passwords do not match')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await api.acceptInvite(token, password)
      onDone()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not reach the server')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card login">
      <h2>Join the shop</h2>
      <p className="muted">Choose a password (at least 10 characters) to accept the invitation.</p>
      <form onSubmit={submit}>
        <label>
          Password
          <input type="password" minLength={10} required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          Confirm password
          <input type="password" minLength={10} required value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          {busy ? 'Joining…' : 'Accept invitation'}
        </button>
      </form>
    </div>
  )
}
