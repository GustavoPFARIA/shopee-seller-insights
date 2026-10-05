import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'

type ToastKind = 'success' | 'error' | 'info'
interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}
type Notify = (text: string, kind?: ToastKind) => void

const ToastContext = createContext<Notify>(() => undefined)
let nextId = 1

/** Short, self-dismissing confirmations ("Saved", "Upload failed"...). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const notify = useCallback<Notify>((text, kind = 'success') => {
    const id = nextId++
    setItems((all) => [...all, { id, kind, text }])
    window.setTimeout(() => setItems((all) => all.filter((t) => t.id !== id)), 4500)
  }, [])
  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export const useToast = () => useContext(ToastContext)
