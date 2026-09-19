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
| Core | `APP_ENV`, `MONGODB_URI`, `JWT_SECRET` |
| Public URLs | `FRONTEND_URL`, `BACKEND_URL`, `CORS_ORIGINS`, `COOKIE_SECURE` |
| Redis, logging, metrics, SSE | `REDIS_URL`, `LOG_LEVEL`, `LOG_JSON`, `METRICS_ENABLED`, `METRICS_AUTH_TOKEN`, `SSE_*` |
| Monitoring stack | `PROMETHEUS_RETENTION`, `GRAFANA_BIND_ADDRESS`, `GRAFANA_ADMIN_USER`, `GRAFANA_ADMIN_PASSWORD`, `ALERTMANAGER_WEBHOOK_URL` |
| LINE | `LINE_ENABLED`, channel credentials, login credentials, `LINE_REDIRECT_URI` |
| LLM | `LLM_ENABLED`, `LLM_API_KEY`, base URL, model tiers, routing, limits, cache, cost inputs |
| Frontend build | `NEXT_PUBLIC_API_URL` |

Startup fails intentionally when required values are missing, either required secret is weak/reused as a placeholder, production cookies are insecure, or an enabled integration is incomplete. Do not work around this validation.

`NEXT_PUBLIC_API_URL` is embedded during the frontend build. Rebuild the frontend when this value changes.

## Image publication

Pull requests run backend, frontend, and local-container gates but never publish an image. A push to `main`, a `v*.*.*` tag, or an explicitly dispatched workflow publishes the backend to GHCR only after all gates pass. The frontend remains a Vercel build rooted at `apps/frontend`.

The image job publishes Linux AMD64 and ARM64 manifests with OCI metadata, provenance, and an SBOM. Every publication includes an immutable full-commit tag:

```text
ghcr.io/<owner>/<repository>-backend:sha-<40-character-git-sha>
```

The job summary also records the stronger digest reference:

```text
ghcr.io/<owner>/<repository>-backend@sha256:<digest>
```

Use the digest in production when practical. The mutable `main` tag is for inspection and must not be the recorded rollback boundary. SemVer tags are also emitted for Git tags matching `v*.*.*`.

If the GHCR package is private, configure Dokploy with a dedicated read-only package credential. Never use a developer password or a token with write/delete package scopes.

