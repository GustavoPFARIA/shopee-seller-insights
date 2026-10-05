import { createContext, useContext } from 'react'
import type { Me, ShopSettings } from './api'

export interface Session {
  me: Me
  settings: ShopSettings | null
  canEdit: boolean
  isOwner: boolean
  /** Reload the user and the active shop's settings (after changes). */
  reload: () => Promise<void>
  switchShop: (shopId: number) => void
}

export const SessionContext = createContext<Session | null>(null)

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession must be used inside SessionContext')
  return session
}
