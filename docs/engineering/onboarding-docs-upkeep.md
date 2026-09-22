# Onboarding documentation upkeep

Status: documentation maintenance receipt, checked 2026-09-21. This is not a
product specification or release acceptance report.

The [contributor guide](../../CONTRIBUTING.md) is the starting point for a new
teammate. The [architecture review](../audit/2026-09-21-onboarding-architecture-review.md)
and [correctness review](../audit/2026-09-21-onboarding-correctness-review.md)
record the bounded source review behind the open gaps. Issue dispositions live
in the [reconciled backlog](../audit/2026-09-21-onboarding-backlog.md).

## Corrections made

- The desktop setup guide names Python 3.14 and Node 24, creates the external
  state directory before copying configuration, and explains the environment
  precedence and `backend/.env` fallback. Both browser-development processes
  need the same exported state path; Vite reads the token at startup.
- The packaging guide distinguishes the development config used by
  `make native` from the default config that requires a frozen backend. A native
  development launch is not evidence that a packaged release works.
- Approved-send documentation separates person-bound reviewed authority from
  generic chat approval. It records the case where Gmail succeeds but person
  filing fails; a missing receipt must not cause another send.
- Run, update, and Doctor guides distinguish implemented fencing, quarantine,
  and restart classification from the still-missing continuation, final-answer
  delivery, and person-projection repair in #128. Doctor's stale unregistered
  Agent Run store description now matches the version 2 migration registry.
- Living-brief and reply-filing guides expose source-warning persistence, raw
  snippet interpretation, retained completion outcomes, and unresolved reply
  gaps rather than presenting those projections as complete.

## Verification and limits

Corrections were checked against the active launcher, Vite/Tauri configuration,
approval executors, restart code, migration registry, brief projection, and reply
filing. Changed Markdown links and whitespace were checked locally. Historical
dated documents and ADRs were preserved; subsystem explanations were retained.

The main onboarding work verified a fresh Python 3.14 / Node 24 dependency
installation, browser startup with isolated credential-free state and no browser
JavaScript errors, 1,954 Python tests passed (3 skipped), 582 GUI tests passed,
the GUI build, and all 15 deterministic sourcing scenes. Native development
startup produced successful backend HTTP and WebSocket connections. The native
visual inspection tool timed out, so native visual acceptance remains unverified.

The main task subsequently rebuilt the frozen backend with Python 3.14 and
passed its full smoke test, including person chat before and after restart.
The smoke harness now uses the project venv. See the
[setup verification receipt](../audit/2026-09-21-onboarding-setup-verification.md)
for reproduced setup failures and the complete check matrix. A full release app
bundle and installed update/rollback journey were not checked.

These checks do not certify live provider or connector behavior, process-kill
continuation, or a signed installed release. The linked review findings remain
open; documenting a limitation does not repair it. This pass did not audit every
source comment or rewrite historical implementation records.
