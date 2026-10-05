// Typed client for the FastAPI backend.
//
// Sessions: the short-lived access token lives only in memory. The refresh token is
// an HttpOnly cookie the page cannot read; /api/auth/refresh exchanges it for a new
// access token (on page load and whenever a request gets a 401).
//
// Shops: a user can belong to several shops. The active one is sent on every request
// as the X-Shop-Id header; the server checks the membership each time.

let accessToken: string | null = null
let refreshing: Promise<boolean> | null = null
let activeShopId: number | null = null
const CSRF = { 'X-Requested-With': 'ssi' }
const SHOP_KEY = 'ssi:active-shop'

export type Money = string
export type Role = 'owner' | 'manager' | 'viewer'

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
  returned_units: number
  cancelled_units: number
  return_rate_pct: number | null
}
export type AbcClass = 'A' | 'B' | 'C'
export interface AbcItem {
  product_id: number
  sku: string
  name: string
  revenue: Money
  share_pct: number
  cumulative_pct: number
  abc_class: AbcClass
}
export type AlertKind = 'low_stock' | 'stalled_product' | 'low_margin' | 'high_returns'
export interface Alert {
  kind: AlertKind
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
export interface UploadRecord {
  id: number
  filename: string
  row_count: number
  orders_created: number
  orders_updated: number
  orders_unchanged: number
  created_at: string
}
export interface SyncRun {
  id: number
  trigger: 'manual' | 'scheduled' | 'push'
  status: 'queued' | 'running' | 'success' | 'error'
  started_at: string
  finished_at: string | null
  orders_created: number
  orders_updated: number
  orders_skipped: number
  products_stock_updated: number
  error: string | null
}
export interface ShopeeStatus {
  enabled: boolean
  connected: boolean
  shop_id: number | null
  connected_at: string | null
  orders_synced_until: string | null
  sync_interval_minutes: number
  runs: SyncRun[]
}
export interface CatalogImportResult {
  rows: number
  updated: number
  created: number
  unchanged: number
}
export interface ShopRef {
  id: number
  name: string
  role: Role
}
export interface Me {
  email: string
  seller_id: number
  shop_name: string
  role: Role
  shops: ShopRef[]
}
export interface Member {
  id: number
  email: string
  role: Role
  created_at: string
}
export interface Invitation {
  id: number
  email: string
  role: Role
  expires_at: string
  token?: string
  emailed?: boolean
}
export interface AiSummary {
  enabled: boolean
  summary: string | null
}
export interface ShopSettings {
  name: string
  email_available: boolean
  stalled_days: number
  min_margin_pct: Money
  max_return_rate_pct: Money
  weekly_email: boolean
}
export type Period = { start: string; end: string }

export class ApiError extends Error {
  status: number
  details: { row: number | null; field: string; message: string }[]
  constructor(status: number, message: string, details: ApiError['details'] = []) {
    super(message)
    this.status = status
    this.details = details
  }
}

// ---- active shop -----------------------------------------------------------------

/** Remember the chosen shop (a per-browser convenience; the server re-checks access). */
export function setActiveShop(id: number | null): void {
  activeShopId = id
  try {
    if (id === null) localStorage.removeItem(SHOP_KEY)
    else localStorage.setItem(SHOP_KEY, String(id))
  } catch {
    /* storage unavailable (private mode): keep it in memory only */
  }
}

export function getActiveShop(): number | null {
  if (activeShopId !== null) return activeShopId
  try {
    const stored = Number(localStorage.getItem(SHOP_KEY))
    activeShopId = Number.isInteger(stored) && stored > 0 ? stored : null
  } catch {
    activeShopId = null
  }
  return activeShopId
}

// ---- transport -------------------------------------------------------------------

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

function send(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  if (accessToken) headers.set('Authorization', `Bearer ${accessToken}`)
  const shop = getActiveShop()
  if (shop !== null) headers.set('X-Shop-Id', String(shop))
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
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) message = body.detail[0].msg
      if (Array.isArray(body.errors)) details = body.errors
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(resp.status, message, details)
  }
  if (resp.status === 204) return undefined as T
  return resp.json() as Promise<T>
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

