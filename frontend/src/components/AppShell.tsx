import { useState, type ReactNode } from 'react'
import { navigate, type Route } from '../router'
import { useSession } from '../session'

const NAV: { to: Route; label: string; icon: string; editorsOnly?: boolean }[] = [
  { to: '/', label: 'Dashboard', icon: '📊' },
  { to: '/products', label: 'Products', icon: '📦' },
  { to: '/upload', label: 'Upload report', icon: '⬆️', editorsOnly: true },
  { to: '/integrations', label: 'Integrations', icon: '🔗' },
  { to: '/team', label: 'Team', icon: '👥' },
  { to: '/settings', label: 'Settings', icon: '⚙️' },
]

export default function AppShell({
  route,
  onLogout,
  children,
}: {
  route: Route
  onLogout: () => void
  children: ReactNode
}) {
  const { me, canEdit, switchShop } = useSession()
  const [open, setOpen] = useState(false)
  const go = (to: Route) => {
    navigate(to)
    setOpen(false)
  }
  return (
    <div className="shell">
      <aside className={`sidebar ${open ? 'open' : ''}`} aria-label="Main navigation">
        <div className="brand">
          <span className="brand-mark" aria-hidden>
            S
          </span>
          Seller Insights
        </div>
        <label className="shop-switch">
          <span className="sr-only">Shop</span>
          <select
            value={me.seller_id}
            onChange={(e) => switchShop(Number(e.target.value))}
            aria-label="Active shop"
          >
            {me.shops.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>
          <span className={`role-pill ${me.role}`}>{me.role}</span>
        </label>
        <nav>
          {NAV.filter((n) => canEdit || !n.editorsOnly).map((n) => (
            <a
              key={n.to}
              href={n.to}
              className={route === n.to ? 'active' : ''}
              aria-current={route === n.to ? 'page' : undefined}
              onClick={(e) => {
                e.preventDefault()
                go(n.to)
              }}
            >
              <span aria-hidden>{n.icon}</span> {n.label}
            </a>
          ))}
        </nav>
        <div className="sidebar-foot">
          <div className="muted small" title={me.email}>
            {me.email}
          </div>
          <button className="link-button" onClick={onLogout}>
            Log out
          </button>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <button
            className="icon-button menu-button"
            aria-label="Open menu"
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            ☰
          </button>
          <h1 className="shop-title">{me.shop_name}</h1>
        </header>
        <main className="content">{children}</main>
      </div>
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
    </div>
  )
}
