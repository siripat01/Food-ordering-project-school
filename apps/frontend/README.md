# Food Ordering Web

The Next.js customer and staff interface for the monorepo. It uses the backend's HttpOnly cookie session and never sends an authoritative user ID, order price, or initial status.

```bash
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_URL=http://localhost:8000/api/v1 pnpm dev
```

See the [root documentation](../../README.md) for architecture, configuration, security, testing, and deployment instructions.