async function download(path: string, filename: string): Promise<void> {
  const resp = await authedFetch(path)
  if (!resp.ok) throw new ApiError(resp.status, 'Download failed')
  const url = URL.createObjectURL(await resp.blob())
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

const qs = (params: Record<string, string>) => new URLSearchParams(params).toString()
const fileForm = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return form
}

export const api = {
  async login(email: string, password: string) {
    const body = new URLSearchParams({ username: email, password })
    const res = await request<{ access_token: string }>('/api/auth/login', { method: 'POST', body })
    accessToken = res.access_token
  },
  async register(email: string, password: string, shopName: string) {
    const res = await request<{ access_token: string }>(
      '/api/auth/register',
      json('POST', { email, password, shop_name: shopName }),
    )
    accessToken = res.access_token
  },
  async logout() {
    await fetch('/api/auth/logout', { method: 'POST', headers: CSRF }).catch(() => undefined)
    accessToken = null
  },
  me: () => request<Me>('/api/auth/me'),
  async acceptInvite(token: string, password: string): Promise<number | null> {
    const res = await request<{ access_token: string; shop_id: number | null }>(
      '/api/auth/accept-invite',
      json('POST', { token, password }),
    )
    accessToken = res.access_token
    return res.shop_id
  },
  createShop: (name: string) => request<ShopRef>('/api/shops', json('POST', { name })),

  settings: () => request<ShopSettings>('/api/settings'),
  updateSettings: (patch: Partial<Omit<ShopSettings, 'email_available'>>) =>
    request<ShopSettings>('/api/settings', json('PATCH', patch)),
  sendDigestPreview: () => request<void>('/api/settings/digest/preview', { method: 'POST' }),

  shopeeStatus: () => request<ShopeeStatus>('/api/shopee/status'),
  shopeeConnect: () =>
    request<{ authorization_url: string }>('/api/shopee/connect', { method: 'POST' }),
  shopeeSync: () => request<SyncRun>('/api/shopee/sync', { method: 'POST' }),
  shopeeDisconnect: () => request<void>('/api/shopee/connection', { method: 'DELETE' }),

  members: () => request<Member[]>('/api/members'),
  invitations: () => request<Invitation[]>('/api/members/invitations'),
  invite: (email: string, role: Role) =>
    request<Invitation>('/api/members/invitations', json('POST', { email, role })),
  revokeInvitation: (id: number) =>
    request<void>(`/api/members/invitations/${id}`, { method: 'DELETE' }),
  setRole: (id: number, role: Role) => request<Member>(`/api/members/${id}`, json('PATCH', { role })),
  removeMember: (id: number) => request<void>(`/api/members/${id}`, { method: 'DELETE' }),

  overview: (p: Period) => request<Overview>(`/api/metrics/overview?${qs(p)}`),
  daily: (p: Period) => request<DailyPoint[]>(`/api/metrics/daily?${qs(p)}`),
  products: (p: Period) => request<ProductMetrics[]>(`/api/metrics/products?${qs(p)}`),
  abc: (p: Period) => request<AbcItem[]>(`/api/metrics/abc?${qs(p)}`),
  alerts: () => request<Alert[]>('/api/alerts'),
  exportCsv: (p: Period) =>
    download(`/api/metrics/products/export.csv?${qs(p)}`, `product-metrics-${p.start}-${p.end}.csv`),

  catalog: () => request<Product[]>('/api/products'),
  updateProduct: (id: number, patch: Partial<Omit<Product, 'id' | 'sku' | 'name'>>) =>
    request<Product>(`/api/products/${id}`, json('PATCH', patch)),
  downloadCatalog: () => download('/api/products/template.csv', 'products.csv'),
  importCatalog: (file: File) =>
    request<CatalogImportResult>('/api/products/import', { method: 'POST', body: fileForm(file) }),

  upload: (file: File) =>
    request<UploadResult>('/api/uploads', { method: 'POST', body: fileForm(file) }),
  uploads: () => request<UploadRecord[]>('/api/uploads'),
  summary: () => request<AiSummary>('/api/summary/weekly'),
}

/** For tests only: reset module state between test cases. */
export function _resetForTests(): void {
  accessToken = null
  refreshing = null
  activeShopId = null
}
