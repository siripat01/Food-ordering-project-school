# CPU-First Recommendation System

## Status and decision

The recommendation workload remains inside the FastAPI modular monolith. Model builds are explicit backend commands and serving uses MongoDB plus bounded in-process caches. The implementation does not require a GPU, vector database, Redis, a separate recommendation service, or heavy ML dependencies.

| Delivery slice | Working-tree status | Production state |
| --- | --- | --- |
| R0: event integrity and privacy | Implemented | Required configuration is validated at startup |
| R1: materialized trending | Implemented | Requires a written and activated model version |
| R2: item-item personalization | Implemented | Disabled by default with `RECOMMENDATION_ITEM_ITEM_ROLLOUT_PERCENT=0` |
| R3: controlled rollout and rollback | Implemented | Activation and rollout remain operator-controlled |

Implementation availability is not the same as model approval. A deployment must build a model from its own data, inspect offline results, pass the activation gate, and deliberately choose a rollout percentage before personalized results reach customers.

## Architecture

```text
Authenticated recommendation request
  -> RecommendationService
      -> optional external-first provider
      -> active immutable local model
          -> bounded completed-order profile
          -> deterministic rollout bucket
          -> item-item or trending scorer
          -> available-product filter
      -> optional external-fallback provider
      -> recent available-product fallback
  -> persist a short-lived served slate

CPU model builder
  -> fixed temporal cutoff and bounded MongoDB cursors
  -> completed orders plus capped engagement events
  -> trending and item-item artifacts
  -> global temporal evaluation and activation gate
  -> immutable model version and atomic active pointer
```

The external provider remains feature-flagged. `RECOMMENDER_MODE` selects `local`, `external_first`, or `external_fallback`; the local and recent-catalog paths remain available without the external service.

## R0: event integrity and privacy

`GET /api/v1/recommendations` requires an authenticated customer and persists the exact served slate. The response contains a server-generated `recommendation_id`, the selected strategy, and available products.

The browser may submit only:

```text
event_type, product_id, recommendation_id
```

Allowed browser event types are `impression`, `click`, and `add_to_cart`. The last value is the current API name for an accepted-order intent; it is not treated as an authoritative purchase. The backend records purchase signals only when an order reaches `completed`.

For every browser event, the backend:

- resolves identity from the authenticated request;
- requires the slate to belong to that pseudonymous user and remain unexpired;
- requires the product to appear in the slate and remain available;
- derives rank, placement, strategy, model version, timestamp, and dedupe key;
- deduplicates by user, slate, product, and event type;
- applies an atomic per-user/product/type/day cap; and
- stores no direct user ID, LINE ID, name, email, price, note, or token.

`RECOMMENDATION_USER_REF_SECRET` is a dedicated secret independent of `JWT_SECRET`. `RECOMMENDATION_USER_REF_KEY_VERSION` records which key version produced a pseudonym. Rotating JWT signing material therefore does not silently change recommendation references.

The frontend uses `IntersectionObserver`: a card must remain at least 50% visible for 750 milliseconds in a visible browser tab before it records one impression for that slate/product pair. Telemetry is best-effort and cannot block browsing, navigation, or order creation.

Completed-order purchase events aggregate duplicate product lines, preserve quantity, and use an order/product dedupe key. Cancelled or otherwise incomplete orders do not create purchase events. Recent order-intent attribution is copied only when it matches the same pseudonymous user and product inside the configured slate-retention window.

## Data model and retention

### `recommendation_slates`

```text
id, schemaVersion, userRef, userRefKeyVersion, placement, strategy,
modelVersion, items[{productId, rank}], createdAt, expiresAt
```

Slates expire after `RECOMMENDATION_SLATE_RETENTION_DAYS`, seven days by default.

### `recommendation_events`

```text
schemaVersion, dedupeKey, userRef, userRefKeyVersion, eventType,
productId, recommendationId, rank, placement, strategy, modelVersion,
source, quantity, orderId, createdAt
```

Events expire after `RECOMMENDATION_EVENT_RETENTION_DAYS`, 180 days by default. Daily counters expire separately after their enforcement window. Startup index management uses `collMod` when an existing TTL duration changes, avoiding an index-options conflict.

### Model collections

- `recommendation_model_versions` stores build configuration, cutoff, status, statistics, and offline metrics.
- `recommendation_artifacts` stores one immutable trending/neighbor artifact per product and model version.
- `recommendation_model_state` is the singleton active-model pointer.
- `recommendation_model_locks` contains the expiring single-builder lease.

Artifacts contain product-level aggregate values only; user histories exist only as bounded build/runtime inputs and are not written into model artifacts.

## R1: materialized trending

The builder uses server-owned weights and exponential time decay. Default relative positive weights are purchase `6.0`, accepted-order intent `1.0`, and click `0.25`. Impressions have zero positive weight and form a decayed exposure denominator for engagement; purchase score remains authoritative and is not divided by impressions. The configuration is persisted with each model version for reproducibility and cannot be supplied by a browser.

