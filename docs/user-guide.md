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
2. Review active orders, ordered from oldest to newest.
3. Select one of the allowed next states shown on the order card.
4. If an update conflicts with another staff action, refresh the queue before retrying.

The queue excludes completed and cancelled orders by default. Status transitions are validated again by the backend; changing the browser request cannot bypass the lifecycle.

## Admin flow

Admins can use the same web queue as staff. The current web application does not yet contain product-management or user-management screens. Those admin capabilities are available through the protected API:

- List all users.
- Change another user's role.
- Activate or deactivate another user.
- List all products, including unavailable and discontinued products.
- Create, update, or discontinue a product.

An admin cannot remove their own admin role or deactivate their own account. See the [API Guide](api-guide.md) for endpoint details.

## LINE ordering assistant

When both LINE and LLM integrations are enabled:

1. Send a text message to the configured LINE bot.
2. If the LINE identity is not registered, follow the short-lived login link returned by the bot.
3. After authentication, ask to list products, create an order, view owned orders, or cancel an eligible owned order.

The customer assistant cannot invoke staff or admin operations. It cannot choose a user ID, role, or authoritative price. Duplicate LINE webhook deliveries are ignored, and order creation uses the webhook event as part of its idempotency context.

Automatic LINE notifications for later order-status changes are not implemented yet.

## Common problems

| Symptom | What to do |
| --- | --- |
| LINE sign-in reports that authentication is disabled | Ask the operator to configure LINE and set `LINE_ENABLED=true`. |
| The order page asks you to sign in | Complete LINE sign-in, then return to the product page. |
| A product cannot be ordered | It may be unavailable, discontinued, or changed since the page loaded. Refresh the product list. |
| Cancellation returns an error | Refresh the order list. Staff may already have moved it beyond an eligible state. |
| The shop queue is denied | The account must have the `staff` or `admin` role and remain active. |
| A retry shows the existing order | This is expected idempotency behavior, not a duplicate failure. |

When reporting a failed request, include the `X-Request-ID` response header. Do not send access tokens, cookies, API keys, full LINE payloads, or customer notes in bug reports.
