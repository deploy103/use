# Use frontend

React 19 + TypeScript + Vite + Tailwind, with actual codex-lb/shadcn components
and registry Dither Kit sources. Attribution: `../licenses/frontend-SOURCES.md`.

```sh
npm ci
npm run typecheck
npm run build
npm test
```

Node 22.12+ is required by Vite (Node 24 used for verification). The production
build writes **../web-dist**, with `/assets/` URLs. It does not write static/.
The owner backend must serve the built index for `/`, `/dashboard`, `/accounts`,
`/usage`, `/settings`, and mount `/assets`. No frontend server/deployment is
required in production. `npm run dev` is intentionally not pointed at a live
backend; browser tests intercept every API request with disposable synthetic data.

## Contracts

Owner: GET `/api/session`, POST `/api/login`, `/api/setup`, `/api/logout`;
POST `/api/settings/password` with current_password/new_password. Cookies remain
server-managed; theme is the only localStorage preference. A first-use
`#setup=...` fragment is consumed into memory and immediately removed.

Dashboard/CSV/account requests retain the Python app's range/account/provider
query and account schemas. POST `/api/refresh` uses optional account_id. Bulk
controls make sequential individual requests and report failures without
pretending an atomic bulk operation succeeded.

Gemini and ChatGPT: POST `/api/oauth/start` with provider/name; GET/DELETE
`/api/oauth/{id}`; POST `.../complete` with callback_input. Snapshot provider is
`gemini` or `chatgpt`; callback_mode, status and expiry come from the backend.
The only clickable auth origins are exactly `https://accounts.google.com` for
Gemini and `https://auth.openai.com` for ChatGPT. No external page is opened
automatically. Closing/logout/expiry clears local state and best-effort cancels
the server flow; late responses do not revive closed or expired dialogs.

Claude is **key-based**, not a consumer OAuth login: default Claude LB mode
(`claude-lb`, trusted HTTPS URL and query key), or official Anthropic Admin
Usage/Cost (`claude`, fixed `https://api.anthropic.com`, Admin key, optional days).
No Claude.ai Pro/Max login or password collection is offered.

Custom API: POST `/api/connections/test`, then POST/PATCH `/api/accounts`.
Default extra is usage_path `/v1/usage`, format `auto`, auth_type `auto`.
Advanced formats: claude-lb/gateway/usage-json; headers: bearer/x-api-key.
URL base prefixes/HTTPS ports are preserved for backend validation.
OAuth completion and connection tests have a bounded 90-second request budget;
ordinary GETs and other requests have a 15-second budget.
No browser-to-provider requests and no inference calls are made. An API that
cannot report usage is explicitly unsupported, never a zero-usage success.

Quota/lifetime data is independent of date filters. No daily chart/model table
is rendered without dated records; null metrics never become fabricated zeros.
Charts and sparklines use only reported dates and measurements; their numeric
table is also available without pointer interaction.

## CSP

This copied Dither Kit version renders in a main-thread Canvas2D context; it
does not create Worker/Blob URLs. **No `worker-src blob:` is needed.** React/Radix
and chart positions use inline styles, so preserve a compatible style policy
(the existing backend allows inline styles). Production scripts/assets are
self-hosted. No external fonts, telemetry, CDN scripts or remote images used.
