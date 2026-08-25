# Background Jobs and Transactional Outbox

This document describes how committed business state becomes background work in
the backend. The design uses MongoDB for business state and the outbox, Taskiq on
Redis Streams for commands, and separate API, worker, and dispatcher processes.

## Vocabulary

Keep facts and commands separate:

| Concept | Meaning | Examples | Storage |
| --- | --- | --- | --- |
| Outbox event | A fact that already happened and committed | `order.created`, `order.status_changed` | MongoDB `outbox_events` |
| Task / job | A command that should be executed | `order.process`, `line.push`, `agent.process` | Redis Streams via Taskiq |

Facts live in `app/domain/outbox.py`. Stable task names live in
`app/domain/jobs.py`. The dispatcher is the boundary that maps facts to commands.

## Runtime components

```text
FastAPI
   │
   ├── direct async command ───────────────▶ Redis Streams ──▶ Taskiq worker
   │
   └── MongoDB transaction
          ├── business state
          └── outbox fact
                 │
                 ▼
          Outbox dispatcher
                 │
                 └─────────────────────────▶ Redis Streams ──▶ Taskiq worker
```

The processes run independently:

| Process | Command | Responsibility |
| --- | --- | --- |
| API | `uvicorn app.main:app` | HTTP, validation, synchronous application flows, enqueue commands |
| Worker | `taskiq worker app.core.taskiq:broker` | Consume Redis Streams and execute thin task handlers |
| Dispatcher | `python -m app.jobs.dispatcher` | Poll MongoDB outbox and enqueue mapped commands |

The dispatcher is not started from FastAPI, so multiple Uvicorn workers do not
accidentally create multiple polling loops.

## Composition root

Dependency construction belongs in `app/bootstrap.py`.

`ApiServices` and `WorkerServices` are data-only records. They do not contain
business methods, lifecycle methods, or service-locator behavior. The
composition-root functions build the dependency graph needed by each runtime:

```text
build_api_services()
  └── only API dependencies

build_worker_services()
  └── only Taskiq worker dependencies

dispatcher
  └── MongoDatabase + OutboxService only
```

This keeps runtime wiring in one explicit place without a giant mutable
`ServiceContainer`. Startup cleanup is exception-safe: resources already opened
during a partial startup are closed before the original error is re-raised.

FastAPI exposes the concrete dependencies routes already use on `app.state`; it
does not also expose a second container path. Taskiq stores one `WorkerServices`
instance on worker state and task handlers retrieve it through
`services_from(context)`.

## Layering

```text
API route ──▶ Service ──▶ Repository / external adapter
Task handler ──▶ Service ──▶ Repository / external adapter
Service transaction ──▶ business state + outbox
Outbox dispatcher ──▶ Taskiq command
```

Rules:

- Task handlers stay thin.
- Services own application/business decisions.
- Repositories do not import Taskiq.
- Outbox events are facts, never commands.
- The dispatcher contains routing and delivery infrastructure, not business logic.
- Services that need to enqueue LINE work receive an injected enqueue callable.

## Supported facts and jobs

| Outbox event | Routed task | Effect |
| --- | --- | --- |
| `order.created` | `order.process` | Post-commit order processing / LINE acknowledgement |
| `order.status_changed` | `order.update_status` | Post-commit status notification |

Current Taskiq commands:

- `order.process`
- `order.update_status`
- `order.cancel`
- `line.push`
- `line.reply`
- `agent.process`

`order.cancel` is a command, not an outbox fact. A successful cancellation
changes business state and emits `order.status_changed`.

## Transactional outbox

Business state and its fact are written with the same MongoDB session:

```python
async with self.db.transaction() as session:
    await self.db.orders.insert_one(order, session=session)
    await self.outbox.save_event(..., session=session)
```

Therefore a supported MongoDB deployment commits both writes or neither write.

MongoDB multi-document transactions require a replica set or sharded cluster.
The local compose stack runs a single-node replica set. A standalone `mongod`
uses the documented non-atomic fallback and must not be treated as a production
transactional outbox.

## Claiming, leases, and fencing

A due event is claimed with one atomic `find_one_and_update`:

