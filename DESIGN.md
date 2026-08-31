# Design System — Sourcecado

> **"Warm Operator."** Attio/Linear density and speed, broken on exactly two axes
> (warm neutrals + avocado accent), with personality concentrated in the
> low-density moments. Fast and dense for long sessions, but it greets you like
> Codeology, not a Bloomberg terminal.

## Product Context
- **What this is:** A local desktop assistant that helps a sourcing director find people, prepare and approve outreach, keep conversations moving, and maintain durable person files.
- **Who it's for:** One Codeology Sourcing Director in the current spring (an operator, not a visitor), with records designed to be useful to the next officer. Long, dense work sessions.
- **Space/industry:** Sourcing/recruiting data tools. Peers: Gem, hireEZ, Clay, Apollo. Visual peers: Linear, Attio, Retool, Notion.
- **Project type:** Data-dense local desktop work app with chat as home.

## Aesthetic Direction
- **Direction:** Industrial/Utilitarian × Organic — "Warm Operator."
- **Decoration level:** Intentional. The dense table stays quiet and legible. Warmth and avocado character live in empty states, onboarding, the nav header, and run-ledger "win" moments. **Never decorate the table.**
- **Mood:** Calm, dense, fast, trustworthy — but warm and human. Confidence through restraint, not chrome.
- **Memorable thing:** "Warm, approachable, friendly, on-theme with avocado/Codeology." Every decision serves this. The painted owl with the avocado satchel is the mascot and dock icon. Masters live in `brand/`. The owl belongs in the dock, the rail mark, first-run welcome, and empty states — never on Contacts or other dense tables. The geometric avocado-pit motif remains a color/shape language, not the app icon.
- **Reference sites:** attio.com (density), linear.app (chrome restraint), clay.com (warmth concentrated in low-density zones).

## Typography
- **Display/Hero:** General Sans (500/600) — humanist grotesque, warm letterforms with real personality. Carries "friendly" without loosening density.
- **Body:** General Sans (400/500). 13px in dense surfaces, 14px in prose/marketing.
- **UI/Labels:** General Sans (same as body). Uppercase mono micro-labels use Geist Mono.
- **Data/Tables:** General Sans with `font-variant-numeric: tabular-nums` on any numeric column. Compact letterforms save horizontal space.
- **Code/IDs/Ledger figures:** Geist Mono — record IDs, Apollo credits, fit scores, run numbers, dates.
- **Loading:**
  - General Sans: `https://api.fontshare.com/v2/css?f[]=general-sans@400,500,600,700&display=swap`
  - Geist Mono: `https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500&display=swap` (or self-host via `geist` npm package)
- **Scale (px):** display 40–52 / h1 24 / h2 18 / body 14 / dense-body 13 / metadata 12 / micro-label 11. Tracking: tight on display (-.025em), near-zero on body, +.04–.1em on uppercase mono labels.

