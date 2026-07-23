# ShipFlow — API Reference

> Companion to `docs/ARCHITECTURE.md`. Every endpoint the system exposes, exact
> payloads, and the outbound event contract for EventHub. Implementers: routes live in
> `app/api/`, thin handlers only — logic belongs in `app/services/`.

## Conventions

- **Two surfaces**: HTML **pages** (Jinja2 + HTMX, session auth, pt-BR UI) and a JSON
  **API** under `/api` (session auth; also used by HTMX fragments). The webhook and
  OAuth callback are the only unauthenticated non-page endpoints.
- IDs in URLs are ShipFlow numeric ids (not ML ids) unless prefixed `meli_`.
- Timestamps: ISO-8601 UTC in JSON; rendered in `America/Sao_Paulo` in HTML.
- Errors: `{"detail": "human-readable message", "code": "machine_code"}` with proper
  status (400 validation, 401 unauthenticated, 404 not found *or cross-tenant*, 409
  conflict/state violations, 422 pydantic).
- Pagination: `?page=1&per_page=50` → `{"items": […], "page": 1, "per_page": 50, "total": 123}`.
- All list endpoints are implicitly filtered by the session's `tenant_id`.

## 1. Auth & health

| Method & path | Purpose |
|---|---|
| `GET /login` | Login page |
| `POST /login` | Form `email`, `password` → sets session cookie, redirect `/dashboard`; invalid → re-render with error |
| `POST /logout` | Clears session, redirect `/login` |
| `GET /healthz` | Public. `{"status":"ok","db":true,"redis":true}` (503 if a dependency is down) |

## 2. Mercado Libre accounts & OAuth

| Method & path | Purpose |
|---|---|
| `GET /accounts` | Page: connected accounts, token health, "Conectar conta ML" button |
| `GET /api/meli/oauth/start` | 302 to ML authorization URL; signed `state` carries `tenant_id`+nonce |
| `GET /api/meli/oauth/callback?code&state` | Verifies `state`, exchanges code, upserts account → redirect `/accounts?connected=1`. Failure → `/accounts?error=…` |
| `GET /api/accounts` | List (JSON below) |
| `POST /api/accounts/{id}/refresh` | Force token refresh (health check). 200 → account JSON; 409 `{"code":"reauth_required"}`; 502 `{"code":"meli_unavailable"}` when ML itself errors/is unreachable |
| `DELETE /api/accounts/{id}` | Sets `status='disabled'` (soft; historical orders keep FK) |

```json
// GET /api/accounts → 200
{"items": [{
  "id": 1, "meli_user_id": 123456789, "nickname": "LOJA_EXEMPLO",
  "site_id": "MLB", "status": "active",
  "access_token_expires_at": "2026-07-13T18:00:00Z",
  "last_refresh_at": "2026-07-13T12:00:00Z", "created_at": "2026-07-01T10:00:00Z"
}]}
// tokens are NEVER serialized in any response
```

## 3. Webhook (public)

`POST /webhooks/meli` — body as sent by ML:

```json
{"resource": "/orders/2000003508419500", "user_id": 123456789,
 "topic": "orders_v2", "application_id": 5503910054141466,
 "attempts": 1, "sent": "2026-07-13T14:00:00Z", "received": "2026-07-13T14:00:00Z"}
```

Always `200 {}` (even for unknown `user_id` or foreign `application_id` — recorded as
`skipped`; 4xx would make ML retry forever). Contract: persist + enqueue only, <500 ms,
no ML API calls (ARCHITECTURE §6.2).

## 4. Orders & shipments

