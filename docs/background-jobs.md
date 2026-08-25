# Background Jobs and Transactional Outbox

This document describes how committed business state becomes background work.
It covers the transactional outbox, the outbox dispatcher, the Taskiq worker,
and the reliability properties the design does and does not provide.

## Vocabulary

The two concepts are deliberately never mixed:

| Concept | Meaning | Examples | Storage |
| --- | --- | --- | --- |
| **Outbox event** | A fact. Something that already happened and is committed. | `order.created`, `order.status_changed` | MongoDB `outbox_events` |
| **Task / job** | A command. Something that should be done. | `order.process`, `line.push`, `agent.process` | Redis Streams (Taskiq) |

Facts are declared in `app/domain/outbox.py`. Commands are declared in
`app/domain/jobs.py`. The dispatcher is the only component that turns one into
the other.

## Runtime components

```text
┌──────────────┐   MongoDB transaction   ┌──────────────┐
│   FastAPI    │────────────────────────▶│ orders +     │
│ (API process)│                         │ outbox_events│
└──────┬───────┘                         └──────┬───────┘
       │ agent.process.kiq()                    │
       ▼                                        ▼
  Redis Streams  ◀───────────────────  Outbox Dispatcher
       │                                (own process)
       ▼
┌──────────────┐
│ Taskiq Worker│──▶ Order / LINE / Agent services ──▶ MongoDB, LINE API
└──────────────┘
```

Three processes run independently:

| Process | Command | Role |
| --- | --- | --- |
| API | `uvicorn app.main:app` | Serves HTTP. Enqueues tasks; never consumes them. |
| Worker | `taskiq worker app.core.taskiq:broker` | Consumes Redis Streams and runs task handlers. |
| Dispatcher | `python -m app.jobs.dispatcher` | Polls the outbox and enqueues the mapped task. |

The dispatcher is a dedicated process on purpose. Running it inside the API
would start one polling loop per Uvicorn worker, and reliable dispatch would
then depend on an individual web process staying alive.

### Broker configuration

`app/core/taskiq.py` builds the broker at import time from `REDIS_URL` in the
process environment. It is a module-level singleton because
`taskiq worker app.core.taskiq:broker` resolves the broker by import path, so
the worker, the dispatcher, and the API all reach the same queue. A per-request
`Settings` object cannot redirect it; change `REDIS_URL` instead.

Taskiq manages its own broker connections. The application's Redis client
(`app/db/redis.py`) stays separate because it serves auth sessions, agent
memory, and caching — not broker internals.

## Layering

```text
API route ──▶ Service ──▶ Repository / MongoDB
Task handler ──▶ Service ──▶ Repository / external adapter
Service transaction ──▶ business state + outbox ──▶ Dispatcher ──▶ Taskiq
```

- Task handlers in `app/jobs/` are thin: they bind the correlation id, resolve
  the worker's service container, and delegate.
- `OutboxRepository` knows nothing about Taskiq.
- `OrderWorkflowService` and `LineChatService` receive an *injected* enqueue
  callable, so no domain service imports the Redis broker.

## Supported facts and their routes

| Outbox event | Produced by | Routed to task | Effect |
| --- | --- | --- | --- |
| `order.created` | `OrderService.create` | `order.process` | LINE acknowledgement to the customer |
| `order.status_changed` | `OrderService.transition`, `OrderService.cancel_own` | `order.update_status` | LINE status notification |

The routing table is `EVENT_HANDLERS` in `app/jobs/dispatcher.py`. Adding a fact
means adding one registry entry, never a new branch in the dispatcher.

## Supported jobs

| Task name | Handler | Delegates to |
| --- | --- | --- |
| `order.process` | `app/jobs/order.py::process_order` | `OrderWorkflowService.process_created` |
| `order.update_status` | `app/jobs/order.py::update_order_status` | `OrderWorkflowService.process_status_change` |
| `order.cancel` | `app/jobs/order.py::cancel_order` | `OrderService.cancel_own` |
| `line.push` | `app/jobs/line.py::push_line` | `LineBotClient.push_messages` |
| `line.reply` | `app/jobs/line.py::reply_line` | `LineBotClient.reply_messages` |
| `agent.process` | `app/jobs/agent.py::process_agent_message` | `LineChatService.handle_text_message` |

`order.cancel` is the asynchronous cancellation *command*. It is not produced by
any outbox event, because cancellation is already performed synchronously by the
API and the agent tool; both then emit `order.status_changed`.

## MongoDB transaction requirement

The outbox event is written with the same session as the business write:

```python
async with self.db.transaction() as session:
    await self.db.orders.insert_one(doc, session=session)
    await self.outbox.save_event(..., session=session)
```

So the outcome is always `order + outbox` or `neither` — never a dual write.

**Multi-document transactions require a replica set or a sharded cluster.** A
standalone `mongod` cannot provide them. `MongoDatabase._detect_transaction_support`
probes `hello` at startup and sets `transactions_supported`.

- **Supported** (MongoDB Atlas, or the local single-node replica set in
  `docker-compose.yaml`): the writes are atomic.
