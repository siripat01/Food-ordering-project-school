# In-Process LiteLLM Gateway

## Design

The customer assistant uses LiteLLM's Python `Router` and built-in complexity router inside the FastAPI modular monolith. It is not a separately deployed proxy or microservice. The gateway provides a stable logical model group, cost-aware model tiers, independent fallback model groups, bounded retries, provider timeouts, usage reporting, and optional local response caching.

The default provider configuration targets DeepSeek's OpenAI-compatible API:

```env
LLM_ENABLED=true
LLM_API_KEY=replace-with-llm-provider-api-key
LLM_API_BASE=https://api.deepseek.com
LLM_MODEL=ordering-assistant
LLM_PRIMARY_MODEL=deepseek/deepseek-v4-flash
LLM_COMPLEX_MODEL=deepseek/deepseek-v4-pro
LLM_COMPLEXITY_ROUTING_ENABLED=true
LLM_COMPLEXITY_CLASSIFIER=heuristic
LLM_FALLBACK_MODELS=[]
LLM_ROUTING_STRATEGY=simple-shuffle
```

Store `LLM_API_KEY` only in a local untracked `.env` or the deployment platform's secret manager. `OPENAI_API_KEY` remains accepted as a temporary backward-compatible input name, but new deployments should use `LLM_API_KEY`.

The logical `LLM_MODEL` is passed to LiteLLM's `auto_router/complexity_router`. The router maps `SIMPLE` and `MEDIUM` work to `LLM_PRIMARY_MODEL`, and maps `COMPLEX` and `REASONING` work to `LLM_COMPLEX_MODEL`. The default heuristic classifier makes no external API call. Because LiteLLM's lexical word-boundary matcher does not reliably segment unspaced Thai text, the application first applies exact substring matches from `LLM_COMPLEXITY_KEYWORDS`; a match routes directly to the capable group, while every other request goes through LiteLLM's classifier. User-controlled escalation phrases are disabled, although any content-based classifier can still be influenced by crafted input; application budgets and rate limits remain necessary cost controls.

Set `LLM_COMPLEXITY_CLASSIFIER=llm` to use the economical model for classification. The classifier has a short timeout, receives only the current request rather than conversation history, and falls back to the local heuristic on failure. This mode adds one model call, latency, and classification cost, so it is not the default.

`LLM_FALLBACK_MODELS` has a different purpose from `LLM_COMPLEX_MODEL`: fallbacks handle provider or deployment failures after a tier has been selected. Configure genuinely independent fallbacks where possible. Supported deployment-selection strategies are `simple-shuffle`, `least-busy`, and `latency-based-routing`.

LiteLLM runs in production mode so importing it cannot load a nearby `.env`. It is also forced to use its packaged local model cost map so application startup does not fetch mutable pricing metadata. Application cost metrics use explicit deployment settings instead.

## Cache safety

Local caching is disabled by default:

```env
LLM_CACHE_ENABLED=false
LLM_CACHE_TTL_SECONDS=60
LLM_CACHE_MAX_ENTRIES=200
```

The cache is exact-match, bounded, in-process, and cleared on restart. It is not shared across replicas.

Customer-agent calls always send `no-store`, even when the gateway cache is enabled. Agent responses can contain tool calls or order-specific context; caching them could replay side effects, return stale order state, or retain personal data. Cache use must be explicitly enabled only for public, read-only, non-personalized completions. Do not enable semantic caching or add a vector database without a demonstrated use case and a separate privacy review.

## Security boundary

LiteLLM chooses a model deployment, not an application identity or permission. The gateway cannot override the authenticated user, role, tool allowlist, product catalog, authoritative price, idempotency key, or order transition rules. Those remain deterministic backend responsibilities.

Verbose LiteLLM payload logging is disabled. Do not enable debug prompt logging in production.

References:

- [LiteLLM Router documentation](https://docs.litellm.ai/docs/routing)
- [LiteLLM automatic complexity routing](https://docs.litellm.ai/docs/proxy/auto_routing)
- [LiteLLM caching documentation](https://docs.litellm.ai/docs/proxy/caching)
- [DeepSeek API documentation](https://api-docs.deepseek.com/)