| Method & path | Purpose |
|---|---|
| `GET /orders` | Page: order list, filters, HTMX-refreshed table |
| `GET /api/orders?status=&account_id=&q=&page=` | JSON list; `q` searches `meli_order_id`/buyer; `status` filters `meli_status` |
| `GET /api/orders/{id}` | Detail incl. items + shipment |
| `POST /api/orders/sync` | Manual poll trigger (enqueues `poll_orders` now) → `202 {"detail":"sync enfileirado"}` |
| `GET /api/shipments/eligible` | Shipments eligible for a new batch (ARCHITECTURE §7.1); `?include_printed=true` adds `substatus='printed'` |

```json
// GET /api/orders?status=&account_id=&q=&page= → 200
// Each row carries only what the list table renders; `shipment.urgent` = handling
// deadline under 4h (derived, drives the red badge). Full data is in the detail call.
{"items": [
  {"id": 42, "meli_order_id": 2000003508419500, "pack_id": null,
   "account": {"id": 1, "nickname": "LOJA_EXEMPLO"},
   "buyer_nickname": "COMPRADOR123", "meli_status": "paid",
   "meli_created_at": "2026-07-13T13:55:00Z",
   "items": [{"title": "Tênis Runner Masculino Preto", "size": "41", "quantity": 1}],
   "shipment": {"meli_status": "ready_to_ship", "meli_substatus": "ready_to_print",
                "handling_limit_at": "2026-07-14T16:00:00Z", "urgent": false}}],
 "total": 1, "page": 1, "page_size": 50}

// GET /api/orders/{id} → 200
{"id": 42, "meli_order_id": 2000003508419500, "pack_id": null,
 "account": {"id": 1, "nickname": "LOJA_EXEMPLO"},
 "meli_status": "paid", "buyer_nickname": "COMPRADOR123",
 "total_amount": "289.90", "currency": "BRL",
 "meli_created_at": "2026-07-13T13:55:00Z",
 "shipment": {"id": 7, "meli_shipment_id": 44444444444, "meli_status": "ready_to_ship",
              "meli_substatus": "ready_to_print", "logistic_type": "drop_off",
              "carrier_name": "Correios", "tracking_number": "AA123456789BR",
              "handling_limit_at": "2026-07-14T16:00:00Z"},
 "items": [{"id": 91, "meli_item_id": "MLB3333333333", "variation_id": 181052026853,
            "title": "Tênis Runner Masculino Preto", "seller_sku": "RUN-PTO",
            "size": "41", "quantity": 1, "unit_price": "289.90",
            "variant": {"id": 12, "model_name": "Tênis Runner Preto", "size": "41",
                        "internal_code": "SFV000012"}}]}

// GET /api/shipments/eligible → 200
{"items": [{"shipment_id": 7, "meli_shipment_id": 44444444444,
            "carrier_name": "Correios", "logistic_type": "drop_off",
            "handling_limit_at": "2026-07-14T16:00:00Z", "urgent": false,
            "orders": [{"meli_order_id": 2000003508419500, "buyer_nickname": "COMPRADOR123"}],
            "items_summary": "1× Tênis Runner Preto 41"}],
 "total": 1}
```

## 5. Print batches, labels, picking

| Method & path | Purpose |
|---|---|
| `GET /batches` | Page: batch list + "Novo lote" |
| `GET /batches/new` | Page: eligible shipments, pre-checked, urgency highlights |
| `GET /batches/{id}` | Page: status, per-shipment label states, failures + retry, links to PDF/picking/check |
| `POST /api/batches` | Create batch (body below) → `201`, enqueues `download_batch_labels` |
| `GET /api/batches?status=&page=` | JSON list |
| `GET /api/batches/{id}` | Detail (body below); HTMX polls this for download progress |
| `POST /api/batches/{id}/labels/retry` | Re-enqueue failed labels only → `202`. 409 if none failed |
| `POST /api/batches/{id}/shipments/{sid}/exclude` | `label_status='excluded'` (body `{"reason": "…"}`) → 200 |
| `GET /api/batches/{id}/labels.pdf` | Merged PDF (`application/pdf`, tenant-checked). 409 `{"code":"labels_not_ready"}` before ready |
| `GET /batches/{id}/picking` | Print-friendly picking page (§7.3 grouping; live — cancelled orders struck through) |
| `POST /api/batches/{id}/complete` | Manual close → 200; 409 if pending checks remain (unless `{"force": true}`) |
| `POST /api/batches/{id}/cancel` | Only from `created|downloading|ready*` with no completed checks → 200; else 409 |

