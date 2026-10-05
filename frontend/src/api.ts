// Typed client for the FastAPI backend.
// The short-lived access token lives only in memory. The refresh token is an
// HttpOnly cookie the page cannot read; /api/auth/refresh exchanges it for a new
// access token (on page load and whenever a request gets a 401).

let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null
const CSRF = { 'X-Requested-With': 'ssi' }

export type Money = string

export interface KpiValues {
  revenue: Money
  orders: number
  units: number
  avg_ticket: Money
  net_margin: Money | null
}
export interface Overview {
  start: string
  end: string
  previous_start: string
  previous_end: string
  current: KpiValues
  previous: KpiValues
  change: { revenue_pct: number | null; orders_pct: number | null; avg_ticket_pct: number | null }
}
export interface DailyPoint {
  day: string
  revenue: Money
  orders: number
}
export interface ProductMetrics {
  product_id: number
  sku: string
  name: string
  units: number
  revenue: Money
  commission_fee: Money
  service_fee: Money
  shipping_fee: Money
  voucher: Money
  product_cost: Money | null
  margin: Money | null
  margin_pct: number | null
}
export interface AbcItem {
  product_id: number
  sku: string
  name: string
  revenue: Money
  share_pct: number
  cumulative_pct: number
  abc_class: 'A' | 'B' | 'C'
}
export interface Alert {
  kind: 'low_stock' | 'stalled_product' | 'low_margin'
  product_id: number
  sku: string
  name: string
  message: string
}
export interface Product {
  id: number
  sku: string
  name: string
  unit_cost: Money | null
  stock_quantity: number | null
  low_stock_threshold: number
}
export interface UploadResult {
  upload_id: number
  row_count: number
  orders_created: number
  orders_updated: number
  orders_unchanged: number
  products_created: number
}
export interface Me {
  email: string
  seller_id: number
  shop_name: string
}
export interface AiSummary {
  enabled: boolean
  summary: string | null
}

export class ApiError extends Error {
  status: number
  details: { row: number; field: string; message: string }[]
  constructor(status: number, message: string, details: ApiError['details'] = []) {
    super(message)
    this.status = status
    this.details = details
  }
}

/** Exchange the refresh cookie for a new access token. Concurrent callers share one call. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= fetch('/api/auth/refresh', { method: 'POST', headers: CSRF })
    .then(async (resp) => {
      accessToken = resp.ok ? ((await resp.json()) as { access_token: string }).access_token : null
      return resp.ok
    })
    .catch(() => false)
    .finally(() => {
      refreshing = null
    })
  return refreshing
}

async function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  return fetch(path, { ...init, headers })
}

async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let resp = await send(path, init)
  if (resp.status === 401 && !path.startsWith('/api/auth/')) {
    if (await refreshSession()) {
      resp = await send(path, init)
    } else {
      window.dispatchEvent(new Event('ssi:logout'))
    }
  }
  return resp
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const resp = await authedFetch(path, init)
  if (!resp.ok) {
    let message = `Request failed (${resp.status})`
    let details: ApiError['details'] = []
    try {
      const body = await resp.json()
      if (typeof body.detail === 'string') message = body.detail
      if (Array.isArray(body.errors)) details = body.errors
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, message, details)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

const qs = (params: Record<string, string>) => new URLSearchParams(params).toString()

export const api = {
  async login(email: string, password: string) {
    const body = new URLSearchParams({ username: email, password })
    const res = await request<{ access_token: string }>('/api/auth/login', { method: 'POST', body })
    accessToken = res.access_token
  },
  async logout() {
    await fetch('/api/auth/logout', { method: 'POST', headers: CSRF }).catch(() => undefined)
    accessToken = null
  },
  me: () => request<Me>('/api/auth/me'),
  overview: (p: Record<string, string>) => request<Overview>(`/api/metrics/overview?${qs(p)}`),
  daily: (p: Record<string, string>) => request<DailyPoint[]>(`/api/metrics/daily?${qs(p)}`),
  products: (p: Record<string, string>) =>
    request<ProductMetrics[]>(`/api/metrics/products?${qs(p)}`),
  abc: (p: Record<string, string>) => request<AbcItem[]>(`/api/metrics/abc?${qs(p)}`),
  alerts: () => request<Alert[]>('/api/alerts'),
  catalog: () => request<Product[]>('/api/products'),
  updateProduct: (id: number, patch: Partial<Omit<Product, 'id' | 'sku' | 'name'>>) =>
    request<Product>(`/api/products/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<UploadResult>('/api/uploads', { method: 'POST', body: form })
  },
  summary: () => request<AiSummary>('/api/summary/weekly'),
  async exportCsv(p: Record<string, string>) {
    const resp = await authedFetch(`/api/metrics/products/export.csv?${qs(p)}`)
    if (!resp.ok) throw new ApiError(resp.status, 'Export failed')
    const url = URL.createObjectURL(await resp.blob())
    const a = document.createElement('a')
    a.href = url
    a.download = `product-metrics-${p.start}-${p.end}.csv`
    a.click()
    URL.revokeObjectURL(url)
  },
}

const brl = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'BRL' })
export const fmtMoney = (v: Money | number | null) => (v === null ? '—' : brl.format(Number(v)))
export const fmtPct = (v: number | null) => (v === null ? '—' : `${v.toFixed(1)}%`)
