# Documentation

This directory contains the operational and engineering documentation for the Food Ordering Platform. Start with the guide that matches your role.

| Document | Audience | Purpose |
| --- | --- | --- |
| [User and Role Guide](user-guide.md) | Customers, staff, admins, demo reviewers | Web and LINE flows, permissions, order lifecycle, and current UI limitations |
| [Developer Guide](developer-guide.md) | Backend and frontend contributors | Architecture, local setup, quality checks, conventions, and safe change workflow |
| [AI Security Model](ai-security.md) | Security reviewers and agent contributors | Prompt-injection threat model, deterministic controls, confirmations, and residual risk |
| [API Guide](api-guide.md) | API consumers and testers | Endpoint and role matrix, authentication, idempotency, examples, and error behavior |
| [Operations Runbook](operations-runbook.md) | Deployers and operators | Configuration, deployment, health checks, migration, rollback, and troubleshooting |
| [LLM Gateway](llm-gateway.md) | AI/backend engineers | LiteLLM complexity routing, DeepSeek configuration, fallback, and cache safety |
| [Background Jobs and Outbox](background-jobs.md) | Backend engineers and operators | Taskiq worker, transactional outbox, dispatcher, retries, idempotency, and delivery guarantees |
| [Observability Runbook](observability.md) | Operators and backend engineers | Structured logs, Prometheus metrics, redaction, and operational checks |
| [CPU Recommendation Plan](recommendation-system-plan.md) | Backend and recommendation engineers | Hardened event attribution, materialized trending, item-item ranking, evaluation, and rollout |
| [Credential Incident Runbook](security-incident-response.md) | Repository owner and security responder | Required external credential rotation and optional coordinated history cleanup |

## Quick paths

- First local run: [Developer Guide — Docker setup](developer-guide.md#docker-setup)
- Using the application: [User and Role Guide](user-guide.md)
- Calling the API: [API Guide](api-guide.md)
- Preparing a deployment: [Operations Runbook](operations-runbook.md)
- Configuring DeepSeek: [LLM Gateway](llm-gateway.md)
- Running the worker and outbox dispatcher: [Background Jobs and Outbox](background-jobs.md)
- Responding to the historical MongoDB credential incident: [Credential Incident Runbook](security-incident-response.md)

## Implementation status

Phases 0 through 3 are implemented, including the SSE staff queue, LINE status notifications, slate-validated recommendation analytics, and bounded CPU-only trending/item-item artifacts with controlled rollout and rollback. Backend lint/type/tests, frontend type-check/build, and container smoke tests passed locally and in GitHub Actions for Phase 3 commit `e5bd195`. Real LINE sandbox end-to-end verification remains an external action.

Documentation must describe only behavior present in the repository. Update the relevant guide in the same change whenever an endpoint, role permission, environment variable, operational procedure, or user flow changes.
