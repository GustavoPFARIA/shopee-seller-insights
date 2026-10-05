# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-10-05

First complete release.

### Added

- **Order import** from Shopee Seller Centre CSV/XLSX exports (Portuguese and English
  headers): row-level validation, idempotent re-uploads, size, type and row limits,
  formula-injection protection, buyer data pseudonymized or dropped.
- **Metrics**: revenue, orders, units, average ticket and net margin with comparison to
  the previous period; daily revenue; real margin per product after Shopee fees,
  coupons, shipping and unit cost; ABC curve; CSV export.
- **Returns per product**: returned and cancelled units and return rate.
- **Alerts**: low stock, stalled products, low margin and high returns, with per-shop
  thresholds in Settings.
- **Product catalogue**: cost and stock editing, spreadsheet download and import.
- **Shopee Open Platform v2 integration**: OAuth bound to the browser, encrypted
  tokens, order, escrow and stock sync, background worker with queued runs, signed
  push notifications, reconnect prompt when access is revoked.
- **Shopee demo mode** with a local fake Shopee server (`docker-compose.shopee-demo.yml`).
- **Accounts**: owner, manager and viewer roles; one-time invitations; several shops
  per account with a shop switcher.
- **E-mail** (optional, SMTP): invitations and a weekly summary.
- **AI weekly summary** (optional) with Claude, from aggregated numbers only.
- **Web app**: dashboard, products, upload, integrations, team and settings pages;
  dark mode; mobile layout.
- **Security**: Argon2id, short-lived JWT with rotating HttpOnly refresh cookie and
  reuse detection, CSRF header, PostgreSQL rate limiter, nginx with CSP, least-privilege
  database role, production refuses development secrets.
- **Quality**: 216 backend tests (~99% coverage), 16 frontend tests, 52-check smoke test
  through nginx, performance budget, dependency audit and secret scan in CI.

[Unreleased]: https://github.com/GustavoPFARIA/shopee-seller-insights/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/GustavoPFARIA/shopee-seller-insights/releases/tag/v1.0.0
