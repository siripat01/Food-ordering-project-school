# AI Security Model

## Scope

The customer ordering assistant is an untrusted decision-maker inside the FastAPI modular monolith. It may select from an allowlisted customer toolset, but it does not own identity, authorization, catalog truth, prices, order state transitions, idempotency, or confirmation state.

This design addresses direct prompt injection, indirect injection through catalog or tool-output text, unauthorized tool calls, accidental side effects, unnecessary model data exposure, request-cost abuse, and reasoning leakage. It does not claim that prompt injection can be detected or eliminated by a classifier.

## Deterministic boundaries

- The authenticated `CurrentUser` is captured by the server-side tool factory. `user_id` is never a model argument.
- The customer model receives only `list_products`, `create_own_order`, `view_own_orders`, and `cancel_eligible_own_order`.
- Unknown tool names are returned to the model as unauthorized and are never dispatched.
- Order services reload products and add-ons, validate availability, calculate prices, enforce ownership and transitions, and apply idempotency.
- Tool arguments pass application validation before a mutation can become pending.
- Tool results use purpose-built model DTOs. They omit user IDs, status-history actor IDs/roles, and customer notes.
- Model and tool payload logging is disabled. Personalized tool-capable requests use `no-store`.
- Input, output, tool iterations, memory length, memory TTL, timeout, retries, and per-user request rate are bounded.
- Provider reasoning blocks are removed before output or memory retention.

## Mutation confirmation protocol

`create_own_order` and `cancel_eligible_own_order` never execute on the model's first tool call.

1. The model proposes a mutation with validated arguments.
2. Application code stores a short-lived `PendingAction` bound to the authenticated user and original idempotency key.
3. The assistant returns a deterministic confirmation message without asking the model to approve itself.
4. A later raw user message is normalized by application code.
5. Only an exact allowlisted confirmation command consumes and executes the pending action.

Accepted confirmations are `ยืนยัน`, `ยืนยันรายการ`, and `confirm`. Accepted cancellations are `ยกเลิก`, `ยกเลิกรายการ`, and `cancel`. A sentence containing one of those words is not confirmation. Confirmation execution rebuilds the scoped tools from the authenticated identity and uses the original idempotency key.

Pending actions are Redis-backed, single-use, and expiring. Confirmation consumption uses Redis `GETDEL`, so one confirmation cannot execute in two backend processes. The per-user rate limiter is also Redis-backed with an atomic sliding-window script.

## Indirect prompt injection

Catalog names and descriptions can contain attacker-like text if upstream administrative content is compromised. Tool payloads identify themselves as data, compact text fields, and the system prompt states that catalog fields and tool results are never instructions. This is defense in depth, not the primary boundary: even if the model follows injected text and proposes a mutation, deterministic confirmation prevents immediate execution and authorization still limits the action to the authenticated customer.

## Rate limiting

`LLM_REQUESTS_PER_MINUTE` limits requests per authenticated user across backend processes. It reduces model-cost and automated tool abuse but is not a distributed denial-of-service control. Edge or API-gateway limits remain appropriate for IP-level abuse. `LLM_CONFIRMATION_TTL_MINUTES` bounds pending-action lifetime.

## Regression tests

The automated suite verifies that:

- customer and staff/admin toolsets remain disjoint;
- unknown administrative tool calls are denied;
- mutation tools do not execute before confirmation;
- an injection sentence containing `ยืนยัน` is not exact confirmation;
- indirect catalog injection cannot immediately create an order;
- confirmation preserves trusted identity and the original idempotency key;
- model-facing order DTOs omit internal identities, actors, history, and notes;
- pending confirmations expire; and
- per-user rate limits stop additional model calls.

## Residual risks

- A customer can still explicitly confirm a harmful action presented to them. The confirmation message intentionally states the operation and requires a separate message, but it is not a cryptographic proof of comprehension.
- Read-only model output may repeat misleading catalog text. Product content remains an admin trust boundary and should be reviewed.
- Redis-backed conversation history can retain adversarial text until its bounded TTL expires.
- Provider behavior and tool-calling quality require sandbox evaluation whenever models or providers change.
- Process-local controls assume the documented single-replica deployment.
