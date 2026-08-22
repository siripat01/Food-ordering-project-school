# Secure Food Ordering Platform

A portfolio-grade modular monolith for ordering food through a Next.js web application or a LINE chat assistant. The backend treats authentication, authorization, prices, order transitions, and idempotency as deterministic application concerns rather than LLM decisions.

> Security incident notice: a MongoDB URI was committed in the legacy backend and remains in Git history. Treat that credential as compromised, rotate it outside this repository, and follow [the incident runbook](docs/security-incident-response.md). The application no longer contains or accepts an insecure fallback URI.

## Problem statement

The original school prototype demonstrated LINE Login, a chatbot, MongoDB persistence, and basic ordering. It also trusted client-supplied identities and prices, initialized external clients during imports, and exposed privileged tools to a customer-facing LLM. This version establishes a secure foundation while preserving the FastAPI, MongoDB, LINE, LangChain, and Next.js technology choices.

## Implemented capabilities

- LINE OAuth with a single-use, expiring state, nonce validation, and an HttpOnly session cookie
- Three application roles: `customer`, `staff`, and `admin`
- Customer-owned order creation, reading, and eligible cancellation
- Staff order queue and validated operational status transitions
- Admin product and user-role management
- Multi-item order schema with product/add-on snapshots and server-calculated totals
- Atomic status changes, status history, order idempotency, and LINE webhook deduplication
- Role-scoped AI tool factories; the customer toolset has no staff or admin operations
- Bounded, expiring in-memory LLM conversation context and async model calls
- Feature flags for LINE, LLM, and the external recommender
- In-process LiteLLM complexity/cost routing, independent fallbacks, and safe opt-in caching
- Structured redacted JSON logs and Prometheus request, order, and LLM metrics
- Explicit CORS allowlist, request IDs, liveness, readiness, and non-root containers
- Dual-read compatibility and an idempotent legacy-order migration command
- GitHub Actions for lint, type-check, unit/integration tests, frontend build, and container smoke tests

## Repository layout

```text
.
├── apps/
│   ├── backend/              # FastAPI modular monolith
│   │   ├── app/api/          # HTTP routes and dependencies
│   │   ├── app/core/         # Settings, JWT, and middleware
│   │   ├── app/db/           # MongoDB lifecycle and indexes
│   │   ├── app/domain/       # Validated domain models and rules
│   │   ├── app/integrations/ # LINE and role-scoped AI agent
│   │   ├── app/services/     # Application services
│   │   ├── scripts/          # Explicit database migration
│   │   └── tests/
│   └── frontend/             # Next.js customer/staff web application
├── docker-compose.yaml
└── .env.example
```

## Architecture

```mermaid
flowchart LR
    Browser[Next.js web app] -->|HttpOnly cookie / Bearer| API[FastAPI API]
    LINE[LINE Platform] -->|Signed webhook + OAuth| API
    API --> Auth[Authentication and RBAC]
    API --> Products[Product service]
    API --> Orders[Order service]
    API --> Agent[Role-scoped agent]
    Agent -->|Customer tools only| Products
    Agent -->|Trusted user context| Orders
    Agent --> Gateway[In-process LiteLLM router]
    Gateway -->|Economical/capable tiers and fallbacks| LLM[Configured LLM provider]
    Auth --> Mongo[(MongoDB)]
    Products --> Mongo
    Orders --> Mongo
    API -. feature-flagged .-> Recommender[External recommender]
```

The application remains one deployable backend. The modules separate responsibilities without introducing microservices or infrastructure that this project does not need.

## Security and role model

| Role | Permissions |
| --- | --- |
| `customer` | List products, create an own order, read own orders, cancel an eligible own order |
| `staff` | Read the operational queue and apply allowed status transitions |
| `admin` | Staff permissions plus product management and user role/activation management |

Every protected request resolves the current user from a signed JWT and reloads that user from MongoDB. Client payloads and LLM tool arguments cannot set `userId`, role, authoritative price, or initial status. Cross-customer reads return `404` to avoid disclosing resource existence. Missing authentication returns `401`; insufficient role permissions return `403`.

The customer AI allowlist is exactly:

- `list_products`
- `create_own_order`
- `view_own_orders`
- `cancel_eligible_own_order`

Staff tools are constructed separately. No user search, role update, product mutation, deletion, or plaintext password tool is exposed to the customer agent.

