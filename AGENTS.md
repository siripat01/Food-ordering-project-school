# Project Handoff for Coding Agents

## Purpose

This repository is a portfolio-oriented food-ordering modular monolith. It combines a FastAPI and MongoDB backend with a Next.js frontend, LINE Login and Messaging API integration, role-scoped LangChain tools, and an optional customer ordering assistant.

Communicate progress and explanations to the user in Thai. Keep code, identifiers, commits, and technical documentation in English.

## Repository layout

- `apps/backend`: FastAPI application, domain services, MongoDB lifecycle, LINE integration, and role-scoped agent tools.
- `apps/frontend`: Next.js customer and staff application.
- `docker-compose.yaml`: Local MongoDB (single-node replica set), Redis, backend, Taskiq worker, outbox dispatcher, and frontend stack.
- `.env.example`: Placeholder-only configuration reference. Never put real credentials in it.
- `docs/README.md`: Documentation index for users, developers, API consumers, and operators.
- `docs/user-guide.md`: Implemented customer, staff, admin, web, and LINE flows.
- `docs/developer-guide.md`: Architecture, local setup, quality checks, and safe change workflow.
- `docs/api-guide.md`: Endpoint/RBAC matrix, request examples, idempotency, and error behavior.
- `docs/background-jobs.md`: Taskiq worker, transactional outbox, dispatcher, retries, and idempotency.
- `docs/operations-runbook.md`: Deployment, health, migration, rollback, and troubleshooting.
- `docs/security-incident-response.md`: Credential rotation and optional history-cleanup runbook.
- `README.md`: Implemented architecture, role model, setup, migration, and roadmap.

The old backend files were intentionally moved or replaced during the monorepo refactor. Inspect the working tree before every change and never reset, restore, or overwrite unrelated user work. A legacy frontend repository may still exist beside this repository and was intentionally retained because deleting it was not authorized.

## Non-negotiable constraints

- Keep the backend as a modular monolith. Do not introduce microservices, Kubernetes, Kafka, a vector database, or another framework without a demonstrated requirement. Background work uses Taskiq on Redis Streams with a MongoDB transactional outbox; do not add Celery, RabbitMQ, event sourcing, or custom distributed locks.
- Keep outbox events (facts, past tense: `order.created`) and tasks (commands, imperative: `order.process`) distinct. Task handlers in `app/jobs/` stay thin and delegate to services; the outbox dispatcher stays infrastructure; repositories never import Taskiq.
- Write an outbox event in the same MongoDB transaction as the business state it describes. Transactions require a replica set; the standalone fallback is non-atomic and is not for production.
- Assume at-least-once delivery. Never claim exactly-once, and keep handlers idempotent.
- Inspect `git status` before editing and preserve unrelated user changes.
- Do not commit, push, rotate external credentials, modify MongoDB Atlas, rewrite Git history, or delete the retained frontend repository without explicit authorization.
- Never print, copy, or repeat secrets. Report only the affected file and credential type.
- Do not weaken startup configuration validation to make local startup pass.
- Treat authenticated server context as authoritative. Client payloads and LLM arguments must never choose another `userId`, a role, an authoritative price, or an initial order status.
- Keep customer, staff, and admin capabilities deterministically authorized in application code. Prompts are not an authorization boundary.
- Keep README claims limited to functionality that is implemented and verified.

## Security incident status

A MongoDB connection credential was committed in the legacy backend. On 2026-08-22, the repository owner explicitly authorized rewriting `main` to one sanitized root commit and force-pushing it; the local reflog and unreachable objects were pruned. Hosting caches, forks, and old clones may still retain the former object. Treat the credential as compromised. External rotation is still required before deployment; follow `docs/security-incident-response.md`.

## Delivery phases and current status

There are four phases, numbered 0 through 3.

### Phase 0: Security incident handling — implemented

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
- Added deterministic AI mutation confirmations, minimized customer-agent DTOs, prompt-injection regression tests, and per-user LLM request limits. Prompt text remains guidance, never an authorization boundary.
- Added non-root backend/frontend containers and local Docker Compose.
- The current local suite covers Ruff/format, strict Mypy, backend tests, frontend type-check/production build, and backend image startup. The dedicated real-MongoDB boundary is available through the integration marker. The Compose stack includes liveness/readiness/frontend/metrics/auth-boundary smoke checks and non-root application containers.

Local startup intentionally fails if `JWT_SECRET` is missing, weak, or still a placeholder. Generate a stable random value of at least 32 characters, store it only in the untracked `.env`, and recreate the backend container. Never commit it.

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

Remote verification: GitHub Actions run `32586236010` passed backend, frontend, and container jobs for Phase 3 commit `e5bd195` on 2026-08-23. LINE OAuth and webhook end-to-end behavior still requires dedicated sandbox credentials.

### Phase 3: Portfolio features — implemented and locally verified

- The authenticated staff queue uses SSE snapshots, committed order updates, bounded subscriber queues, heartbeats, reconnect UX, and REST fallback.
- Admins have a responsive product-management screen at `/admin/products`; user-role management remains API-only.
- LINE notifications are dispatched after committed operational status changes without making LINE availability part of the database transaction.
Remaining external/operational work: rotate the historically exposed MongoDB credential outside the repository and validate LINE flows with dedicated sandbox credentials.

### Production delivery contract

- GitHub Actions runs backend, frontend, and local-container gates for pull requests without publishing packages.
- `main`, `v*.*.*`, and manually dispatched runs publish the backend to GHCR with immutable full-commit tags, multi-architecture manifests, provenance, and SBOM metadata. The image publication job runs independently from the quality checks so the digest is available sooner; deploy only references from runs whose required checks passed.
- `compose.prod.yaml` pulls a caller-selected immutable backend reference on a private Docker network. Dokploy exposes the backend through its Domains/Traefik integration; the stack does not run the Vercel frontend or local MongoDB and does not publish backend port 8000.
- Vercel builds `apps/frontend` separately with the production `NEXT_PUBLIC_API_URL`.
- Dokploy production values live in the Compose Environment. The `deploy/*.example` files are placeholder-only references and must not contain populated credentials.

## LLM provider note

DeepSeek and LiteLLM support are implemented through an in-process gateway at `apps/backend/app/integrations/agent/gateway.py`. LiteLLM's complexity router maps simple/medium requests to the economical model and complex/reasoning requests to the capable model, with a zero-call heuristic classifier by default and an optional cheap-LLM classifier. Provider fallbacks, retries, timeouts, and bounded opt-in local caching are separate controls. Customer-agent requests always use `no-store` because tool calls and personalized order context must not be cached. New deployments use `LLM_API_KEY`; `OPENAI_API_KEY` remains a backward-compatible input only. See `docs/llm-gateway.md`.

MiniMax's OpenAI-compatible endpoint can be selected with `LLM_API_BASE=https://api.minimax.io/v1` and `minimax/` model identifiers. Final provider `<think>` blocks are removed before customer output or memory retention. Keep MiniMax disabled in production until tool-calling behavior is verified with a dedicated provider sandbox.

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

Background runtimes (each is a separate process, started by Docker Compose as
the `worker` and `dispatcher` services):

```bash
taskiq worker app.core.taskiq:broker   # consumes Redis Streams, runs task handlers
python -m app.jobs.dispatcher          # polls the outbox, enqueues tasks
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
3. Keep the backend, frontend, and container GitHub Actions jobs green on every change.
4. Validate LINE OAuth, webhook, and status notifications with dedicated sandbox credentials.
