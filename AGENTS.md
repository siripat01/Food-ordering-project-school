# Project Handoff for Coding Agents

## Purpose

This repository is a portfolio-oriented food-ordering modular monolith. It combines a FastAPI and MongoDB backend with a Next.js frontend, LINE Login and Messaging API integration, role-scoped LangChain tools, an optional customer ordering assistant, and a feature-flagged external recommender.

Communicate progress and explanations to the user in Thai. Keep code, identifiers, commits, and technical documentation in English.

## Repository layout

- `apps/backend`: FastAPI application, domain services, MongoDB lifecycle, LINE integration, and role-scoped agent tools.
- `apps/frontend`: Next.js customer and staff application.
- `docker-compose.yaml`: Local MongoDB, backend, and frontend stack.
- `.env.example`: Placeholder-only configuration reference. Never put real credentials in it.
- `docs/README.md`: Documentation index for users, developers, API consumers, and operators.
- `docs/user-guide.md`: Implemented customer, staff, admin, web, and LINE flows.
- `docs/developer-guide.md`: Architecture, local setup, quality checks, and safe change workflow.
- `docs/api-guide.md`: Endpoint/RBAC matrix, request examples, idempotency, and error behavior.
- `docs/operations-runbook.md`: Deployment, health, migration, rollback, and troubleshooting.
- `docs/security-incident-response.md`: Credential rotation and optional history-cleanup runbook.
- `README.md`: Implemented architecture, role model, setup, migration, and roadmap.

The old backend files were intentionally moved or replaced during the monorepo refactor. The working tree contains a large uncommitted refactor; do not reset, restore, or overwrite those changes. A legacy frontend repository may still exist beside this repository and was intentionally retained because deleting or merging its history was not authorized.

## Non-negotiable constraints

- Keep the backend as a modular monolith. Do not introduce microservices, Kubernetes, Kafka, a vector database, or another framework without a demonstrated requirement.
- Inspect `git status` before editing and preserve unrelated user changes.
- Do not commit, push, rotate external credentials, modify MongoDB Atlas, rewrite Git history, or delete the retained frontend repository without explicit authorization.
- Never print, copy, or repeat secrets. Report only the affected file and credential type.
- Do not weaken startup configuration validation to make local startup pass.
- Treat authenticated server context as authoritative. Client payloads and LLM arguments must never choose another `userId`, a role, an authoritative price, or an initial order status.
- Keep customer, staff, and admin capabilities deterministically authorized in application code. Prompts are not an authorization boundary.
- Keep README claims limited to functionality that is implemented and verified.

## Security incident status

A MongoDB connection credential was committed in the legacy backend and remains in Git history. The current source no longer contains or accepts that credential or an insecure fallback. Treat the historical credential as compromised. External rotation is still required before deployment; follow `docs/security-incident-response.md`. Git history has not been rewritten.

## Delivery phases and current status

There are four phases, numbered 0 through 3.

### Phase 0: Security incident handling — implemented in the working tree

- Removed hard-coded credentials and insecure connection fallbacks.
- Added placeholder-only `.env.example` and ensured real `.env` files remain ignored.
- Added Pydantic Settings validation with clear startup failures.
- Documented external credential rotation and optional post-rotation history cleanup.
- Remaining external action: rotate the compromised MongoDB credential. Do not perform this from the repository.

### Phase 1: Secure core backend and monorepo — implemented and locally verified

- Consolidated backend and frontend under `apps/`.
- Added FastAPI lifespan-managed MongoDB and LINE clients, explicit CORS, request IDs, and health endpoints.
- Added `customer`, `staff`, and `admin` RBAC with trusted request identity.
- Hardened LINE OAuth state/nonce handling, cookies, token validation, and webhook deduplication.
- Split customer and staff agent tools. Customer tools cannot reach staff or admin operations.
- Added multi-item orders, server-owned price calculations, product/add-on snapshots, status history, validated transitions, idempotency, indexes, and legacy dual-read support.
- Added an idempotent, dry-run-by-default order migration at `apps/backend/scripts/migrate_orders_v2.py`.
- Added non-root backend/frontend containers and local Docker Compose.
- After the Phase 2 and LiteLLM changes, Ruff and formatting pass, Mypy passes for 38 backend source files, all 26 tests pass against MongoDB on Python 3.12, and the frontend type-check/production build passes. On 2026-08-22, Docker Compose rebuilt the current backend and frontend images, started the isolated full stack, returned HTTP 200 from liveness, readiness, frontend, and metrics endpoints, and confirmed that both application containers run as non-root users.

Local startup intentionally fails if `JWT_SECRET` is missing, weak, or still a placeholder. Generate a stable random secret of at least 32 characters, store it only in the untracked `.env`, and recreate the backend container. Never commit that value.

### Phase 2: Quality and observability — implemented and locally verified

Implemented:

- Ruff and formatter configuration.
- Strict Mypy configuration.
- Pytest coverage for core configuration, RBAC, cross-user isolation, pricing, transitions, idempotency, OAuth state, webhook deduplication, and customer-agent tool isolation.
- GitHub Actions for lint, type-check, tests, and Docker build.
- An isolated real-MongoDB API integration test, supplied with MongoDB by GitHub Actions.
- Structured JSON logging with secret/PII redaction and request/order correlation.
- Metrics for request latency, order volume, LLM latency/errors, and token/cost usage.
- Automated valid/missing configuration container startup tests and broader API integration coverage.

Remaining external verification:

- Run the GitHub Actions workflow for the first time.
- Run LINE OAuth and webhook end-to-end tests with dedicated sandbox credentials.

### Phase 3: Portfolio features — not started

- SSE staff order queue.
- LINE notifications for operational status changes.
- Recommendation impression, click, add-to-cart, and purchase events.
- Popularity/trending baseline before collaborative filtering.
- Recall@K and NDCG@K offline evaluation.

Do not start Phase 3 until Phase 2 is complete and stable.

## LLM provider note

DeepSeek and LiteLLM support are implemented through an in-process gateway at `apps/backend/app/integrations/agent/gateway.py`. LiteLLM's complexity router maps simple/medium requests to the economical model and complex/reasoning requests to the capable model, with a zero-call heuristic classifier by default and an optional cheap-LLM classifier. Provider fallbacks, retries, timeouts, and bounded opt-in local caching are separate controls. Customer-agent requests always use `no-store` because tool calls and personalized order context must not be cached. New deployments use `LLM_API_KEY`; `OPENAI_API_KEY` remains a backward-compatible input only. See `docs/llm-gateway.md`.

## Common commands

Create `.env` from `.env.example`, replace every enabled feature's placeholders locally, and never display or commit the resulting secrets.

```bash
docker compose up --build
```

Backend checks:

```bash
cd apps/backend
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy app
.venv/bin/pytest -q
```

Frontend and container checks:

```bash
cd apps/frontend
pnpm build
cd ../..
docker compose build
```

Runtime endpoints:

- Frontend: `http://localhost:3000`
- API documentation in non-production: `http://localhost:8000/docs`
- Liveness: `http://localhost:8000/api/v1/health/live`
- Readiness: `http://localhost:8000/api/v1/health/ready`

## Recommended next session

1. Read this file, `README.md`, and `docs/security-incident-response.md`.
2. Inspect the complete working tree and current diff before making changes.
3. Inspect the first GitHub Actions result when the working tree is published with explicit authorization.
4. Fix any CI portability issue before treating the GitHub check as verified.
5. Continue to Phase 3 only after the Phase 2 revision is stable, and report changed files, commands, results, and remaining risks in Thai.
