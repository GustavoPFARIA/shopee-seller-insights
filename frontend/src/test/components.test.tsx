import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { _resetForTests } from '../api'
import KpiCard, { Delta } from '../components/KpiCard'
import Login from '../pages/Login'

describe('KpiCard', () => {
  it('shows value, direction and hint', () => {
    render(<KpiCard label="Revenue" value="R$10.00" delta={-12.34} hint="last 30 days" />)
    expect(screen.getByText('R$10.00')).toBeInTheDocument()
    expect(screen.getByText(/12\.3% vs previous period/)).toHaveClass('down')
    expect(screen.getByText('last 30 days')).toBeInTheDocument()
  })

  it('explains a missing comparison', () => {
    render(<Delta value={null} />)
    expect(screen.getByText('No data for the previous period')).toBeInTheDocument()
  })
})

describe('Login', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    _resetForTests()
  })

  it('shows the server error and does not log in', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Incorrect e-mail or password' }), { status: 401 }),
      ),
    )
    const onLoggedIn = vi.fn()
    render(<Login onLoggedIn={onLoggedIn} />)
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'a@example.com')
    await userEvent.type(screen.getByLabelText(/^password/i), 'wrong-password')
    await userEvent.click(screen.getByRole('button', { name: /^sign in$/i }))

    expect(await screen.findByText('Incorrect e-mail or password')).toBeInTheDocument()
    expect(onLoggedIn).not.toHaveBeenCalled()
  })
})
