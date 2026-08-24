# Developer Guide

## Architecture

The repository is a modular monolith:

```text
apps/backend/app/
├── api/             HTTP routes and authenticated dependencies
├── core/            Settings, security, middleware, logging, and metrics
├── db/              MongoDB lifecycle and index creation
├── domain/          Pydantic request/response models and domain rules
├── integrations/    LINE and the role-scoped LiteLLM/LangChain agent
└── services/        Product, order, user, OAuth-state, and webhook use cases

apps/frontend/src/app/
├── components/      Shared UI components
├── libs/            API and login helpers
├── store/           Client display-state store
└── */page.tsx       Next.js route screens
```

FastAPI owns the application lifecycle. MongoDB, LINE, HTTP, and LLM clients are created after configuration validation and closed during shutdown. Route handlers delegate business rules to services. Authentication and role dependencies run before protected handlers. Prompts are not an authorization boundary.

## Requirements

- Docker with Compose v2 for the simplest full-stack setup.
- Python 3.12 for native backend development.
- Node.js 24.15 and pnpm 11.0.9 for native frontend development.
- A local or disposable MongoDB instance for integration work.

Real LINE and LLM credentials are optional unless those feature flags are enabled.

## Docker setup

From the repository root:

```bash
cp .env.example .env
```

Replace both `JWT_SECRET` and `RECOMMENDATION_USER_REF_SECRET` in the untracked `.env` with independent generated random values of at least 32 characters. The placeholders are intentionally rejected. Keep `LINE_ENABLED=false`, `LLM_ENABLED=false`, and `RECOMMENDER_ENABLED=false` until their complete configuration is available.

Start the stack:

```bash
docker compose up --build
```

Verify:

```bash
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail http://localhost:3000
```

Local URLs:

- Frontend: `http://localhost:3000`
- OpenAPI UI outside production: `http://localhost:8000/docs`
- Metrics: `http://localhost:8000/metrics`

## Native backend setup

Create and install the environment:

```bash
cd apps/backend
python3.12 -m venv .venv
.venv/bin/pip install --requirement requirements-dev.txt
cp ../../.env.example .env
```

For a MongoDB process running on the host, change the untracked backend `.env` from `mongodb://mongo:27017` to `mongodb://localhost:27017`. Replace both required secret placeholders and keep unused integrations disabled.

Start the API:

```bash
.venv/bin/uvicorn app.main:app --reload
```

## Native frontend setup

```bash
cd apps/frontend
corepack enable
corepack prepare pnpm@11.0.9 --activate
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 pnpm dev
```

The browser client sends cookies with API requests. `FRONTEND_URL`, `CORS_ORIGINS`, and `NEXT_PUBLIC_API_URL` must describe the same browser-visible deployment topology.

## Quality checks

Backend:

```bash
cd apps/backend
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q
```

Real-MongoDB integration tests use a random database namespace and clean it after the run:

```bash
TEST_MONGODB_URI=mongodb://localhost:27017 .venv/bin/pytest -q -m integration
```

Frontend:

```bash
cd apps/frontend
pnpm exec tsc --noEmit
pnpm build
```

Containers:

```bash
docker compose build
docker compose up --detach --wait

# Validate the production image-pull contract without starting it.
BACKEND_IMAGE=ghcr.io/example/hiwkaw-backend:sha-test \
BACKEND_ENV_FILE=.env \
CLOUDFLARE_TUNNEL_TOKEN_FILE=/path/to/test-token-file \
docker compose --file compose.prod.yaml config --quiet
```

The GitHub Actions workflow runs the same lint, type-check, test, frontend-build, and container-startup boundaries. Pull requests stop there. Successful `main`, SemVer-tag, and manual runs additionally publish an AMD64/ARM64 backend image to GHCR; production must deploy its immutable SHA tag or digest rather than build on the VM.

Build and evaluate the bounded CPU-only recommendation artifacts without exporting user identities:

```bash
cd apps/backend
.venv/bin/python -m scripts.build_recommendation_model
.venv/bin/python -m scripts.evaluate_recommendations --days 180 --test-days 14
```

Both commands are read-only by default. After reviewing the temporal metrics and activation decision, use `python -m scripts.build_recommendation_model --write --activate`. See the [CPU Recommendation System](recommendation-system-plan.md) for artifact lifecycle, rollout, rollback, and operational bounds.

## Security invariants

Every change must preserve these rules:

- Resolve identity from the signed request and active database user.
- Never accept `user_id`, role, authoritative price, or initial order status from a customer or LLM tool argument.
- Keep customer, staff, and admin tools separate and authorize inside deterministic code.
- Return `401` for missing or invalid authentication and `403` for an authenticated role without permission.
- Hide cross-customer resource existence where practical.
- Calculate prices from current product and add-on documents using `Decimal`.
- Require idempotency for order creation and deduplicate LINE webhook events.
- Do not log bodies, prompts, notes, tokens, cookies, raw upstream responses, or other PII.
- Never add real credentials to source, examples, tests, fixtures, screenshots, or documentation.

## API and model conventions

- All application endpoints use `/api/v1`; `/metrics` is intentionally outside that prefix.
- Request and response JSON uses snake_case field names.
- Domain models reject unknown fields.
- Money is normalized to two decimal places and persisted as MongoDB `Decimal128`.
- Timestamps are UTC-aware.
- MongoDB IDs are represented as strings at API boundaries.
- Domain errors map to `400`, `403`, `404`, or `409` with a request ID.
- Product deletion is a soft transition to `discontinued`.

## Safe change workflow

1. Read `AGENTS.md` and inspect `git status` before editing.
2. Identify the domain rule and authorization boundary before adding a route or tool.
3. Change the smallest relevant domain, service, route, and UI modules.
4. Add tests for both the allowed path and denial or conflict path.
5. Run proportional backend, frontend, integration, and container checks.
6. Update the API, user, or operations documentation in the same change.
7. Review the diff for credentials, PII, unrelated edits, and claims that exceed verification.

Do not commit, push, modify MongoDB Atlas, rotate credentials, rewrite history, or remove the retained legacy frontend repository without explicit repository-owner authorization.

## Database compatibility

New orders are schema version 2. The API can still read legacy documents while migration is in progress. The migration command is read-only without `--apply`:

```bash
cd apps/backend
.venv/bin/python -m scripts.migrate_orders_v2
```

Before using `--apply`, follow the backup and verification sequence in the [Operations Runbook](operations-runbook.md#legacy-order-migration).

## Related guides

- [API Guide](api-guide.md)
- [Operations Runbook](operations-runbook.md)
- [LLM Gateway](llm-gateway.md)
- [Observability Runbook](observability.md)
- [CPU Recommendation Plan](recommendation-system-plan.md)
