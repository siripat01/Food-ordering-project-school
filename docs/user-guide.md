# User and Role Guide

## What the application does

The platform lets customers browse products, authenticate with LINE, place orders, track their own orders, and cancel eligible orders. Staff operate the order queue. Admins inherit staff queue access and can manage products and user accounts through protected API endpoints.

The server is authoritative for identity, permissions, catalog availability, prices, totals, and order status. Values displayed by a browser or proposed by the ordering assistant are never the source of truth.

## Customer web flow

### Browse products

1. Open the home page at `http://localhost:3000` in the local stack.
2. Select **View all menu items** or open `/product`.
3. Search by product name or description.
4. Select an available product to open its order page.

Product browsing is public. Unavailable or discontinued products cannot be ordered.

### Sign in with LINE

1. Select **Sign in with LINE** when prompted.
2. Complete the LINE authorization screen.
3. The backend validates the single-use OAuth state and ID-token nonce.
4. LINE redirects to `/callback`; the frontend then loads the authenticated profile.

LINE sign-in is available only when the operator has enabled and configured the LINE integration. Authentication is stored in an HttpOnly cookie; the frontend does not need to read the token.

### Place an order

1. Choose the quantity, available add-ons, and an optional note.
2. Review the displayed estimate.
3. Submit the order.
4. Open `/order` to view the created order and its current status.

The current web screen creates one product line per submission. The backend and LINE ordering assistant support multiple order items in one order.

The frontend retains the same idempotency key when retrying a failed submission. A retry of the same request therefore returns the original order rather than creating a duplicate. The server reloads current products and add-ons and calculates every price before saving.

### Track or cancel an order

The **My orders** page shows only orders owned by the authenticated customer and can filter by status. A customer can cancel an order while it is `pending` or `confirmed`. Orders in `preparing`, `ready`, `completed`, or `cancelled` cannot be cancelled by the customer.

## Order status reference

| Status | Meaning | Allowed next states |
| --- | --- | --- |
| `pending` | Submitted and waiting for staff confirmation | `confirmed`, `cancelled` |
| `confirmed` | Accepted by the shop | `preparing`, `cancelled` |
| `preparing` | Being prepared | `ready`, `cancelled` |
| `ready` | Ready for pickup or handoff | `completed` |
| `completed` | Finished terminal state | None |
| `cancelled` | Cancelled terminal state | None |

## Staff flow

Staff and admins see **Shop queue** in the navigation bar.

1. Open `/admin`.
2. Review live updates, operational counts, status filters, notes, and orders sorted from oldest to newest.
3. Select one of the allowed next states shown on the order card.
4. If an update conflicts with another staff action, refresh the queue before retrying.

The page opens an authenticated SSE stream, reconnects automatically, and retains a manual REST refresh fallback. It can show recent terminal orders for operational review. Status transitions are validated again by the backend; changing the browser request cannot bypass the lifecycle.

## Recommendations and privacy

Authenticated customers may see a recent-catalog fallback, materialized local trending ranking, personalized item-item ranking, or feature-flagged external recommendation on the home page. Item-item delivery is controlled by a deterministic rollout percentage and defaults to disabled until an operator builds, evaluates, and activates a model.

The browser records an impression only after at least half of a recommendation card remains visible for 750 milliseconds. Impression, click, and order-intent delivery is best-effort and never blocks navigation or order creation. Events contain the product and served-slate identifiers but no user ID or token; the server resolves the authenticated user, validates that the product belonged to that user's unexpired slate, derives attribution and deduplication fields, and applies daily caps. Completed orders remain the authoritative purchase signal.

An authenticated customer can call `DELETE /api/v1/recommendations/data` to remove their raw recommendation slates, events, counters, and cached rankings. The endpoint derives the customer from the session and cannot be used to target another account. Aggregate product-model artifacts contain no user-level history.

## Admin flow

Admins can use the same web queue as staff. Admin accounts also see **Manage menu** in the navigation bar and can open `/admin/products` to:

- Search and filter the full catalog, including unavailable and discontinued products.
- Create products with a server-validated price, description, image URL, and add-ons.
- Update product details, availability, and add-ons.
- Discontinue a product without deleting historical order snapshots.

The product-management screen is guarded in the browser for usability and again by deterministic backend RBAC. The current web application does not yet contain a user-management screen. User administration remains available through the protected API:

- List all users.
- Change another user's role.
- Activate or deactivate another user.

An admin cannot remove their own admin role or deactivate their own account. See the [API Guide](api-guide.md) for endpoint details.

## LINE ordering assistant

When both LINE and LLM integrations are enabled:

1. Send a text message to the configured LINE bot.
2. If the LINE identity is not registered, follow the short-lived login link returned by the bot.
3. After authentication, ask to list products, create an order, view owned orders, or cancel an eligible owned order.

The customer assistant cannot invoke staff or admin operations. It cannot choose a user ID, role, or authoritative price. Duplicate LINE webhook deliveries are ignored, and order creation uses the webhook event as part of its idempotency context.

Creating or cancelling an order is a two-step action. The assistant first prepares a short-lived pending action without changing data. The customer must then send exactly **ยืนยัน**, **ยืนยันรายการ**, or **confirm** in a follow-up message. Any longer message containing those words is not accepted as confirmation. Send **ยกเลิก**, **ยกเลิกรายการ**, or **cancel** to discard the pending action. Pending actions expire automatically.

For authenticated one-to-one chats, LINE displays a bounded loading animation while the assistant prepares a response. Loading-indicator delivery is best-effort and never blocks the actual agent response.

When LINE is enabled and the account has a LINE identity, the system pushes a concise notification after the order is confirmed, preparing, ready, completed, or cancelled. Notification delivery happens after the database transition, so an upstream LINE failure does not undo the order status.

## Common problems

| Symptom | What to do |
| --- | --- |
| LINE sign-in reports that authentication is disabled | Ask the operator to configure LINE and set `LINE_ENABLED=true`. |
| The order page asks you to sign in | Complete LINE sign-in, then return to the product page. |
| A product cannot be ordered | It may be unavailable, discontinued, or changed since the page loaded. Refresh the product list. |
| Cancellation returns an error | Refresh the order list. Staff may already have moved it beyond an eligible state. |
| The shop queue is denied | The account must have the `staff` or `admin` role and remain active. |
| Menu management is denied | The account must have the `admin` role and remain active. Refresh after a role change. |
| A LINE message reaches the webhook but no reply arrives | Recreate the backend after updating its environment, then verify that the Messaging API channel access token can call `GET /v2/bot/info`. Do not use a channel ID or LINE Login credential as the Messaging API token. |
| A retry shows the existing order | This is expected idempotency behavior, not a duplicate failure. |

When reporting a failed request, include the `X-Request-ID` response header. Do not send access tokens, cookies, API keys, full LINE payloads, or customer notes in bug reports.
