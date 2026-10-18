# Blue dark theme

Dark Mode uses the exact color values exposed by the public
[shadcn.io Blue theme](https://www.shadcn.io/theme/blue)
[preview](https://www.shadcn.io/preview/themes/blue).
The source color tokens are recorded in [blue-dark-tokens.json](blue-dark-tokens.json).
Values remain in OKLCH; they are not approximated with a different hex palette.
The dark primary and its foreground use the dark preview's values, which differ
from the light preview's primary values. Blue buttons intentionally use white
text for readability, including hover, pressed, focus, and disabled states.
Links and blue statistics icons use a lighter blue on dark surfaces, and checked
switches use a white thumb so their state remains easy to identify.

`web/src/blue-dark.css` adapts those tokens to TailAdmin inside `.dark` only.
`web/src/app.css` retains the existing light rules, typography, spacing, radii,
responsive layout, and modal behavior. The supplied logo asset is unchanged.

| TailAdmin area | Blue token |
| --- | --- |
| Page background and main text | `background`, `foreground` |
| Cards and ordinary panels | `card`, `card-foreground` |
| Modals and select options | `popover`, `popover-foreground` |
| Primary actions and usage bars | `primary`; white text on blue buttons |
| Neutral controls and hover/active surfaces | `secondary`, `accent` |
| Labels, saved-data metadata, and scrollbars | `muted-foreground` |
| Control borders and keyboard focus | `input`, `ring` |
| Navigation and active navigation indicator | `sidebar-*` |
| Error states and destructive actions | `destructive` |
| Success, warning, and auxiliary status colors | `chart-2`, `chart-3`, `chart-4` |

Hover/pressed fills use transparent mixes of the corresponding source token.
Disabled text controls/buttons use 50% opacity. Checkboxes, switches, selected
export rows, validation errors, banners, copy/open controls, placeholder text,
and native popup options use the same token set. Theme rules do not change
control geometry, focus order, editing, or account operations.

## Focused verification

```bash
npm run build
PYTHON=python3 npm run test:theme
```

The browser test runs against synthetic accounts. It checks the exact computed
source colors, six pages, account/import/API/edit/alias/export/login-session
modals, hover/pressed/focus/disabled/checked/error states, and mobile layouts.
Light Mode colors and control geometry are compared with the Git baseline.
No live credentials, real login, Ping, service restart, or account switch is used.

The default comparison reads the committed CSS from `HEAD`. To compare against
another revision, set `THEME_BASELINE_REF` to that revision before running the
test. The test prints a temporary directory containing synthetic screenshots.

For changes limited to dark link/icon/switch readability and native account
card spacing, run `npm run test:readability`. This compares the affected Light
Mode colors, checks dark contrast, and verifies compact cards at desktop and
mobile widths without performing account operations.
