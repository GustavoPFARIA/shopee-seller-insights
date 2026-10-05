import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { api, ApiError, type Invitation, type Member, type Role } from '../api'
import { useToast } from '../components/Toast'
import { fmtDate, fmtDateTime } from '../format'
import { useSession } from '../session'

const ROLES: Role[] = ['owner', 'manager', 'viewer']
const ROLE_HELP: Record<Role, string> = {
  owner: 'everything, including the team, settings and the Shopee connection',
  manager: 'upload reports, edit products, sync Shopee, AI summary',
  viewer: 'read-only dashboards',
}

export default function TeamPage() {
  const { me, isOwner } = useSession()
  const notify = useToast()
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

  useEffect(load, [load])

  const run = async (action: () => Promise<unknown>, done?: string) => {
    setError(null)
    try {
      await action()
      if (done) notify(done)
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Request failed')
    }
  }

  async function invite(e: FormEvent) {
    e.preventDefault()
    setLink(null)
    await run(async () => {
      const inv = await api.invite(email, role)
      if (inv.emailed) {
        notify(`Invitation e-mailed to ${inv.email}`)
      } else {
        // Fragment, not query string: never sent to the server or logged.
        setLink(`${window.location.origin}/#invite=${inv.token}`)
      }
      setEmail('')
    })
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h2>Team</h2>
          <p className="muted">People with access to {me.shop_name}.</p>
        </div>
      </div>
      {error && <p className="error-box">{error}</p>}

      <section className="card">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>E-mail</th>
                <th>Role</th>
                <th>Since</th>
                {isOwner && <th aria-label="Actions" />}
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
                      <select
                        aria-label={`Role of ${m.email}`}
                        value={m.role}
                        onChange={(e) => run(() => api.setRole(m.id, e.target.value as Role), 'Role updated')}
                      >
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="tag">{m.role}</span>
                    )}
                  </td>
                  <td>{fmtDate(m.created_at)}</td>
                  {isOwner && (
                    <td>
                      <button
                        className="secondary small-button"
                        onClick={() =>
                          window.confirm(`Remove ${m.email} from this shop?`) &&
                          run(() => api.removeMember(m.id), 'Member removed')
                        }
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
        <ul className="list muted small">
          {ROLES.map((r) => (
            <li key={r}>
              <strong>{r}</strong>: {ROLE_HELP[r]}
            </li>
          ))}
        </ul>
      </section>

      {isOwner && (
        <section className="card">
          <h3>Invite someone</h3>
          <form className="toolbar" onSubmit={invite}>
            <input
              type="email"
              required
              aria-label="E-mail to invite"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            <select aria-label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
            <button className="primary">Invite</button>
          </form>
          {link && (
            <div className="ok-box">
              <p>
                E-mail is not configured, so send this one-time link yourself (valid for 72 hours,
                shown only once):
              </p>
              <code className="invite-link">{link}</code>{' '}
              <button
                className="secondary small-button"
                onClick={() => navigator.clipboard.writeText(link).then(() => notify('Link copied'))}
              >
                Copy
              </button>
            </div>
          )}
          {invitations.length > 0 && (
            <>
              <h4>Pending invitations</h4>
              <ul className="list">
                {invitations.map((i) => (
                  <li key={i.id}>
                    <span className="grow">
                      {i.email} as <strong>{i.role}</strong>{' '}
                      <span className="muted small">expires {fmtDateTime(i.expires_at)}</span>
                    </span>
                    <button
                      className="secondary small-button"
                      onClick={() => run(() => api.revokeInvitation(i.id), 'Invitation revoked')}
                    >
                      Revoke
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </>
  )
}
