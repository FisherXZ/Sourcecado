# Sourcecado brand assets

Masters for the painted owl mascot. The owl wears an avocado satchel. Personality belongs in the dock icon, the rail mark, first-run welcome, empty states, and marketing — not on the board or other dense tables.

## Mapping

| File | Role | Used as |
| --- | --- | --- |
| `app-icon.png` | Tight crop of the flight pose, 1024px | Dock / `.icns` / favicon / rail mark |
| `mascot/owl-flight.jpg` | App-icon source pose. Cleanest square character on a simple sky. | Cropped into `app-icon.png` |
| `mascot/owl-mapping.jpg` | Pose: planning at the map | Empty-thread illustration |
| `mascot/owl-meadow.jpg` | Pose: outbound over wildflowers | Stored for later empty/onboarding moments |
| `marketing/landing-hero.jpg` | Landing / welcome background. Copy lives in the left sky. | First-run welcome in the desktop app |
| `marketing/social-card-light.jpg` | Landing social card (light) | GitHub README preview |
| `marketing/social-card-dark.jpg` | Landing social card (dark) | Stored for GitHub/LinkedIn social preview |

## Desktop wiring

The GUI serves this folder at `/brand/` via `desktop/surfaces/gui/public/brand`.

## Regenerating app icons

From the repository root:

```sh
python3 desktop/packaging/make_icon.py
```

That reads `app-icon.png` and writes `desktop/surfaces/gui/src-tauri/icons/` plus `desktop/surfaces/gui/public/favicon.png`.

These JPEGs were transcoded on import (about 1.9–2.5 MB originals down to ~300–450 KB). Drop the original files in place if you still have them.
