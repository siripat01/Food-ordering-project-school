# Documentation

This directory contains the operational and engineering documentation for the Food Ordering Platform. Start with the guide that matches your role.

| Document | Audience | Purpose |
| --- | --- | --- |
| [User and Role Guide](user-guide.md) | Customers, staff, admins, demo reviewers | Web and LINE flows, permissions, order lifecycle, and current UI limitations |
| [Developer Guide](developer-guide.md) | Backend and frontend contributors | Architecture, local setup, quality checks, conventions, and safe change workflow |
| [API Guide](api-guide.md) | API consumers and testers | Endpoint and role matrix, authentication, idempotency, examples, and error behavior |
| [Operations Runbook](operations-runbook.md) | Deployers and operators | Configuration, deployment, health checks, migration, rollback, and troubleshooting |
| [LLM Gateway](llm-gateway.md) | AI/backend engineers | LiteLLM complexity routing, DeepSeek configuration, fallback, and cache safety |
| [Observability Runbook](observability.md) | Operators and backend engineers | Structured logs, Prometheus metrics, redaction, and operational checks |
| [Credential Incident Runbook](security-incident-response.md) | Repository owner and security responder | Required external credential rotation and optional coordinated history cleanup |

## Quick paths

- First local run: [Developer Guide — Docker setup](developer-guide.md#docker-setup)
- Using the application: [User and Role Guide](user-guide.md)
- Calling the API: [API Guide](api-guide.md)
- Preparing a deployment: [Operations Runbook](operations-runbook.md)
- Configuring DeepSeek: [LLM Gateway](llm-gateway.md)
- Responding to the historical MongoDB credential incident: [Credential Incident Runbook](security-incident-response.md)

## Implementation status

Phases 0 and 1 are implemented in the current working tree. Phase 2 is implemented and locally verified: all 26 backend tests pass against MongoDB on Python 3.12, frontend type-check/build passes, and the isolated Docker Compose stack passes its liveness, readiness, frontend, metrics, and non-root runtime checks. The first GitHub Actions run still requires a published revision and CI access. Phase 3 features such as SSE queue updates and LINE order-status notifications are not implemented.

Documentation must describe only behavior present in the repository. Update the relevant guide in the same change whenever an endpoint, role permission, environment variable, operational procedure, or user flow changes.
