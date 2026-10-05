// Display helpers. Money comes from the API as decimal strings (never floats).

import type { Money } from './api'

const brl = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'BRL' })
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })

export const fmtMoney = (v: Money | number | null | undefined): string =>
  v === null || v === undefined ? '—' : brl.format(Number(v))

export const fmtCompact = (v: number): string => compact.format(v)

export const fmtPct = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? '—' : `${v.toFixed(digits)}%`

export const fmtDate = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleDateString() : '—'

export const fmtDateTime = (iso: string | null | undefined): string =>
  iso ? new Date(iso).toLocaleString() : '—'

/** "3 hours ago", "2 days ago"... for "last updated" hints. */
export function fmtAgo(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return 'never'
  const seconds = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000)
  if (seconds < 60) return 'just now'
  const units: [number, string][] = [
    [86_400, 'day'],
    [3_600, 'hour'],
    [60, 'minute'],
  ]
  for (const [size, name] of units) {
    if (seconds >= size) {
      const n = Math.floor(seconds / size)
      return `${n} ${name}${n === 1 ? '' : 's'} ago`
    }
  }
  return 'just now'
}

/** Local calendar date as YYYY-MM-DD (not UTC: the seller thinks in local days). */
export function isoDay(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

export function lastDays(days: number, today: Date = new Date()): { start: string; end: string } {
  const start = new Date(today)
  start.setDate(start.getDate() - (days - 1))
  return { start: isoDay(start), end: isoDay(today) }
}
