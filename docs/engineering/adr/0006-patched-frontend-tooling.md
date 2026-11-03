# Patch frontend tooling while retaining the desktop browser floor

Status: Accepted — 2026-10-19

## Context

The #207 security review found seven npm advisory findings in the existing
development/test graph, including Vite 5, Vitest 2 and source-map-js. These tools
run with the developer's filesystem access. They are not part of the packaged
Gmail collector, but leaving the development server vulnerable is undesirable.

## Decision

Use Vite **7.3.7**, React plugin **4.7.0**, Vitest **4.1.12**, matching Node-24
types, and the compatible source-map-js **1.2.2** patch in the lockfile. Keep the
existing Node-24 runtime selection. Vite 7.3 is a
[supported security/important-fix line](https://vite.dev/releases).
Vitest 4.1.12 includes the
[Windows traversal correction](https://github.com/vitest-dev/vitest/security/advisories/GHSA-xp26-3454-mjgp)
and [manual-mock injection correction](https://github.com/vitest-dev/vitest/security/advisories/GHSA-qxg6-89mg-rqv8).

Disable dependency installation scripts in `frontend/.npmrc`; native optional
packages provide the required binaries without postinstall execution. A clean
locked install, full GUI tests, production build and Tauri CLI load pass on the
review host. The final npm audit reports zero findings.

Explicitly retain Vite 5's original build target list (`es2020`, Edge 88,
Firefox 78, Chrome 87, Safari 14), verified against its installed constants.
The security upgrade must not silently raise the browser floor for the app's
macOS-12 WebView. Build success does not replace native macOS verification.

## Alternatives considered

Leaving older tools with documented exposure mitigations would retain known
advisories. Jumping directly to the latest Vite/Vitest major would add unnecessary
migration surface. Forced audit remediation or bypassing peer checks would obscure
compatibility failures. The selected supported versions clear the advisory graph
with the current configuration and passing tests.

## Consequences

The tool upgrade is reviewable alongside the Gmail changes and introduces no
new product runtime service or sending permission. CI/macOS preview remains
required. Future dependencies requiring installation scripts need a deliberate
review rather than enabling all scripts globally.