```json
// POST /api/batches — request
{"shipment_ids": [7, 8, 9], "include_printed": false}
// → 201
{"id": 5, "code": "2026-07-13-1", "status": "created", "shipments_total": 3}
// 409 {"code":"shipment_in_active_batch","detail":"Envio 44444444444 já está no lote 2026-07-13-1"}

// GET /api/batches/{id} → 200
{"id": 5, "code": "2026-07-13-1", "status": "ready_with_failures",
 "created_by": "maria", "created_at": "2026-07-13T11:00:00Z",
 "labels_ready_at": "2026-07-13T11:02:10Z", "checking_started_at": null,
 "completed_at": null, "merged_pdf_available": true,
 "counts": {"total": 40, "ok": 37, "failed": 3, "excluded": 0,
            "checks_completed": 0, "checks_pending": 37},
 "failures": [{"shipment_id": 9, "meli_shipment_id": 44444444446,
               "error": "shipment no longer ready_to_print (substatus=picked_up)",
               "attempts": 2}]}
```

## 6. Barcode check (conferência)

| Method & path | Purpose |
|---|---|
| `GET /batches/{id}/check` | Page: scan station (single focused input; server-driven state) |
| `POST /api/checks/scan` | THE endpoint — every scan goes here; server infers meaning (label vs product) |
| `POST /api/checks/{check_id}/items/{item_id}/override` | Manual confirm, body `{"reason": "…"}` (required) → scan-state JSON |
| `POST /api/checks/{check_id}/takeover` | Take over a claimed check → scan-state JSON |
| `POST /api/checks/{check_id}/bind-barcode` | Teach mode confirm: `{"barcode": "789…", "check_item_id": 3}` → binds + counts as scan |
| `POST /api/checks/{check_id}/hold` | Flag `mismatch_hold` for supervisor, body `{"reason": "…"}` |
| `GET /api/batches/{id}/checks/progress` | HTMX progress fragment data: `{"completed": 12, "total": 37, "mismatch_hold": 1}` |

```json
// POST /api/checks/scan — request
{"batch_id": 5, "barcode": "44444444444", "active_check_id": null}
// active_check_id = check in progress at this station (null → IDLE, expect label)

// → 200, unified scan-state response (all outcomes use this shape):
{"result": "label_accepted",        // see table below
 "message": "Conferindo envio 44444444444",
 "check": {"id": 31, "shipment_meli_id": 44444444444, "status": "in_progress",
           "claimed_by": "maria",
           "items": [{"check_item_id": 3, "model_name": "Tênis Runner Preto",
                      "size": "41", "qty_expected": 1, "qty_checked": 0,
                      "thumbnail_url": "…", "completed": false}]},
 "bind_candidates": null}
```

| `result` | Meaning / extra fields |
|---|---|
| `label_accepted` | Check claimed, `check` populated → CHECKING |
| `item_ok` | Product matched, `qty_checked` incremented |
| `check_completed` | Last item confirmed; `check.status="completed"` → station returns to IDLE |
| `already_completed` | Label of a finished check (info, not error) |
| `mismatch` | Product belongs to another variant; `message` says scanned vs. expected |
| `unknown_barcode` | IDLE: code is nothing known. CHECKING: `bind_candidates` = unfulfilled items for teach-mode modal |
| `blocked_cancelled` | Order cancelled — full-screen block, do not ship |
| `claimed_by_other` | `{"claimed_by": "joão", "claimed_at": "…"}` → offer takeover |
| `not_in_batch` | Shipment exists but isn't in this batch |