## Color
- **Approach:** Balanced — warm neutrals + one disciplined avocado accent + a terracotta "pit" secondary. Color is reserved and meaningful; the accent is not decoration.
- **Primary (Accent · Avocado):** `#5B8C2A` — primary buttons, focus rings, active nav, links, score bars. Tint `#EBF1DF` (selected row, success pill bg). Deep `#3E5E1C` (accent text on tint, hover).
- **Secondary (Pit · Terracotta):** `#C2703D` — sparing only: "needs attention," secondary highlights, the avocado-pit motif. Tint `#F7EADF`.
- **Neutrals (warm — never blue-gray):**
  - Canvas `#FAF8F3` (warm cream, the app background — not white)
  - Surface `#FEFDFB` (warm white — cards, tables, panels)
  - Raised `#F5F2EA` (hover/overlay step-up)
  - Text `#2B2722` (warm near-black — **never #000**)
  - Muted `#6B6259` (warm stone; darkened from #78716C to clear WCAG AA 4.5:1 on the cream canvas for body-sized supporting copy)
  - Border `#E7E3DA` (warm greige hairline)
- **Semantic (tinted-bg pills, dark text — never saturated dots):**
  - success: text `#3E5E1C` on `#EBF1DF` (reuses the green family)
  - warning: text `#92591B` on `#FBEFD8`
  - error: text `#9B3B2E` on `#FBE6E1` (warm brick, not pure red)
  - info: reuse muted/neutral or pit-tint; avoid introducing a cold blue
- **Dark mode (warm dark — brown-black, not blue-black; reduce saturation, brighten accent):**
  - Canvas `#1A1815` · Surface `#232019` · Raised `#2C2821`
  - Text `#F0EBE2` · Muted `#A8A096` · Border `#3A352D`
  - Accent `#8FBF5C` · Accent tint `#2C3320` · Accent deep `#B7DC8C`
  - Pit `#D98A57` · Pit tint `#36281D`
  - warning text `#E0B062` on `#3A2E16` · error text `#E08A77` on `#3A211B`
- **Anti-pattern guard:** no purple/violet gradients, no gradient CTAs, no cold #FFFFFF canvas, no beige-on-beige (keep text genuinely dark on cream).

## Spacing
- **Base unit:** 4px.
- **Density:** Compact. Body 13px, metadata 12px, table row height 36–38px, cell padding 8px horizontal / 0 vertical (height does the work).
- **Scale:** 2xs(2) xs(4) sm(8) md(16) lg(24) xl(32) 2xl(48) 3xl(64). Reserve generous spacing (24–32px) for *between zones* and empty states — never inside dense rows.

## Layout
- **Approach:** Hybrid, grid-disciplined app shell.
- **Shell:** Persistent global rail / center work surface / contextual inspector when evidence or a person brief needs detail. The inspector may collapse on narrow windows; it must never trap the operator away from the thread.
- **Hero object:** The conversation is home. Dense Contacts and person-file views are destinations in the rail; structured tool results appear inline when the assistant is doing sourcing work.
- **Grid:** Desktop-first shell with responsive collapse behavior. Content within panes uses a 4px rhythm and maintains usable composer and approval controls at narrow widths.
- **Border radius:** 8px cards/inputs/panels · 6px buttons · 4px tiny inline tags · full (9999px) pills/avatars/toggles. Friendly-precise band — avoid ≥12px (toy) and ≤4px (clinical).
- **Elevation:** Borders over shadows for dense surfaces (1px `#E7E3DA` hairlines). Reserve soft shadows for floating elements only — command palette, Gmail-draft popup, inspector.

## Motion
- **Approach:** Minimal-functional, with one allowance for warmth in low-density moments.
- **Use:** inspector slide-in, command-palette fade, row hover, filter-chip toggle. A gentle entrance is OK in ledger "win"/celebration moments only. Nothing that slows an operator.
- **Easing:** enter `ease-out` · exit `ease-in` · move `ease-in-out`.
- **Duration:** micro 50–100ms · short 150–250ms · medium 250–400ms · long 400–700ms (low-density moments only).

## Design Decisions & Risks
- **Risk taken — warm cream/greige neutrals instead of cold white.** Gain: instant warmth and differentiation at zero density cost. Guard: keep text genuinely dark (`#2B2722`) so cream reads cozy, not muddy.
- **Risk taken — avocado green as primary accent (not cobalt/violet).** Gain: on-theme, memorable, rare in category. Guard: accent-green is deep/flesh; success uses tint+dark text so accent vs. semantic green don't collide.
- **Risk taken — General Sans, not Inter.** Gain: warmth in the text texture itself. Guard: verified legible at 13px in the preview.
- **Safe (category baseline, kept deliberately):** three-pane shell, spreadsheet-grade table with tinted pills, 13px dense rows + tabular numerals, hairline borders over shadows, keyboard-first affordances.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-06-18 | Initial design system created ("Warm Operator") | Created by /design-consultation. Direction chosen: data-dense operator tool with warm/avocado character. Research-backed (Attio/Linear density + Clay warmth split). Approved after live HTML preview. |
| 2026-08-25 | Chat became the home surface | The sourcing-director job begins with a target and a conversation; Contacts reports active sequences and the person file preserves the full record. |
| 2026-08-26 | Local desktop became the active product | The React/Vite/Tauri shell and Python backend replaced the hosted web app as the implementation target. |
| 2026-08-28 | Owl mascot replaced the geometric avocado dock icon | New painted brand set: flight pose as the app icon, mapping pose on empty threads, meadow pose stored, landing-hero on first-run welcome, light social card on the README. |
