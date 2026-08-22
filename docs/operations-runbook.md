# Operations Runbook

## Scope

This runbook covers configuration, deployment, health checks, observability, migration, rollback, and common failures for the current modular monolith. It does not prescribe a particular cloud provider.

## Before the first deployment

1. Complete the external MongoDB credential rotation in the [Credential Incident Runbook](security-incident-response.md). The sanitized history rewrite does not revoke copies retained by hosting caches, forks, or old clones.
2. Store production secrets in the deployment platform's secret manager, not in the repository or image.
3. Use HTTPS public URLs.
4. Set `APP_ENV=production` and keep `COOKIE_SECURE=true`.
5. Configure an exact `CORS_ORIGINS` allowlist.
6. Provision MongoDB with least-privilege credentials, restricted network access, backups, and monitoring.
7. Register the exact LINE callback and webhook URLs if LINE is enabled.
8. Review current DeepSeek model names and prices before enabling LLM cost metrics.
9. Run lint, type-check, tests, frontend build, container build, and startup smoke checks.

## Configuration groups

| Group | Important variables |
| --- | --- |
| Core | `APP_ENV`, `MONGODB_URI`, `JWT_SECRET`, `RECOMMENDATION_USER_REF_SECRET` |
| Public URLs | `FRONTEND_URL`, `BACKEND_URL`, `CORS_ORIGINS`, `COOKIE_SECURE` |
| Logging, metrics, SSE | `LOG_LEVEL`, `LOG_JSON`, `METRICS_ENABLED`, `SSE_*` |
| LINE | `LINE_ENABLED`, channel credentials, login credentials, `LINE_REDIRECT_URI` |
| LLM | `LLM_ENABLED`, `LLM_API_KEY`, base URL, model tiers, routing, limits, cache, cost inputs |
| Recommender | Provider mode/URL, dedicated pseudonym key, retention/caps, rollout, artifact/cache/profile bounds |
| Frontend build | `NEXT_PUBLIC_API_URL` |

Startup fails intentionally when required values are missing, either required secret is weak/reused as a placeholder, production cookies are insecure, or an enabled integration is incomplete. Do not work around this validation.

`NEXT_PUBLIC_API_URL` is embedded during the frontend build. Rebuild the frontend when this value changes.

When rotating `RECOMMENDATION_USER_REF_SECRET`, increment `RECOMMENDATION_USER_REF_KEY_VERSION` and temporarily map the old version to its old key in `RECOMMENDATION_USER_REF_PREVIOUS_SECRETS`. Keep old keys in the secret manager until the event/slate retention windows expire so authenticated privacy purge can derive every still-live pseudonym. Current and previous recommendation keys must be distinct and must never equal `JWT_SECRET`.

## Container deployment

Build without embedding a secret file in either image:

```bash
docker compose build
```

Provide runtime variables through the deployment platform. For the local Compose stack only, the root untracked `.env` is loaded by the backend service.

Start and wait for health checks:

```bash
docker compose up --detach --wait
```

Inspect service state and logs without exposing environment values:

```bash
docker compose ps
docker compose logs backend
docker compose logs frontend
```

Do not use commands that print the complete container environment in shared terminals or tickets.

## Health and readiness

```bash
curl --fail https://api.example.com/api/v1/health/live
curl --fail https://api.example.com/api/v1/health/ready
```

- Liveness confirms that the API process can serve HTTP.
- Readiness also pings MongoDB and returns `503` when the database is unavailable.
- Remove an unready instance from traffic. Do not restart it repeatedly solely because MongoDB is temporarily unavailable.

The frontend and API should be smoke-tested through their public ingress after deployment. OpenAPI `/docs` is intentionally disabled in production.

The staff stream uses `text/event-stream`. Disable proxy buffering for `/api/v1/staff/orders/stream`, preserve long-lived connections, and set an idle timeout longer than `SSE_HEARTBEAT_SECONDS`. SSE fan-out is process-local; keep one backend replica for this portfolio deployment unless a shared event transport is deliberately introduced.

## Logs and metrics

The backend emits structured JSON logs by default. Use `X-Request-ID` to correlate a client report with the request log. Order lifecycle logs also include an order ID.

Prometheus metrics are available at `/metrics` when enabled. Do not expose this endpoint publicly without ingress-level access control. The application endpoint itself is not authenticated.

See the [Observability Runbook](observability.md) for the metric list and redaction guarantees.

## Deployment sequence

1. Back up MongoDB and verify restore instructions.
2. Run the complete CI boundary against the exact revision.
3. Build immutable backend and frontend images.
4. Apply environment and secret-manager configuration.
5. Deploy the backend without directing production traffic to it.
6. Wait for readiness and inspect startup logs.
7. Deploy the frontend with the correct public API build argument.
8. Smoke-test public products, login if enabled, an isolated test order, staff transition, logs, and metrics.
9. Shift traffic gradually where the platform supports it.
10. Record the deployed revision, image digests, configuration version, and verification result without recording secret values.

