import { useCallback, useEffect, useState } from 'react'
import { api, refreshSession, type Me } from './api'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import ProductsPage from './pages/ProductsPage'
import UploadPage from './pages/UploadPage'

type Tab = 'dashboard' | 'upload' | 'products'

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')
  const [checking, setChecking] = useState(true)

  const loadMe = useCallback(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    // Restore the session from the HttpOnly refresh cookie, if any.
    refreshSession().then((ok) => (ok ? loadMe() : setChecking(false)))
    const onLogout = () => setMe(null)
    window.addEventListener('ssi:logout', onLogout)
    return () => window.removeEventListener('ssi:logout', onLogout)
  }, [loadMe])

  if (checking) return <p className="app muted">Loading…</p>
  if (!me) return <Login onLoggedIn={loadMe} />

  return (
    <div className="app">
      <header className="top">
        <h1>Shopee Seller Insights · {me.shop_name}</h1>
        <nav className="tabs">
          {(['dashboard', 'upload', 'products'] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? 'active' : ''} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
          <button
            onClick={() => {
              api.logout().finally(() => setMe(null))
            }}
          >
            Log out
          </button>
        </nav>
      </header>
      {tab === 'dashboard' && <Dashboard />}
      {tab === 'upload' && <UploadPage />}
      {tab === 'products' && <ProductsPage />}
    </div>
  )
}
