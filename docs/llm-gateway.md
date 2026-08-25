# LiteLLM Gateway

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

## MiniMax OpenAI-compatible alternative

MiniMax exposes an OpenAI-compatible chat-completions endpoint and LiteLLM accepts it through the native `minimax/` provider prefix. A single-tier deployment can use:

```env
LLM_ENABLED=true
LLM_API_KEY=replace-with-minimax-api-key
LLM_API_BASE=https://api.minimax.io/v1
LLM_MODEL=ordering-assistant
LLM_PRIMARY_MODEL=minimax/MiniMax-M2.7
LLM_COMPLEX_MODEL=minimax/MiniMax-M2.7
LLM_COMPLEXITY_ROUTING_ENABLED=false
```

Do not infer that the high-speed model is cheaper from its name. Review current provider pricing and measured quality before assigning different MiniMax models to economical and capable tiers. The customer agent removes provider `<think>` blocks before returning or retaining final text, and it never logs reasoning. MiniMax tool calling still requires a provider sandbox end-to-end test before production enablement because the provider requires complete assistant messages during multi-turn function-call exchanges.

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

The optional cache is exact-match, bounded by TTL, and Redis-backed through `REDIS_URL`. It is shared across replicas; customer-agent requests still always use `no-store`.

Customer-agent calls always send `no-store`, even when the gateway cache is enabled. Agent responses can contain tool calls or order-specific context; caching them could replay side effects, return stale order state, or retain personal data. Cache use must be explicitly enabled only for public, read-only, non-personalized completions. Do not enable semantic caching or add a vector database without a demonstrated use case and a separate privacy review.

## Security boundary

LiteLLM chooses a model deployment, not an application identity or permission. The gateway cannot override the authenticated user, role, tool allowlist, product catalog, authoritative price, idempotency key, or order transition rules. Those remain deterministic backend responsibilities.

Prompt-injection resistance follows the same boundary. Direct user instructions and indirect catalog/tool-output text are untrusted. Customer mutation tool calls create a short-lived pending action instead of executing. Only a subsequent exact confirmation parsed by application code executes the stored validated arguments; the model cannot mark its own call as confirmed. Tool DTOs omit internal identities, status-history actors, and notes. See [AI Security Model](ai-security.md).

Verbose LiteLLM payload logging is disabled. Do not enable debug prompt logging in production.

References:

- [LiteLLM Router documentation](https://docs.litellm.ai/docs/routing)
- [LiteLLM automatic complexity routing](https://docs.litellm.ai/docs/proxy/auto_routing)
- [LiteLLM caching documentation](https://docs.litellm.ai/docs/proxy/caching)
- [LiteLLM MiniMax provider](https://docs.litellm.ai/docs/providers/minimax)
- [DeepSeek API documentation](https://api-docs.deepseek.com/)
- [MiniMax OpenAI-compatible API](https://platform.minimax.io/docs/api-reference/text-openai-api)
