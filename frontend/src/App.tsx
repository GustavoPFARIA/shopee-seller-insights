import { useCallback, useEffect, useState } from 'react'
import { api, refreshSession, type Me } from './api'
import AcceptInvite from './pages/AcceptInvite'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import ProductsPage from './pages/ProductsPage'
import ShopeePage from './pages/ShopeePage'
import TeamPage from './pages/TeamPage'
import UploadPage from './pages/UploadPage'
import { readInviteToken, readShopeeCallback } from './urlHash'

type Tab = 'dashboard' | 'upload' | 'products' | 'shopee' | 'team'
const TAB_LABEL: Record<Tab, string> = {
  dashboard: 'Dashboard',
  upload: 'Upload',
  products: 'Products',
  shopee: 'Shopee',
  team: 'Team',
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  // Coming back from the Shopee OAuth redirect: show the result on the Shopee tab.
  const [shopeeCallback] = useState(readShopeeCallback)
  const [tab, setTab] = useState<Tab>(shopeeCallback ? 'shopee' : 'dashboard')
  const [inviteToken, setInviteToken] = useState(readInviteToken)
  // Accepting an invite needs no session lookup; otherwise try the refresh cookie first.
  const [checking, setChecking] = useState(() => readInviteToken() === null)

  useEffect(() => {
    if (shopeeCallback) history.replaceState(null, '', window.location.pathname)
  }, [shopeeCallback])

  const loadMe = useCallback(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    // Restore the session from the HttpOnly refresh cookie, if any.
    if (!inviteToken) refreshSession().then((ok) => (ok ? loadMe() : setChecking(false)))
    const onLogout = () => setMe(null)
    window.addEventListener('ssi:logout', onLogout)
    return () => window.removeEventListener('ssi:logout', onLogout)
  }, [loadMe, inviteToken])

  if (checking) return <p className="app muted">Loading…</p>
  if (inviteToken && !me)
    return (
      <AcceptInvite
        token={inviteToken}
        onDone={() => {
          history.replaceState(null, '', window.location.pathname)
          setInviteToken(null)
          loadMe()
        }}
      />
    )
  if (!me) return <Login onLoggedIn={loadMe} />

  const canEdit = me.role !== 'viewer'
  const tabs: Tab[] = canEdit
    ? ['dashboard', 'upload', 'products', 'shopee', 'team']
    : ['dashboard', 'products', 'shopee', 'team']

  return (
    <div className="app">
      <header className="top">
        <h1>
          Shopee Seller Insights · {me.shop_name} <span className="muted role">{me.role}</span>
        </h1>
        <nav className="tabs">
          {tabs.map((t) => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
              {TAB_LABEL[t]}
            </button>
          ))}
          <button onClick={() => api.logout().finally(() => setMe(null))}>Log out</button>
        </nav>
      </header>
      {tab === 'dashboard' && <Dashboard canEdit={canEdit} />}
      {tab === 'upload' && canEdit && <UploadPage />}
      {tab === 'products' && <ProductsPage canEdit={canEdit} />}
      {tab === 'shopee' && <ShopeePage me={me} callback={shopeeCallback} />}
      {tab === 'team' && <TeamPage me={me} />}
    </div>
  )
}
