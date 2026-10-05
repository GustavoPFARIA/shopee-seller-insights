import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, ApiError, refreshSession, setActiveShop, type Me, type ShopSettings } from './api'
import AppShell from './components/AppShell'
import { ToastProvider } from './components/Toast'
import AcceptInvite from './pages/AcceptInvite'
import Dashboard from './pages/Dashboard'
import IntegrationsPage from './pages/IntegrationsPage'
import Login from './pages/Login'
import ProductsPage from './pages/ProductsPage'
import SettingsPage from './pages/SettingsPage'
import TeamPage from './pages/TeamPage'
import UploadPage from './pages/UploadPage'
import { navigate, useRoute } from './router'
import { SessionContext, type Session } from './session'
import { readInviteToken, readShopeeCallback } from './urlHash'

export default function App() {
  const route = useRoute()
  const [me, setMe] = useState<Me | null>(null)
  const [settings, setSettings] = useState<ShopSettings | null>(null)
  const [inviteToken, setInviteToken] = useState(readInviteToken)
  // Accepting an invite needs no session lookup; otherwise try the refresh cookie first.
  const [checking, setChecking] = useState(() => readInviteToken() === null)
  // Coming back from the Shopee OAuth redirect: show the result on Integrations.
  const [shopeeCallback] = useState(readShopeeCallback)

  const loadMe = useCallback(async () => {
    try {
      let current: Me
      try {
        current = await api.me()
      } catch (e) {
        // The remembered shop is no longer accessible: fall back to the default one.
        if (!(e instanceof ApiError) || e.status !== 403) throw e
        setActiveShop(null)
        current = await api.me()
      }
      setActiveShop(current.seller_id)
      setMe(current)
      setSettings(await api.settings().catch(() => null))
    } catch {
      setMe(null)
    } finally {
      setChecking(false)
    }
  }, [])

  useEffect(() => {
    if (shopeeCallback) {
      history.replaceState(null, '', '/integrations')
      navigate('/integrations')
    }
  }, [shopeeCallback])

  useEffect(() => {
    if (!inviteToken) refreshSession().then((ok) => (ok ? loadMe() : setChecking(false)))
    const onLogout = () => setMe(null)
    window.addEventListener('ssi:logout', onLogout)
    return () => window.removeEventListener('ssi:logout', onLogout)
  }, [loadMe, inviteToken])

  const session = useMemo<Session | null>(
    () =>
      me && {
        me,
        settings,
        canEdit: me.role !== 'viewer',
        isOwner: me.role === 'owner',
        reload: loadMe,
        switchShop: (shopId: number) => {
          setActiveShop(shopId)
          navigate('/')
          void loadMe()
        },
      },
    [me, settings, loadMe],
  )

  let content
  if (checking) {
    content = <p className="center muted">Loading…</p>
  } else if (inviteToken && !me) {
    content = (
      <AcceptInvite
        token={inviteToken}
        onDone={(shopId) => {
          history.replaceState(null, '', '/')
          setInviteToken(null)
          if (shopId) setActiveShop(shopId)
          void loadMe()
        }}
      />
    )
  } else if (!me || !session) {
    content = <Login onLoggedIn={() => void loadMe()} />
  } else {
    const page = {
      '/': <Dashboard />,
      '/products': <ProductsPage />,
      '/upload': session.canEdit ? <UploadPage /> : <Dashboard />,
      '/integrations': <IntegrationsPage callback={shopeeCallback} />,
      '/team': <TeamPage />,
      '/settings': <SettingsPage />,
    }[route]
    content = (
      <SessionContext.Provider value={session}>
        <AppShell
          route={route}
          onLogout={() => {
            void api.logout().finally(() => {
              setActiveShop(null)
              setMe(null)
            })
          }}
        >
          {/* key: remount the page (and refetch) when the active shop changes */}
          <div key={me.seller_id}>{page}</div>
        </AppShell>
      </SessionContext.Provider>
    )
  }
  return <ToastProvider>{content}</ToastProvider>
}
