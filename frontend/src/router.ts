import { useEffect, useState } from 'react'

export const ROUTES = ['/', '/products', '/upload', '/integrations', '/team', '/settings'] as const
export type Route = (typeof ROUTES)[number]

const current = (): Route => {
  const path = window.location.pathname.replace(/\/+$/, '') || '/'
  return (ROUTES as readonly string[]).includes(path) ? (path as Route) : '/'
}

/** Navigate without reloading; query parameters (e.g. ?filter=) are kept in the URL. */
export function navigate(to: string): void {
  if (to !== window.location.pathname + window.location.search) {
    window.history.pushState(null, '', to)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(current)
  useEffect(() => {
    const onPop = () => setRoute(current())
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])
  return route
}

export const queryParam = (name: string): string | null =>
  new URLSearchParams(window.location.search).get(name)