## Order lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending
    pending --> confirmed
    pending --> cancelled
    confirmed --> preparing
    confirmed --> cancelled
    preparing --> ready
    preparing --> cancelled
    ready --> completed
    completed --> [*]
    cancelled --> [*]
```

Products and add-ons are loaded by ID when an order is created. Their names and prices are snapshotted, and the server calculates every line total, subtotal, and total. `completed` and `cancelled` are terminal states. Order creation requires an `Idempotency-Key` header; retries for the same customer return the original order.

## Local Docker setup

Requirements: Docker with Compose v2.

```bash
cp .env.example .env
# Replace placeholders and generate a JWT secret with at least 32 random characters.
docker compose up --build
```

- Web application: <http://localhost:3000>
- API documentation (non-production): <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/api/v1/health/live>
- Readiness: <http://localhost:8000/api/v1/health/ready>

`LINE_ENABLED`, `LLM_ENABLED`, and `RECOMMENDER_ENABLED` default to `false`. Enabling a feature without all required variables fails configuration validation with a clear error.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `APP_ENV` | No | `development`, `test`, or `production` |
| `MONGODB_URI` | Yes | MongoDB URI; no source fallback exists |
| `MONGODB_*_DATABASE` | No | Existing database names for backward compatibility |
| `JWT_SECRET` | Yes | JWT signing secret, minimum 32 characters |
| `JWT_ISSUER`, `JWT_AUDIENCE` | No | JWT validation boundaries |
| `FRONTEND_URL`, `BACKEND_URL` | Yes in deployment | Canonical public URLs |
| `CORS_ORIGINS` | Yes | JSON array containing explicit origins |
| `COOKIE_SECURE` | Yes in production | Must be `true` in production |
| `LOG_LEVEL`, `LOG_JSON` | No | Structured application log configuration |
| `METRICS_ENABLED` | No | Exposes Prometheus metrics at `/metrics` |
| `LINE_ENABLED` | No | Enables LINE OAuth and webhook handling |
| `LINE_CHANNEL_SECRET` | When LINE is enabled | Webhook signature secret |
| `LINE_CHANNEL_ACCESS_TOKEN` | When LINE is enabled | Messaging API credential |
| `LINE_LOGIN_CHANNEL_ID` | When LINE is enabled | OAuth client ID |
| `LINE_LOGIN_CHANNEL_SECRET` | When LINE is enabled | OAuth client secret |
| `LINE_REDIRECT_URI` | When LINE is enabled | Exact registered callback URL |
| `LLM_ENABLED` | No | Enables the customer ordering assistant |
| `LLM_API_KEY` | When LLM is enabled | Provider-neutral model credential |
| `LLM_API_BASE` | Provider dependent | OpenAI-compatible provider endpoint |
| `LLM_MODEL`, `LLM_PRIMARY_MODEL`, `LLM_COMPLEX_MODEL` | No | Logical route, economical model, and capable model |
| `LLM_COMPLEXITY_*` | No | Heuristic or optional LLM complexity classification and domain keywords |
| `LLM_FALLBACK_MODELS` | No | Independent provider/model fallbacks used after request failures |
| `LLM_ROUTING_STRATEGY`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | No | Routing and reliability limits |
| `LLM_MAX_TOOL_ITERATIONS`, `LLM_MAX_OUTPUT_TOKENS` | No | Agent budget limits |
| `LLM_MEMORY_MESSAGES`, `LLM_MEMORY_TTL_MINUTES` | No | Per-user memory bounds and expiry |
| `LLM_CACHE_*` | No | Bounded local cache; customer-agent calls always use `no-store` |
| `LLM_*_COST_PER_MILLION` | No | Explicit inputs for estimated cost metrics |
| `RECOMMENDER_ENABLED`, `RECOMMENDER_URL` | No | Reserved feature-flagged integration settings |
| `NEXT_PUBLIC_API_URL` | Yes for frontend build | Browser-visible API base URL |

See [.env.example](.env.example) for safe placeholders.

See [the LLM gateway design](docs/llm-gateway.md) and [the observability runbook](docs/observability.md) for routing, cache safety, logging, metrics, and operational guidance.

## Documentation

- [Documentation index](docs/README.md)
- [User and role guide](docs/user-guide.md)
- [Developer guide](docs/developer-guide.md)
- [API guide](docs/api-guide.md)
- [Operations runbook](docs/operations-runbook.md)
- [LLM gateway](docs/llm-gateway.md)
- [Observability runbook](docs/observability.md)
- [Credential incident runbook](docs/security-incident-response.md)

## Database compatibility and migration

The API reads both legacy and schema-v2 orders. New orders are always schema v2. Legacy prices are preserved only as historical snapshots and marked `legacyPriceUnverified`; they are never reused as authoritative catalog prices for a new order.

Run a read-only migration report first:

```bash
cd apps/backend
python -m scripts.migrate_orders_v2
```

After a database backup and verification in a non-production environment:

```bash
python -m scripts.migrate_orders_v2 --apply
```

The migration is idempotent and does not delete legacy fields. Rollout order: deploy dual-read/new-write code, back up the database, dry-run, apply in batches, compare document counts/totals, and retain the legacy reader until verification is complete.

Indexes are created at backend startup for user/time, status/time, active order lookup, order idempotency, webhook events, LINE identities, and OAuth state TTL.

## Testing and quality checks

```bash
cd apps/backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q