References: [GitHub container publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images) and [GHCR authentication](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Dokploy deployment

Production uses [`compose.prod.yaml`](../compose.prod.yaml). It contains Redis,
the backend, the Taskiq worker, and the outbox dispatcher. MongoDB is external
and the frontend is deployed by Vercel. The backend has no published host port;
Dokploy's Domains/Traefik integration routes the public API hostname to the
backend service on port `8000`.

Configure the Dokploy Compose service with:

1. Compose path: `compose.prod.yaml`.
2. The production branch used by the repository.
3. Keep the repository connection for Compose source synchronization, but disable direct Git push Auto Deploy for this service. The image digest is updated manually after GitHub Actions publishes it.
4. A Dokploy Domain targeting service `backend` and container port `8000`.
5. The complete runtime environment in Dokploy's Compose Environment. The
   `BACKEND_IMAGE` value must be an immutable GHCR digest, and the same
   environment is loaded by `backend`, `worker`, and `dispatcher` through
   `.env`.

After a successful image publication run, copy the full immutable image
reference from the `Backend container` job summary into Dokploy's
`BACKEND_IMAGE` Compose Environment and redeploy the Compose service. Do not
copy only the digest; the value must include the GHCR image name and the
`@sha256:` prefix.

If GHCR is private, configure a read-only GHCR registry credential in Dokploy.
Do not commit a populated `.env`, `deploy/backend.env`, or registry token.

Validate the Compose model locally with a test environment file without
printing secret values:

```bash
BACKEND_IMAGE='ghcr.io/example/hiwkaw-backend@sha256:<digest>' \
BACKEND_ENV_FILE='.env.ci' \
docker compose --file compose.prod.yaml config --quiet
```

The image publication job runs in parallel with the backend, frontend, and
container checks. Only deploy an image reference from a run whose required
checks have passed.

## Vercel frontend

Configure the Vercel project root as `apps/frontend` and set the production build variable:

```env
NEXT_PUBLIC_API_URL=https://api.example.com/api/v1
```

Assign a stable same-site custom hostname such as `app.example.com`; configure the backend with matching `FRONTEND_URL` and `CORS_ORIGINS`. A changed `NEXT_PUBLIC_API_URL` requires a new Vercel deployment because it is embedded during the Next.js build. Do not add arbitrary `*.vercel.app` preview origins to credentialed production CORS.

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

## Background job runtimes

The deployment runs three backend processes from the same image:

| Service | Command | Notes |
| --- | --- | --- |
| `backend` | `uvicorn app.main:app` (image default) | Only service exposed through the Dokploy Domain |
| `worker` | `taskiq worker app.core.taskiq:broker` | No published or exposed ports |
| `dispatcher` | `python -m app.jobs.dispatcher` | Run one replica; claims are atomic so extras are safe but useless |

All three share Dokploy's Compose Environment through `.env`; do not duplicate
secrets per service.

`MONGODB_URI` must point at a replica set (MongoDB Atlas already is one).
Without it the transactional outbox falls back to a non-atomic dual write and
logs `mongodb_transactions_unavailable` at startup. Treat that warning in
production as a configuration defect.

If the worker is stopped, LINE assistant replies and order notifications queue
in Redis and are delivered when it returns. If the dispatcher is stopped,
outbox events accumulate as `pending` and are dispatched on restart; nothing is
lost. Check for stuck work with:

```javascript
db.outbox_events.countDocuments({status: {$in: ["pending", "failed"]}})
db.outbox_events.find({status: "dead"}).sort({createdAt: -1}).limit(20)
```

Delivery is at-least-once, so duplicate execution is possible and is suppressed
by idempotency keys rather than prevented. See
[Background Jobs and Outbox](background-jobs.md).

## Logs and metrics

The backend emits structured JSON logs by default. Use `X-Request-ID` to correlate a client report with the request log. Order lifecycle logs also include an order ID.

Prometheus metrics are available at `/metrics` when enabled. Production
requires the deployment-managed `METRICS_AUTH_TOKEN` bearer token, and
Prometheus reads the same value from the private Docker secret file. Do not
share this token or put it in dashboards.

See the [Observability Runbook](observability.md) for the metric list and redaction guarantees.

## Private monitoring access

The production Compose stack keeps Prometheus, Grafana, and Alertmanager on
the private Docker network. Grafana is bound only to the Dokploy host's
Tailscale IPv4 address using `GRAFANA_BIND_ADDRESS`; Prometheus and
Alertmanager have no published ports. To inspect the dashboards, connect
through Tailscale/VPN and open `http://<tailscale-ip>:3000`. Use the
deployment-managed Grafana credentials; do not expose Grafana or `/metrics`
through the public API domain.

## Deployment sequence

1. Back up MongoDB and verify restore instructions.
2. Run the complete CI boundary against the exact revision.
3. Select the immutable backend digest produced by the successful CI run.
4. Apply backend, Compose, Dokploy Domain, and Vercel environment configuration.
5. Let Dokploy pull and start the backend, worker, and dispatcher without publishing the backend port.
6. Wait for readiness through the public Dokploy Domain and inspect startup logs.
7. Deploy the frontend from `apps/frontend` on Vercel with the correct public API build variable.
8. Connect through Tailscale/VPN and confirm Grafana loads the provisioned dashboards and Prometheus reports healthy API, worker, and dispatcher targets.
9. Smoke-test public products, login if enabled, an isolated test order, staff transition, logs, and metrics. Confirm the test order's outbox event reaches `sent` and the worker logged the matching task.
10. Verify Alertmanager can deliver a test alert through the configured generic webhook, then remove the test alert.
11. Shift traffic gradually where the platform supports it.
12. Record the deployed revision, image digests, configuration version, and verification result without recording secret values.

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
3. Set `BACKEND_IMAGE` to the previous known-good digest and restore its compatible configuration.
4. Re-run liveness, readiness, and core smoke checks.
5. Do not automatically roll back database documents written in the new schema; the dual-read design is intended to keep them readable.
6. If data restoration is required, stop writes and use the provider's reviewed restore procedure. Do not improvise destructive MongoDB commands.

Apply an image rollback by setting `BACKEND_IMAGE` to the previous known-good
digest in Dokploy's Compose Environment, then redeploying the Compose service.

## Common startup failures

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `JWT_SECRET must be a generated random value` | Placeholder or weak secret | Generate a stable random value of at least 32 characters in the secret manager and recreate the backend. |
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
- Metrics and SSE fan-out remain process-local. Agent memory, confirmations, rate limits, and authentication session state are Redis-backed.
- LINE assistant replies and order notifications now require a running Taskiq worker; the API only enqueues them.
- The transactional outbox requires a MongoDB replica set. A standalone deployment degrades to a documented non-atomic fallback and is not production-suitable.
