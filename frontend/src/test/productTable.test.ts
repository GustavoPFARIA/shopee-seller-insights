import { describe, expect, it } from 'vitest'
import type { AbcItem, Product, ProductMetrics } from '../api'
import { buildRows, countByFilter, selectRows } from '../productTable'

const product = (id: number, over: Partial<Product> = {}): Product => ({
  id,
  sku: `SKU-${id}`,
  name: `Product ${id}`,
  unit_cost: '10.00',
  stock_quantity: 50,
  low_stock_threshold: 5,
  ...over,
})
const metrics = (id: number, over: Partial<ProductMetrics> = {}): ProductMetrics => ({
  product_id: id,
  sku: `SKU-${id}`,
  name: `Product ${id}`,
  units: 10,
  revenue: '100.00',
  commission_fee: '0',
  service_fee: '0',
  shipping_fee: '0',
  voucher: '0',
  product_cost: '50.00',
  margin: '20.00',
  margin_pct: 20,
  returned_units: 0,
  cancelled_units: 0,
  return_rate_pct: 0,
  ...over,
})
const abc: AbcItem[] = [
  { product_id: 1, sku: 'SKU-1', name: 'Product 1', revenue: '500', share_pct: 80, cumulative_pct: 80, abc_class: 'A' },
]

const rows = buildRows(
  [
    product(1),
    product(2, { unit_cost: null, stock_quantity: 3 }),
    product(3, { name: 'Blue mug' }),
    product(4),
  ],
  [
    metrics(1, { revenue: '500.00' }),
    metrics(2, { margin: null, margin_pct: null }),
    metrics(3, { margin: '-5.00', margin_pct: -5, return_rate_pct: 25 }),
  ],
  abc,
)

describe('product table', () => {
  it('joins catalogue, metrics and ABC classes', () => {
    expect(rows[0].abc).toBe('A')
    expect(rows[3].metrics).toBeNull()
  })

  it('counts every filter', () => {
    expect(countByFilter(rows, 10)).toEqual({
      all: 4,
      missing_cost: 1,
      low_stock: 1,
      losing_money: 1,
      high_returns: 1,
      no_sales: 1,
      class_a: 1,
    })
  })

  it('uses the shop return threshold', () => {
    expect(countByFilter(rows, 30).high_returns).toBe(0)
  })

  it('searches name and SKU, case-insensitively', () => {
    const opts = { filter: 'all' as const, sort: 'revenue' as const, maxReturnRatePct: 10 }
    expect(selectRows(rows, { ...opts, search: 'MUG' }).map((r) => r.product.id)).toEqual([3])
    expect(selectRows(rows, { ...opts, search: 'sku-4' }).map((r) => r.product.id)).toEqual([4])
  })

  it('sorts revenue descending and stock ascending', () => {
    const base = { filter: 'all' as const, search: '', maxReturnRatePct: 10 }
    expect(selectRows(rows, { ...base, sort: 'revenue' })[0].product.id).toBe(1)
    expect(selectRows(rows, { ...base, sort: 'stock' })[0].product.id).toBe(2)
  })
})
