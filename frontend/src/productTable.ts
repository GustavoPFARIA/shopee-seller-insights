// Joins the catalogue (cost, stock) with sales metrics and ABC classes, and applies
// the Products page filters. Pure functions: easy to test, no React here.

import type { AbcClass, AbcItem, Product, ProductMetrics } from './api'

export interface ProductRow {
  product: Product
  metrics: ProductMetrics | null
  abc: AbcClass | null
}

export type ProductFilter =
  | 'all'
  | 'missing_cost'
  | 'low_stock'
  | 'losing_money'
  | 'high_returns'
  | 'no_sales'
  | 'class_a'

export type ProductSort = 'revenue' | 'margin_pct' | 'units' | 'stock' | 'name'

export const FILTER_LABELS: Record<ProductFilter, string> = {
  all: 'All',
  missing_cost: 'Missing cost',
  low_stock: 'Low stock',
  losing_money: 'Losing money',
  high_returns: 'High returns',
  no_sales: 'No sales',
  class_a: 'Class A',
}

export function buildRows(
  catalog: Product[],
  metrics: ProductMetrics[],
  abc: AbcItem[],
): ProductRow[] {
  const byId = new Map(metrics.map((m) => [m.product_id, m]))
  const classes = new Map(abc.map((a) => [a.product_id, a.abc_class]))
  return catalog.map((product) => ({
    product,
    metrics: byId.get(product.id) ?? null,
    abc: classes.get(product.id) ?? null,
  }))
}

export const isLowStock = (p: Product): boolean =>
  p.stock_quantity !== null && p.stock_quantity <= p.low_stock_threshold

export function matches(
  row: ProductRow,
  filter: ProductFilter,
  maxReturnRatePct: number,
): boolean {
  const m = row.metrics
  switch (filter) {
    case 'all':
      return true
    case 'missing_cost':
      return row.product.unit_cost === null
    case 'low_stock':
      return isLowStock(row.product)
    case 'losing_money':
      return m?.margin !== null && m?.margin !== undefined && Number(m.margin) < 0
    case 'high_returns':
      return (m?.return_rate_pct ?? 0) > maxReturnRatePct
    case 'no_sales':
      return !m || m.units === 0
    case 'class_a':
      return row.abc === 'A'
  }
}

export function countByFilter(
  rows: ProductRow[],
  maxReturnRatePct: number,
): Record<ProductFilter, number> {
  const filters = Object.keys(FILTER_LABELS) as ProductFilter[]
  return Object.fromEntries(
    filters.map((f) => [f, rows.filter((r) => matches(r, f, maxReturnRatePct)).length]),
  ) as Record<ProductFilter, number>
}

const sortValue = (row: ProductRow, sort: ProductSort): number | string => {
  switch (sort) {
    case 'revenue':
      return Number(row.metrics?.revenue ?? 0)
    case 'margin_pct':
      return row.metrics?.margin_pct ?? Number.NEGATIVE_INFINITY
    case 'units':
      return row.metrics?.units ?? 0
    case 'stock':
      return row.product.stock_quantity ?? Number.POSITIVE_INFINITY
    case 'name':
      return row.product.name.toLowerCase()
  }
}

export function selectRows(
  rows: ProductRow[],
  opts: { filter: ProductFilter; search: string; sort: ProductSort; maxReturnRatePct: number },
): ProductRow[] {
  const term = opts.search.trim().toLowerCase()
  const ascending = opts.sort === 'name' || opts.sort === 'stock'
  return rows
    .filter((r) => matches(r, opts.filter, opts.maxReturnRatePct))
    .filter(
      (r) =>
        !term ||
        r.product.name.toLowerCase().includes(term) ||
        r.product.sku.toLowerCase().includes(term),
    )
    .sort((a, b) => {
      const va = sortValue(a, opts.sort)
      const vb = sortValue(b, opts.sort)
      const cmp = va < vb ? -1 : va > vb ? 1 : 0
      return ascending ? cmp : -cmp
    })
}
