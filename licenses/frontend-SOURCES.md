# Frontend source attribution

The Use frontend reuses MIT source from **Soju06/codex-lb**, revision
`72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e`:
https://github.com/Soju06/codex-lb/tree/72630bbfeee9e3b0d9acfb95e2b1e706b8b0188e/frontend

Copied unchanged:
- `src/lib/utils.ts`
- `src/components/empty-state.tsx`
- `src/components/ui/button.tsx`, `button-variants.ts`, `input.tsx`,
  `badge.tsx`, `badge-variants.ts`, `table.tsx`,
  `use-floating-layer-dismiss-guard.ts`

Adapted:
- `src/components/ui/dialog.tsx`: Korean close labels without gateway i18n.
- Upstream `features/dashboard/components/stats-grid.tsx` →
  `src/components/stats-grid.tsx`: local data contract, mobile two-column grid,
  actual Dither Kit Sparkline instead of Recharts.
- Upstream `components/layout/app-header.tsx` → local same-named component:
  sticky header/action/nav treatment; app-specific left rail and mobile dialog;
  removed gateway roles, billing/credit badges, gateway APIs and branding.
- Theme tokens/card treatment and TypeScript configuration form the basis of
  the local styles/build configuration. No upstream application data copied.

The full codex-lb MIT notice is retained in `frontend-codex-lb-MIT.txt`.

**Dither Kit** core + area-chart 0.1.0 sources were copied unchanged from the
existing registry installation in `gemini-lb/frontend/src/components/dither-kit`
(24 source files plus its original NOTICE), with its `dither-kit.json` manifest.
The reference repository's chart integration commit is
`94c674e858d80b21ef26bb296217517dad080f25`.
Upstream: https://github.com/Boring-Software-Inc/dither-kit
Registry: https://tripwire.sh/r/area-chart.json and https://tripwire.sh/r/core.json
The original NOTICE is retained both with the components and in
`frontend-dither-kit-NOTICE.txt`, including its MIT permission text and existing
Vite browser environment adaptation. Its historical font paragraph describes
the source installation: **Use copies no fonts**, proprietary or otherwise,
and uses the system font stack. The registry manifest describes original
registry ownership/hashes; local `area.tsx` already contained the documented
Vite adaptation when copied.

Only used runtime/build/test dependencies are declared in frontend/package.json.
Its npm lockfile was resolved independently after removing the inherited
gateway dependency set. No reference repository node_modules is needed to
install or build Use.