```text
status ∈ {pending, failed, processing}
availableAt <= now

        │ atomic claim
        ▼

status      = processing
claimId     = random fencing token
claimedAt   = now
availableAt = now + visibility timeout
attempts   += 1
```

`availableAt` is the visibility deadline. If a dispatcher dies, another
dispatcher may reclaim the event after the deadline.

`claimId` is equally important. Every `sent`, `failed`, or `dead` transition is
conditional on:

```text
_id == event.id
status == processing
claimId == caller.claim_id
```

This prevents a stale dispatcher from waking after its lease expired and
overwriting the state written by a newer dispatcher. Successful transitions
remove `claimId` and `claimedAt`.

A dispatcher crash consumes an attempt because attempts increment at claim
time. If repeated crashes exhaust the attempt budget without reporting a normal
failure, the next reclaim parks the event as `dead` rather than dispatching it
forever.

## Status flow and retry

```text
pending
   │
   ▼
processing ───────────────▶ sent
   │
   ├── failure ───────────▶ failed ──(backoff)──▶ processing
   │
   └── exhausted ─────────▶ dead
```

Outbox retry backoff is staged:

| Attempt | Next retry |
| --- | --- |
| 1 | 5 seconds |
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 5 minutes |
| 5+ | 15 minutes |

Retries are represented by `availableAt`; the dispatcher does not sleep for a
long backoff while holding work.

Taskiq task failures are a separate layer and use
`SimpleRetryMiddleware(default_retry_count=3)`.

## Delivery guarantee

The system is **at-least-once**, not exactly-once.

A valid failure window is:

```text
enqueue to Redis succeeds
        │
dispatcher crashes before mark-as-sent
        │
visibility timeout expires
        │
event is dispatched again
```

That behavior is intentional. Consumers must tolerate duplicate delivery.

Outbox fact duplication is constrained by the unique `idempotencyKey` index.
Handlers re-read authoritative state where stale payloads would be dangerous.

## LINE webhook reliability

Inbound LINE delivery also follows at-least-once semantics.

The API intentionally does **not** write a permanent "processed" marker before
Taskiq enqueue. Doing so creates this loss window:

```text
mark webhook processed
        │
process crashes
        │
task was never enqueued
        │
LINE retry is rejected as duplicate  ← lost message
```

Instead the API verifies the signature and enqueues `agent.process`. If enqueue
fails, it returns HTTP 503 so LINE can retry. A retried webhook can enqueue the
same command more than once, but every command carries the stable key:

```text
line:<LINE webhook event id>
```

`agent.process` claims that key in `job_idempotency` before running. Duplicate
deliveries therefore return without re-running the LLM turn, while failed runs
release the claim so Taskiq can retry.

This chooses duplicate-safe delivery over silent loss.

## Correlation IDs

The existing request ID is reused as the correlation ID:

```text
HTTP request
  → direct task or outbox fact
  → dispatcher
  → Taskiq task
  → service
```

Structured logs may include `correlationId`, `eventId`, `eventType`, `taskId`,
`taskName`, `attempt`, and `orderId`. Secrets, LINE recipients, and message
bodies are not logged.

## Failure behavior

| Failure | Behavior |
| --- | --- |
| Redis unavailable during outbox dispatch | Event becomes retryable; never marked sent |
| API cannot enqueue LINE webhook task | API returns 503 so LINE can retry |
| Worker unavailable | Tasks remain in Redis Streams |
| Dispatcher crashes after claim | Lease expires and event becomes reclaimable |
| Stale dispatcher resumes | Fencing token prevents stale status overwrite |
| Repeated dispatcher crashes | Attempt budget eventually parks event as dead |
| Task handler raises | Taskiq retry middleware handles the task |
| Unknown outbox event | Event is parked as dead and remains inspectable |

## Operating

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
taskiq worker app.core.taskiq:broker
python -m app.jobs.dispatcher
```

Docker Compose runs these as separate `backend`, `worker`, and `dispatcher`
services. Worker and dispatcher do not expose public ports.

Useful outbox queries:

```javascript
db.outbox_events.find({status: "dead"}).sort({createdAt: -1})

db.outbox_events.find(
  {status: "failed"},
  {eventType: 1, attempts: 1, lastError: 1, availableAt: 1}
)
```

Sent events are removed by the existing TTL index seven days after
`publishedAt`.
