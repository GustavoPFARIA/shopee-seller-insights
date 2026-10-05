import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { _resetForTests, api, ApiError, setActiveShop } from '../api'

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('api client', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    _resetForTests()
    localStorage.clear()
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })
  afterEach(() => vi.unstubAllGlobals())

  it('sends the bearer token and the active shop', async () => {
    fetchMock.mockResolvedValueOnce(json(200, { access_token: 'tok' }))
    await api.login('a@example.com', 'secret-password')
    setActiveShop(7)
    fetchMock.mockResolvedValueOnce(json(200, []))
    await api.alerts()

    const headers = new Headers(fetchMock.mock.calls[1][1].headers)
    expect(headers.get('Authorization')).toBe('Bearer tok')
    expect(headers.get('X-Shop-Id')).toBe('7')
    expect(localStorage.getItem('ssi:active-shop')).toBe('7')
  })

  it('refreshes once on 401 and retries the request', async () => {
    fetchMock
      .mockResolvedValueOnce(json(401, { detail: 'expired' }))
      .mockResolvedValueOnce(json(200, { access_token: 'fresh' }))
      .mockResolvedValueOnce(json(200, []))
    await expect(api.alerts()).resolves.toEqual([])

    expect(fetchMock.mock.calls[1][0]).toBe('/api/auth/refresh')
    expect(new Headers(fetchMock.mock.calls[1][1].headers).get('X-Requested-With')).toBe('ssi')
    expect(new Headers(fetchMock.mock.calls[2][1].headers).get('Authorization')).toBe('Bearer fresh')
  })

  it('signals logout when the refresh fails', async () => {
    const onLogout = vi.fn()
    window.addEventListener('ssi:logout', onLogout)
    fetchMock.mockResolvedValueOnce(json(401, {})).mockResolvedValueOnce(json(401, {}))
    await expect(api.alerts()).rejects.toBeInstanceOf(ApiError)
    expect(onLogout).toHaveBeenCalledOnce()
    window.removeEventListener('ssi:logout', onLogout)
  })

  it('exposes server messages and row details', async () => {
    fetchMock.mockResolvedValueOnce(
      json(422, { detail: 'Invalid file', errors: [{ row: 3, field: 'price', message: 'bad' }] }),
    )
    const error = await api.uploads().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('Invalid file')
    expect((error as ApiError).details).toHaveLength(1)
  })
})