## 7. Catalog & internal barcode labels

| Method & path | Purpose |
|---|---|
| `GET /catalog` | Page: variants, barcodes, model_name editing, label-sheet printing |
| `GET /api/variants?q=&page=` | List; `q` searches model/SKU/size |
| `PATCH /api/variants/{id}` | `{"model_name": "…"}` (admin) |
| `POST /api/variants/{id}/barcodes` | `{"barcode": "7891234567890", "source": "ean"}` → 201; 409 if bound to another variant |
| `DELETE /api/variants/{id}/barcodes/{barcode_id}` | Unbind |
| `GET /api/variants/labels.pdf?ids=12,13,14` | Printable Code128 sheet (value = `internal_code`), A4 grid via print CSS |

## 8. Dashboard & metrics

| Method & path | Purpose |
|---|---|
| `GET /dashboard` | Page: today's batches + progress, pending orders, token health banner, dead outbox warning, dispatch-time trend |
| `GET /api/metrics/summary?from=2026-07-01&to=2026-07-13` | JSON below |

```json
{"days": [{"date": "2026-07-13", "orders_ingested": 55, "shipments_dispatched": 48,
           "batches": [{"code": "2026-07-13-1",
                        "created_at": "2026-07-13T11:00:00Z",
                        "labels_ready_seconds": 130,
                        "checking_duration_seconds": 3600,
                        "total_cycle_seconds": 5400,
                        "labels_failed": 3, "mismatches_caught": 2,
                        "manual_overrides": 1}]}],
 "totals": {"orders": 610, "dispatched": 590, "mismatches_caught": 14,
            "avg_cycle_seconds": 5100}}
```

`mismatches_caught` is the money number for the portfolio: wrong shipments prevented.

## 9. Outbound events → EventHub (Phase 2)

Delivered by the outbox publisher (ARCHITECTURE §9) as
`POST {EVENTHUB_URL}/webhooks/inbound`, header `Authorization: Bearer {EVENTHUB_TOKEN}`,
`Content-Type: application/json`. At-least-once; EventHub dedupes by `event_id`.
2xx = delivered; anything else = retry with backoff.

### `new_order` (v1) — on first ingestion of an order

```json
{"event_id": "9f7b3c1e-8a4d-4e2f-b1a0-5c6d7e8f9a0b",
 "event_type": "new_order", "version": 1, "tenant_id": "1",
 "occurred_at": "2026-07-13T14:00:05Z",
 "payload": {
   "order_id": 42, "meli_order_id": 2000003508419500, "pack_id": null,
   "meli_account": {"id": 1, "meli_user_id": 123456789, "nickname": "LOJA_EXEMPLO"},
   "status": "paid", "total_amount": "289.90", "currency": "BRL",
   "buyer_nickname": "COMPRADOR123",
   "items": [{"meli_item_id": "MLB3333333333", "variation_id": 181052026853,
              "title": "Tênis Runner Masculino Preto", "seller_sku": "RUN-PTO",
              "size": "41", "quantity": 1, "unit_price": "289.90"}],
   "meli_created_at": "2026-07-13T13:55:00Z"}}
```

### `order_shipped` (v1) — once per order, when its shipment turns `shipped`

```json
{"event_id": "1a2b3c4d-…", "event_type": "order_shipped", "version": 1,
 "tenant_id": "1", "occurred_at": "2026-07-13T17:20:00Z",
 "payload": {
   "order_id": 42, "meli_order_id": 2000003508419500,
   "meli_shipment_id": 44444444444, "tracking_number": "AA123456789BR",
   "carrier_name": "Correios",
   "batch_code": "2026-07-13-1",
   "checked": true, "manual_overrides": 0,
   "shipped_at": "2026-07-13T17:18:30Z"}}
```

Versioning rule: additive changes keep `version: 1`; breaking changes bump `version`
and are documented here before any producer change ships.
