# Connections and Scheduled: QA evidence (2026-11-02)

The real app ran against fake services: FakeGmail, a fake HTTP layer for the
Google, Granola and Monid endpoints, stubbed connector probes, and a slow fake
model. No real account, key, email or credit was used.

- `before-*.png`: `main` at 46f68c2, from the existing QA app on port 5180.
- Every other PNG: this branch, at 1440 and 390 px, light theme, driven by a
  Playwright script. The OAuth redirects were played by calling the callback
  with the recorded `state`.
- Request interception was used only for the loading state, the load-error
  state and the missing-Google-credentials refusal.
- `capture-log.txt`: the script log, with the jobs and runs read back from
  `GET /v1/schedule` after the UI edits.
