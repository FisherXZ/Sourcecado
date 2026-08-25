# Manual run does not consume the weekly slot

A tick advances `next_run_at` to the next Monday 09:00 America/Los_Angeles. Run now records a run and leaves `next_run_at` alone.

We considered treating Run now as the weekly run (jump the slot). That would make a Wednesday click cancel Monday. Fisher still wants the Monday sourcing pass even if he ran one mid-week.