# Run the real MongoDB integration test against an isolated disposable instance.
TEST_MONGODB_URI=mongodb://localhost:27017 .venv/bin/pytest -q -m integration

cd ../frontend
pnpm install --frozen-lockfile
pnpm build

cd ../..
docker compose build
```

The backend tests cover missing configuration, JWT secret strength, 401/403 RBAC, cross-user order access, trusted price calculation, idempotent creation, invalid terminal transitions, OAuth state consumption, duplicate webhook delivery, customer-agent tool isolation, log redaction, metrics, LiteLLM routing/cache policy, and a real MongoDB API flow. GitHub Actions supplies an isolated MongoDB service and also verifies missing/valid container startup behavior.

## Deployment

1. Rotate the exposed MongoDB credential before any deployment.
2. Store all production values in the deployment platform's secret manager; do not create a committed `.env`.
3. Use HTTPS URLs, set `APP_ENV=production` and `COOKIE_SECURE=true`, and configure an exact CORS allowlist.
4. Register the exact LINE callback and webhook URLs under the production domain.
5. Deploy MongoDB separately or use a managed provider with least-privilege network and database access.
6. Build the images with `docker compose build` or the equivalent platform build, then gate traffic on `/api/v1/health/ready`.
7. Provision the first admin explicitly in the database after that user has completed LINE Login; never accept a role from a browser, OAuth claim, or LLM argument.

## Demo placeholders

- `[Screenshot: customer product list]`
- `[Screenshot: secure LINE Login flow]`
- `[Screenshot: multi-item order confirmation]`
- `[Screenshot: staff order queue]`
- `[Video: LINE assistant creates and tracks an own order]`

## Engineering trade-offs

- MongoDB database names remain configurable and default to the legacy `Users`, `Products`, and `Orders` databases to allow a safe incremental migration.
- Money is calculated with `Decimal` and stored as MongoDB `Decimal128`; legacy numeric values are normalized at the boundary.
- AI memory is bounded in-process and expires. This avoids storing chat PII but is not shared across replicas.
- HttpOnly cookies are used for the browser, while Bearer tokens remain supported for non-browser API clients.
- Physical deletion is avoided for products and users; operational state is preserved for auditability.

## Known limitations

- The GitHub Actions workflow must run in GitHub before its first result can be reported.
- LINE status notifications, SSE staff queue updates, and recommendation event tracking are not implemented.
- The recommender configuration is feature-flagged, but the external recommender is not called by the Phase 1 agent.
- OAuth/webhook behavior requires real LINE sandbox credentials for end-to-end verification.
- In-memory agent context is per process and is intentionally lost on restart.
- LiteLLM response caching is local to one process and intentionally disabled for customer-agent calls.
- The copied frontend history is not yet merged into the backend repository's commit graph; the original frontend repository is retained outside this monorepo working tree until a history-preserving merge is explicitly authorized.

## Roadmap

1. Run the locally verified Phase 2 workflow in GitHub, then add deployment screenshots and a demo recording.
2. Add SSE for the staff queue and LINE notifications for every operational status.
3. Record recommendation events and implement a popularity baseline with Recall@K and NDCG@K evaluation.
