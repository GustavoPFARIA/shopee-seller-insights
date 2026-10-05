import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Invitation, type Me, type Member, type Role } from '../api'

const ROLES: Role[] = ['owner', 'manager', 'viewer']
const ROLE_HELP: Record<Role, string> = {
  owner: 'everything, including the team',
  manager: 'upload reports, edit products, AI summary, Shopee sync',
  viewer: 'read-only dashboards',
}

export default function TeamPage({ me }: { me: Me }) {
  const isOwner = me.role === 'owner'
  const [members, setMembers] = useState<Member[]>([])
  const [invitations, setInvitations] = useState<Invitation[]>([])
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [link, setLink] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    api.members().then(setMembers).catch(() => setError('Could not load the team'))
    if (isOwner) api.invitations().then(setInvitations).catch(() => undefined)
  }, [isOwner])

  useEffect(() => {
    load()
  }, [load])

  const run = async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Request failed')
    }
  }

  async function invite(e: FormEvent) {
    e.preventDefault()
    await run(async () => {
      const inv = await api.invite(email, role)
      // Fragment, not query string: never sent to the server or logged.
      setLink(`${window.location.origin}/#invite=${inv.token}`)
      setEmail('')
    })
  }

  return (
    <>
      <div className="card">
        <h2>Team</h2>
        {error && <p className="error">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>E-mail</th>
                <th>Role</th>
                <th>Since</th>
                {isOwner && <th />}
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id}>
                  <td>
                    {m.email} {m.email === me.email && <span className="muted">(you)</span>}
                  </td>
                  <td>
                    {isOwner ? (
                      <select value={m.role} onChange={(e) => run(() => api.setRole(m.id, e.target.value as Role))}>
                        {ROLES.map((r) => (
                          <option key={r} value={r}>{r}</option>
                        ))}
                      </select>
                    ) : (
                      m.role
                    )}
                  </td>
                  <td>{new Date(m.created_at).toLocaleDateString()}</td>
                  {isOwner && (
                    <td>
                      <button
                        className="secondary"
                        onClick={() => window.confirm(`Remove ${m.email}?`) && run(() => api.removeMember(m.id))}
                      >
                        Remove
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <ul className="muted">
          {ROLES.map((r) => (
            <li key={r}>
              <strong>{r}</strong>: {ROLE_HELP[r]}
            </li>
          ))}
        </ul>
      </div>

      {isOwner && (
        <div className="card">
          <h2>Invite someone</h2>
          <form className="filters" onSubmit={invite}>
            <label>
              E-mail
              <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
            </label>
            <label>
              Role
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.map((r) => (
                  <option key={r} value={r}>{r}</option>
                ))}
              </select>
            </label>
            <button className="primary">Create invite link</button>
          </form>
          {link && (
            <p>
              Send this one-time link (valid for 72 hours, shown only once):{' '}
              <code className="invite-link">{link}</code>{' '}
              <button className="secondary" onClick={() => navigator.clipboard.writeText(link)}>Copy</button>
            </p>
          )}
          {invitations.length > 0 && (
            <>
              <h2>Pending invitations</h2>
              <ul className="alerts invites">
                {invitations.map((i) => (
                  <li key={i.id}>
                    <span>
                      {i.email} as <strong>{i.role}</strong>{' '}
                      <span className="muted">expires {new Date(i.expires_at).toLocaleString()}</span>
                    </span>
                    <button className="secondary" onClick={() => run(() => api.revokeInvitation(i.id))}>
                      Revoke
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </>
  )
}