- **Not supported** (a plain standalone `mongod`): `transaction()` yields
  `None`, both documents are still written, a `mongodb_transactions_unavailable`
  warning is logged at startup, and atomicity is lost. This fallback keeps the
  application running for local experiments; it is **not** a transactional
  outbox and must not be used in production.

The local compose file therefore runs `mongod --replSet rs0` and initiates the
set from the healthcheck. `MONGODB_URI` must include the replica set, for
example `mongodb://mongo:27017/?replicaSet=rs0`.

## Claiming and concurrency

`OutboxRepository.claim_one` claims with a single atomic `find_one_and_update`:

```text
{status ∈ {pending, processing, failed}, availableAt ≤ now}
  → {status: processing, availableAt: now + 60s}, attempts += 1
```

Because matching and mutating happen in one step, two dispatcher instances can
never claim the same event. `availableAt` doubles as a visibility deadline, so an
event left in `processing` by a crashed dispatcher becomes claimable again after
60 seconds. Terminal statuses (`sent`, `dead`) are never claimed.

## Status flow and retries

```text
pending ──▶ processing ──┬──▶ sent
                         └──▶ failed ──▶ (backoff elapses) ──▶ processing
                                     └──▶ dead   (attempts ≥ maxAttempts)
```

`attempts` is incremented at claim time, not at failure time, so an event whose
dispatcher crashed mid-flight still counts its attempt and cannot loop forever.

Backoff is staged, defined by `RETRY_BACKOFF_SECONDS` in `app/domain/outbox.py`:

| Attempt | Next retry after |
| --- | --- |
| 1 | 5 seconds |
| 2 | 30 seconds |
| 3 | 2 minutes |
| 4 | 5 minutes |
| 5+ | 15 minutes |

Retries are scheduled with `availableAt`; the dispatcher never sleeps for the
backoff. After `maxAttempts` (default 5) the event becomes `dead`, keeping its
payload and `lastError` for inspection.

Task-level failures are separate: the broker carries
`SimpleRetryMiddleware(default_retry_count=3)`, so a failing handler is retried
by Taskiq itself.

## Delivery guarantees

The system is **at-least-once, never exactly-once**. This sequence is possible:

```text
task enqueued to Redis  →  dispatcher crashes  →  event not marked sent
                        →  visibility timeout expires  →  event dispatched again
```

An event is only marked `sent` *after* the enqueue returns, so a broker outage
always leaves the event retryable rather than silently dropped. The cost is
duplicate delivery, which consumers must tolerate.

Duplicates are handled at three levels:

1. **Fact level** — `outbox_events.idempotencyKey` has a unique index, so the
   same fact cannot be recorded twice (`order.created:<orderId>`,
   `order.status_changed:<orderId>:<status>`).
2. **Task level** — `agent.process` claims `job_idempotency` before running the
   LLM turn and releases the claim if the run fails, so a genuine retry proceeds
   while a duplicate delivery returns successfully without re-running.
3. **Handler level** — `order.update_status` re-reads the order from MongoDB
   instead of trusting the payload, so a late or duplicate delivery can never
   announce a stale status.

Order creation itself remains idempotent through the pre-existing
`Idempotency-Key` header and the unique `orders.idempotencyKey` index.

## Correlation IDs

The project's existing request id is the correlation id; no competing concept was
introduced. `RequestIDMiddleware` sets it, `OrderService` copies it onto the
outbox event, the dispatcher restores it while dispatching, and each task binds
it again with `bind_correlation_id`. Structured logs therefore carry
`correlationId` (plus `eventId`, `eventType`, `taskId`, `taskName`, `attempt`,
`orderId`) along the whole path.

Logs never include access tokens, channel secrets, authorization headers, LINE
recipients, or message bodies.

## Failure behaviour

| Scenario | Behaviour |
| --- | --- |
| Task handler raises | Taskiq logs it and retries; the worker keeps running. |
| One outbox event fails | It is rescheduled or marked dead; the batch and the polling loop continue. |
| Redis unavailable | The enqueue fails, the event stays `failed` and retryable, nothing is marked `sent`. |
| MongoDB hiccup during polling | Logged as `outbox_poll_failed`; the loop continues and events stay claimable. |
| Worker unavailable | Messages accumulate in the Redis Stream and are consumed when it returns. |
| Dead event | Retains payload, `attempts`, and `lastError` for inspection. |

## Operating

```bash
# API
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Taskiq worker (consumes Redis Streams and runs handlers)
taskiq worker app.core.taskiq:broker

# Outbox dispatcher (polls MongoDB, enqueues tasks)
python -m app.jobs.dispatcher
```

Under Docker Compose, all three run as separate services (`backend`, `worker`,
`dispatcher`). The worker and dispatcher publish no ports.

Inspecting the outbox:

```javascript
// Events that need attention
db.outbox_events.find({status: "dead"}).sort({createdAt: -1})

// Events currently retrying
db.outbox_events.find({status: "failed"}, {eventType: 1, attempts: 1, lastError: 1})
```

`sent` events are removed by a TTL index seven days after `publishedAt`.

## Scaling notes

- The Taskiq worker scales horizontally; Redis Streams consumer groups
  distribute messages.
- The dispatcher is safe to run with several replicas because claims are atomic,
  but one replica is enough — it is not the bottleneck.
