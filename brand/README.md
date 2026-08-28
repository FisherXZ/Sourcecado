# Sourcecado brand assets

Masters for the painted owl mascot. The owl wears an avocado satchel. Personality belongs in the dock icon, the rail mark, first-run welcome, empty states, and marketing — not on the board or other dense tables. This docs and brand pass does not wire the owl into the desktop icon outputs.

## Mapping

| File | Role | Intended use |
| --- | --- | --- |
| `app-icon.png` | Tight crop of the flight pose, 1024px | Dock / `.icns` / favicon / rail mark |
| `mascot/owl-flight.jpg` | App-icon source pose. Cleanest square character on a simple sky. | Cropped into `app-icon.png` |
| `mascot/owl-mapping.jpg` | Pose: planning at the map | Empty-thread illustration |
| `mascot/owl-meadow.jpg` | Pose: outbound over wildflowers | Stored for later empty/onboarding moments |
| `marketing/landing-hero.jpg` | Landing / welcome background. Copy lives in the left sky. | First-run welcome in the desktop app |
| `marketing/social-card-light.jpg` | Landing social card (light) | GitHub README preview |
| `marketing/social-card-dark.jpg` | Landing social card (dark) | Stored for GitHub/LinkedIn social preview |

## Desktop icon wiring

The owl is not wired into the desktop icon outputs in this change. `desktop/packaging/make_icon.py` still renders the geometric avocado and does not read `brand/`. Do not run it expecting owl icons. The separate wiring change must update the generator or the committed icon outputs before this becomes the dock icon.

These JPEGs were transcoded on import (about 1.9–2.5 MB originals down to ~300–450 KB). Drop the original files in place if you still have them.
