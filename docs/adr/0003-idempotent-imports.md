# 0003. Imports and syncs are idempotent and set-based

**Status:** Accepted

## Context

Sellers re-upload overlapping reports, and the Shopee sync re-reads orders whose status changed. Duplicated orders would silently inflate revenue. The first implementation wrote one order per round trip: 167 s for a 50,000-row file.

## Decision

Orders are unique per `(seller_id, external id)`. Each chunk of 2,000 orders takes one `SELECT` of existing orders, one bulk status `UPDATE`, one `INSERT … ON CONFLICT DO NOTHING` and one bulk item insert. The same code path serves uploads, the Shopee sync and the demo seed.

## Consequences

- Re-uploading the same file creates nothing and reports "already up to date".
- The 50,000-row import went from 167 s to 11 s.
- The demo seed exercises the real importer, so the demo also tests it.