The scorer sorts deterministic ties by product ID. Unavailable or discontinued products are filtered through the product service at request time. If an active artifact is missing, invalid, oversized, or produces no available products, serving continues through the configured external fallback or recent available catalog.

## R2: item-item personalization

The CPU builder creates co-occurrence pairs primarily from completed multi-item order baskets and scores neighbors with cosine-style normalization:

```text
cooccurrence(i, j) / sqrt(count(i) * count(j))
```

Defaults require support from three baskets and retain at most 50 neighbors per product. Same-user lifetime pairing is disabled unless an operator explicitly supplies a non-zero build weight. Runtime profiles use only completed orders, at most 20 unique products, and score a bounded candidate pool before blending with trending. Users without a usable profile receive trending results.

## R3: build, activation, rollout, and rollback

Run commands from `apps/backend` with valid backend configuration. The default command is a read-only dry run:

```bash
.venv/bin/python -m scripts.build_recommendation_model
```

Write an immutable ready version without serving it (shadow build):

```bash
.venv/bin/python -m scripts.build_recommendation_model --write
```

Request activation after evaluation:

```bash
.venv/bin/python -m scripts.build_recommendation_model --write --activate
```

Activation is refused when the candidate has too few evaluation users, insufficient catalog coverage, no artifacts, exceeds the exact BSON byte limit, or regresses beyond the configured NDCG@10 allowance against either materialized trending or the current active model. Eligibility is persisted with the immutable version; an ineligible shadow build cannot later bypass the gate through rollback. A failed gate leaves the active pointer unchanged. The command emits aggregate JSON only and does not export user identities.

Roll back atomically to an existing ready version:

```bash
.venv/bin/python -m scripts.build_recommendation_model \
  --write \
  --rollback-version MODEL_VERSION
```

`RECOMMENDATION_ITEM_ITEM_ROLLOUT_PERCENT` controls deterministic pseudonymous buckets from 0 to 100. Recommended rollout checkpoints are 5, 25, and 100 after operational review. Changing the percentage does not rebuild or delete data, and setting it to zero returns profiled users to trending after the applicable result-cache lifetime.

## Offline evaluation and quality gates

The build command uses one global UTC cutoff. Interactions at or before the cutoff train the evaluation model; later completed purchases form the holdout. It evaluates recent-catalog, materialized trending, and item-item rankings on the same split and reports Recall@5/10, NDCG@5/10, catalog coverage, popularity share, evaluated users, warm/cold cohorts, baseline deltas, artifact size, source counts, and build duration.

## Bounds and caches

Default safety bounds include:

- 20 products per API response;
- 20 products per runtime profile;
- 50 neighbors per product;
- 200 scoring candidates;
- 250,000 source interactions;
- 20 products per basket;
- 500,000 co-occurrence pairs;
- 10,000 catalog artifacts and a 10 MiB loaded-model limit.

An out-of-bounds build raises an error and cannot replace the active pointer. The runtime polls the active pointer every 30 seconds by default, validates immutable artifacts before loading, and keeps a bounded LRU-style result cache with a 30-second default TTL. Event writes are never cached.

The original target of a build under five minutes on two CPU cores and under 512 MiB is a deployment benchmark, not a guaranteed measurement. Measure it with representative production-sized data before scheduling the job.

## Indexes and cleanup

Startup creates or migrates:

- a unique partial event `dedupeKey` index;
- event user/type/time, product/type/time, and slate/user/product indexes;
- TTL indexes for events, slates, counters, and model leases;
- a unique artifact model/product index;
- a model status/build-time index; and
- an order status/completion-time index used by the builder.

Successful activation records `previousModelVersion`, protects both active and previous versions, and removes only stale failed/building/ready versions outside the configured count and age window. Rollback validates the complete retained artifact before changing the pointer and never deletes recommendation events.

`DELETE /api/v1/recommendations/data` lets an authenticated customer idempotently remove their own slates, events, counters, and result-cache entries. `RECOMMENDATION_USER_REF_PREVIOUS_SECRETS` retains versioned old keys during rotation so the purge covers still-live pseudonyms. Aggregate product artifacts contain no user-level data and remain intact. User and product records themselves continue to use operational deactivation rather than physical deletion.

## Verification coverage

Backend tests cover dedicated-secret validation, stable pseudonyms across JWT rotation, TTL migration, cross-user/expired/arbitrary slate rejection, server-derived attribution, atomic caps, completed-order quantity and idempotency, cancelled-order exclusion, time decay, deterministic ranking, item-item similarity, history opt-in, hard size bounds, temporal leakage prevention, malformed artifact rejection, and failed activation gates.

Frontend type-check and production build verify the current integration. There is no frontend unit-test runner in this repository, so viewport behavior should also be exercised in a browser before production rollout.

## Rollout gate

Keep personalized rollout at zero until a model built from deployment data passes the automated gate and an operator reviews cohort metrics, baseline deltas, popularity share, artifact bounds, and latency. Start with a small deterministic bucket and retain the previous ready model for immediate rollback.