## Legacy order migration

The application can read legacy and version-2 orders, so migration is not required before deploying the dual-read code.

1. Create and verify a database backup.
2. Deploy the dual-read/new-write application.
3. Run the read-only report from the backend environment:

```bash
cd apps/backend
.venv/bin/python -m scripts.migrate_orders_v2
```

4. Review the scanned count and sample legacy orders. Legacy prices are unverified historical snapshots.
5. Apply in a non-production environment first:

```bash
.venv/bin/python -m scripts.migrate_orders_v2 --apply --batch-size 200
```

6. Compare counts, status distribution, totals, and timestamps.
7. Apply to production during a controlled window.
8. Re-run the dry report and application tests.

The migration is idempotent and does not delete legacy fields. Keep the legacy reader until production verification is complete.

## Rollback

Application rollback should be image-based:

1. Stop traffic expansion when health or functional checks fail.
2. Preserve logs, request IDs, order IDs, timestamps, and deployment metadata.
3. Restore the previous known-good image and its compatible configuration.
4. Re-run liveness, readiness, and core smoke checks.
5. Do not automatically roll back database documents written in the new schema; the dual-read design is intended to keep them readable.
6. If data restoration is required, stop writes and use the provider's reviewed restore procedure. Do not improvise destructive MongoDB commands.

Recommendation rollback does not require an application or data rollback. Switch the active pointer to a retained, validated version:

```bash
cd apps/backend
.venv/bin/python -m scripts.build_recommendation_model \
  --write --rollback-version MODEL_VERSION
```

Keep `RECOMMENDATION_ITEM_ITEM_ROLLOUT_PERCENT=0` while shadow-building. Increase it through reviewed 5%, 25%, and 100% stages. A failed or out-of-bounds build never replaces the active model; online serving falls through to materialized trending and then recent available products.

## Common startup failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `JWT_SECRET must be a generated random value` | Placeholder or weak secret | Generate a stable random value of at least 32 characters in the secret manager and recreate the backend. |
| `RECOMMENDATION_USER_REF_SECRET must be a generated random value` | Missing, reused placeholder, or weak recommendation pseudonym key | Generate an independent stable random value of at least 32 characters; do not reuse `JWT_SECRET`. |
| Missing `MONGODB_URI` or connection timeout | Missing value, wrong Compose hostname, network policy, or revoked user | Use `mongo` as the host inside local Compose and `localhost` only for host-native development; then verify provider access. |
| Readiness returns `503` | MongoDB ping failed | Inspect database availability and network access; liveness may remain healthy. |
| Browser CORS error | Public frontend origin is absent or mismatched | Add the exact scheme, host, and port to `CORS_ORIGINS`; never use `*` with credentials. |
| Cookie works locally but not in production | HTTPS/public URL/cookie configuration mismatch | Verify HTTPS, `COOKIE_SECURE=true`, proxy headers, callback URL, and same-site topology. |
| LINE endpoint returns `503` | LINE integration disabled | Configure every required LINE value before enabling it. |
| LINE callback returns invalid or expired state | State expired, was already used, or callback was replayed | Restart login; do not relax single-use validation. |
| LLM assistant is unavailable | LLM disabled, missing provider key, provider error, or timeout | Verify feature configuration and provider health without logging prompts or keys. |
| Duplicate order retry returns `200` | Existing idempotent result | Treat it as success and use the returned original order. |
| Status update returns `409` | Invalid or concurrent transition | Reload the order and apply only a currently allowed next state. |
| `/metrics` returns `404` | Metrics disabled | Enable metrics only when the endpoint is protected at the deployment boundary. |

## Incident handling

- Never paste secrets, cookies, authorization headers, full webhook payloads, prompts, or customer notes into logs or tickets.
- Use request IDs and order IDs for correlation.
- Rotate a suspected credential in its external provider first; deleting it from source is not revocation.
- Preserve audit evidence before coordinated history cleanup.
- Follow the [Credential Incident Runbook](security-incident-response.md) for the known historical MongoDB exposure.

## Current operational limitations

- The post-LiteLLM backend and frontend images passed an isolated local Docker Compose rebuild and smoke test on 2026-08-22.
- GitHub Actions backend, frontend, and container jobs passed for Phase 3 commit `e5bd195` on 2026-08-23.
- LINE OAuth and webhook behavior still require a real sandbox end-to-end test.
- Metrics are process-local, and agent memory/cache is not shared across replicas.
- SSE fan-out and recommendation caches are process-local. Recommendation replicas converge through the shared active-model pointer in MongoDB.
