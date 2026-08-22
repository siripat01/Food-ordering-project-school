# API Guide

## Base URL and schema

The local API base URL is `http://localhost:8000/api/v1`. Interactive OpenAPI documentation is available at `http://localhost:8000/docs` outside production. JSON fields use snake_case, and unknown request fields are rejected.

## Authentication

Protected endpoints accept either:

- The secure `access_token` cookie created by LINE Login.
- `Authorization: Bearer <access-token>` for non-browser clients.

The backend decodes the token and reloads the active user from MongoDB for every protected request. A token alone cannot preserve access after the user is deactivated.

Browser clients should use the LINE Login flow and send cookies with credentials. Do not put access tokens in query strings.

## Endpoint matrix

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/health/live` | Public | Process liveness |
| `GET` | `/health/ready` | Public | MongoDB-backed readiness |
| `GET` | `/auth/line` | Public | Start LINE OAuth |
| `GET` | `/auth/line/callback` | Public callback | Consume OAuth state and establish a session |
| `POST` | `/auth/logout` | Public/idempotent | Delete the session cookie |
| `GET` | `/users/me` | Authenticated | Return the trusted current user |
| `GET` | `/products` | Public | List available products |
| `GET` | `/products/{product_id}` | Public | Get one product |
| `POST` | `/products` | Admin | Create a product |
| `PATCH` | `/products/{product_id}` | Admin | Update a product |
| `DELETE` | `/products/{product_id}` | Admin | Discontinue, not physically delete, a product |
| `POST` | `/orders` | Customer | Create an owned order with idempotency |
| `GET` | `/orders/me` | Customer | List owned orders, optionally filtered by status |
| `GET` | `/orders/{order_id}` | Authenticated | Read an allowed order; customers are restricted to ownership |
| `POST` | `/orders/{order_id}/cancel` | Customer owner | Cancel an eligible owned order |
| `GET` | `/staff/orders` | Staff or admin | List the operational queue |
| `GET` | `/staff/orders/stream` | Staff or admin | SSE snapshot, committed updates, and heartbeats |
| `PATCH` | `/staff/orders/{order_id}/status` | Staff or admin | Apply a valid status transition |
| `GET` | `/recommendations` | Customer | Return item-item, trending, external, or recent available products |
| `POST` | `/recommendations/events` | Customer | Idempotently record an allowed engagement event |
| `DELETE` | `/recommendations/data` | Customer | Idempotently purge the customer's raw recommendation data |
| `GET` | `/admin/users` | Admin | List users |
| `PATCH` | `/admin/users/{user_id}/role` | Admin | Change another user's role |
| `PATCH` | `/admin/users/{user_id}/active` | Admin | Activate or deactivate another user |
| `GET` | `/admin/products` | Admin | List all product states |
| `POST` | `/line/webhook` | Signed LINE request | Accept and deduplicate text-message events |

`GET /metrics` is outside `/api/v1` and exists only when metrics are enabled.

## Request examples

The examples below use placeholders and must not be committed with real tokens or customer data.

### List products

```bash
curl --fail http://localhost:8000/api/v1/products
```

### Read the current user

```bash
curl --fail \
  --header 'Authorization: Bearer <access-token>' \
  http://localhost:8000/api/v1/users/me
```

### Create an order

Every creation attempt requires an `Idempotency-Key` between 8 and 128 characters. Reuse the same key only when retrying the same logical request for the same customer.

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer <access-token>' \
  --header 'Content-Type: application/json' \
  --header 'Idempotency-Key: demo-order-0001' \
  --data '{
    "items": [
      {
        "product_id": "64f000000000000000000001",
        "quantity": 2,
        "addon_ids": ["extra-egg"],
        "note": "No spicy sauce"
      }
    ]
  }' \
  http://localhost:8000/api/v1/orders
```

The first successful creation returns `201`. A replay returns the original order with `200`. The server ignores any client price because price fields are not accepted in `OrderCreate`.

### List owned orders by status

```bash
curl --fail \
  --header 'Authorization: Bearer <access-token>' \
  'http://localhost:8000/api/v1/orders/me?order_status=pending'
```

