import type { ReactNode } from 'react'

export default function EmptyState({
  title,
  children,
}: {
  title: string
  children?: ReactNode
}) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children}
    </div>
  )
}
