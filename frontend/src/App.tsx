import { useCallback, useEffect, useState } from 'react'
import { api, getToken, setToken, type Me } from './api'
import Dashboard from './pages/Dashboard'
import Login from './pages/Login'
import ProductsPage from './pages/ProductsPage'
import UploadPage from './pages/UploadPage'

type Tab = 'dashboard' | 'upload' | 'products'

export default function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [tab, setTab] = useState<Tab>('dashboard')
  const [checking, setChecking] = useState(Boolean(getToken()))

  const loadMe = useCallback(() => {
    api
      .me()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => {
    if (getToken()) loadMe()
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
              setToken(null)
              setMe(null)
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
