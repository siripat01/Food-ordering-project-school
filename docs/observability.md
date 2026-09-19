# Observability Runbook

## Structured logging

The API emits JSON logs by default. Each request log includes a UTC timestamp, severity, logger, event name, request ID, method, route template, status, and duration. Order lifecycle events include the order ID and resulting status. LLM events include only the logical model group, latency, and aggregate token counts.

The logger does not record request or response bodies, query strings, authorization headers, cookies, user IDs, chat messages, prompts, tool arguments, product notes, or raw upstream exceptions. A final formatter-level redaction pass removes MongoDB URIs, Bearer/JWT values, common secret assignments, and email addresses.

Configuration:

```env
LOG_LEVEL=INFO
LOG_JSON=true
```

Do not add arbitrary structured fields without reviewing them for secrets, PII, and label cardinality.

## Prometheus metrics

Metrics are exposed at `GET /metrics` when `METRICS_ENABLED=true`. In
production the endpoint also requires `Authorization: Bearer <metrics token>`
using `METRICS_AUTH_TOKEN`.

Current application metrics:

- `food_ordering_http_requests_total`
- `food_ordering_http_request_duration_seconds`
- `food_ordering_orders_created_total`
- `food_ordering_order_value_total`
- `food_ordering_order_status_changes_total`
- `food_ordering_llm_request_duration_seconds`
- `food_ordering_llm_errors_total`
- `food_ordering_llm_tokens_total`
- `food_ordering_llm_estimated_cost_usd_total`
- `food_ordering_task_executions_total`
- `food_ordering_task_duration_seconds`
- `food_ordering_outbox_events_total`
- `food_ordering_outbox_oldest_pending_age_seconds`
- `food_ordering_dependency_up`

The API exposes its metrics on port 8000. The Taskiq worker exposes the same
redacted registry on private port 9101, and the outbox dispatcher exposes its
registry on private port 9102. These ports are not published to the host.

## Production monitoring stack

Production Compose includes Prometheus, Grafana, and Alertmanager on the
private `private` network. Prometheus scrapes the API, worker, and dispatcher;
Grafana is provisioned with the Prometheus datasource and four dashboards:

- Food Ordering — Overview
- Food Ordering — API
- Food Ordering — Orders
- Food Ordering — Jobs and LLM

Alertmanager sends grouped firing and resolved alerts to the deployment-only
`ALERTMANAGER_WEBHOOK_URL`. The initial rules cover service scrape failures,
API 5xx rate, API p95 latency, stale or dead outbox events, task failures, and
LLM error rate.

Grafana binds only to `GRAFANA_BIND_ADDRESS`, which should be the Dokploy
host's Tailscale IPv4 address. Operators connect through Tailscale/VPN, then
open `http://<tailscale-ip>:3000` and sign in with `GRAFANA_ADMIN_USER` and
`GRAFANA_ADMIN_PASSWORD`. Keep the Grafana password and webhook URL in the
deployment secret manager.

Routes use route templates instead of raw paths to prevent unbounded labels. No user, request, order, prompt, or credential value is used as a metric label. Order IDs remain log correlation fields, not metric labels.

Estimated LLM cost depends on `LLM_INPUT_COST_PER_MILLION` and `LLM_OUTPUT_COST_PER_MILLION`. Both default to zero because pricing varies by provider and model. Set them from the provider's current pricing during deployment review.

## Operational checks

```bash
curl --fail http://localhost:8000/api/v1/health/live
curl --fail http://localhost:8000/api/v1/health/ready
curl --fail http://localhost:8000/metrics
# Production example:
curl --fail -H "Authorization: Bearer $METRICS_AUTH_TOKEN" https://api.example.com/metrics
```

Liveness indicates that the process can serve HTTP. Readiness also pings MongoDB. A failed readiness check should remove the instance from traffic without restarting it solely because an external dependency is temporarily unavailable.