### Update an order as staff

```bash
curl --fail-with-body \
  --request PATCH \
  --header 'Authorization: Bearer <staff-access-token>' \
  --header 'Content-Type: application/json' \
  --data '{"status":"confirmed"}' \
  http://localhost:8000/api/v1/staff/orders/64f000000000000000000002/status
```

Valid transitions are:

```text
pending -> confirmed | cancelled
confirmed -> preparing | cancelled
preparing -> ready | cancelled
ready -> completed
completed -> terminal
cancelled -> terminal
```

### Subscribe to the staff stream

The browser uses the secure session cookie and `EventSource` with credentials. Non-browser clients may use a Bearer token:

```bash
curl --no-buffer \
  --header 'Authorization: Bearer <staff-access-token>' \
  http://localhost:8000/api/v1/staff/orders/stream
```

Named events are `snapshot`, `order.updated`, and `heartbeat`. The snapshot data contains `{ "orders": [...] }`; an update contains `{ "order": {...} }`. Proxies must not buffer this response.

### Recommendations and events

`GET /recommendations?limit=3` returns a server-generated `recommendation_id`, selected strategy, and available products. Client events accept only `recommendation_id`, `product_id`, and `impression`, `click`, or `add_to_cart`; identity, rank, placement, model version, timestamp, and dedupe key are derived from the authenticated served slate. `purchase` is generated exclusively from a completed order. Repeating the same user/slate/product/type event returns `202` with `duplicate: true` and does not insert a second event.

`DELETE /recommendations/data` removes the authenticated customer's raw slates, events, daily counters, and short-lived cached rankings. It is idempotent and never accepts a user ID. During pseudonym-key rotation, operators must retain the prior keys in `RECOMMENDATION_USER_REF_PREVIOUS_SECRETS` until the configured raw-data retention window has elapsed so the purge covers every live key version. Product-level aggregate model artifacts contain no user-level data and are not deleted.

### Create a product as admin

```bash
curl --fail-with-body \
  --request POST \
  --header 'Authorization: Bearer <admin-access-token>' \
  --header 'Content-Type: application/json' \
  --data '{
    "name": "Demo Rice Bowl",
    "price": 59.00,
    "status": "available",
    "description": "A development-only example",
    "addons": [
      {"id":"extra-egg","name":"Extra egg","price":10.00,"available":true}
    ]
  }' \
  http://localhost:8000/api/v1/products
```

## Error behavior

| Status | Meaning |
| --- | --- |
| `400` | Invalid input, invalid ID, OAuth state, LINE signature, or unsupported payload |
| `401` | Missing, invalid, expired, or inactive authentication identity |
| `403` | Authenticated user lacks the required role |
| `404` | Resource not found, cross-customer order hidden, or metrics disabled |
| `409` | Idempotency conflict, unavailable catalog data, invalid state transition, or concurrent update |
| `422` | Request does not satisfy the Pydantic schema |
| `502` | LINE upstream authentication could not be completed |
| `503` | A disabled integration was requested or readiness failed |

Domain error responses include a safe detail and request ID:

```json
{
  "detail": "Order can no longer be cancelled",
  "requestId": "request-correlation-id"
}
```

The same correlation value is returned in the `X-Request-ID` header. Validation errors use FastAPI's standard error shape but still receive the request ID header.

## Idempotency and concurrency

- Order idempotency is scoped to the authenticated user and `Idempotency-Key`.
- LINE webhook deduplication is scoped to the webhook event ID.
- Recommendation engagement deduplication uses a server-derived user/slate/product/type key. Completed-order purchase events use a stable order/product key.
- Order status updates compare the previously read status in the atomic update.
- A concurrent transition returns `409`; reload the order before deciding whether to retry.

## Security notes

- Never add a `user_id`, role, unit price, total, or initial status to customer request schemas.
- A customer attempting to read another customer's order receives `404`.
- The LINE signature is verified before events are parsed.
- Product and user deactivation preserve operational history.
- The customer-facing LLM has no HTTP bypass around these service and role checks.
