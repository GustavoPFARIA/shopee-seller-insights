import { describe, expect, it } from 'vitest'
import { fmtAgo, fmtMoney, fmtPct, isoDay, lastDays } from '../format'

describe('format', () => {
  it('formats decimal strings as BRL without float surprises', () => {
    expect(fmtMoney('1234.5')).toBe('R$1,234.50')
    expect(fmtMoney(null)).toBe('—')
  })

  it('formats percentages and missing values', () => {
    expect(fmtPct(12.345)).toBe('12.3%')
    expect(fmtPct(undefined)).toBe('—')
  })

  it('describes elapsed time', () => {
    const now = new Date('2026-01-10T12:00:00Z')
    expect(fmtAgo(null, now)).toBe('never')
    expect(fmtAgo('2026-01-10T11:59:30Z', now)).toBe('just now')
    expect(fmtAgo('2026-01-10T11:00:00Z', now)).toBe('1 hour ago')
    expect(fmtAgo('2026-01-07T12:00:00Z', now)).toBe('3 days ago')
  })

  it('builds inclusive local-day periods', () => {
    const today = new Date(2026, 2, 1) // 1 March (local)
    expect(isoDay(today)).toBe('2026-03-01')
    expect(lastDays(7, today)).toEqual({ start: '2026-02-23', end: '2026-03-01' })
    expect(lastDays(1, today)).toEqual({ start: '2026-03-01', end: '2026-03-01' })
  })
})
