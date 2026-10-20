# Contributing

Use English for source comments, UI text, documentation, issue descriptions, and
pull requests. Keep descriptions concrete: explain the behavior changed and the
checks that support it.

## Local setup

Python 3.11 or newer is required. Python account management uses only the standard
library. The optional Telegram bot uses aiogram from `requirements-telegram.txt`. Node.js/npm is needed only for CSS builds and browser tests.

```bash
npm ci
npx playwright install chromium
python3 app.py --help
```

Use a temporary `CODEX_HOME`, `CODEX_ACCOUNT_MANAGER_CONFIG`, and
`CODEX_ACCOUNT_MANAGER_DATA_DIR` for manual experiments. Never test against real
credentials or an active user account store. Existing fixtures in `tests/` create
isolated homes and mock remote OAuth, usage, Ping, and service operations.

## Making a change

- Preserve independent storage: never fall back to another manager's registry.
- Keep CLI and WebUI behavior consistent through shared backend operations.
- Preserve unspecified profile fields, unknown stored metadata, and rollback behavior.
- Keep secrets out of ordinary API output, console logs, screenshots, and fixtures.
- Keep editable controls usable with a keyboard; preserve modal focus and closing.
- Avoid generated demos, additional runtime dependencies, and unrelated refactors.

Run checks for the behavior changed. Do not rerun the complete browser/layout
matrix for a documentation edit or a small isolated change.

```bash
python3 -m unittest test_independent_storage
# In a virtual environment, also verify the optional Telegram integration:
python -m pip install -r requirements-telegram.txt
python -m unittest test_telegram
PYTHON=python3 npm run test:storage     # Account/provider storage workflows
PYTHON=python3 npm run test:dashboard   # Dashboard operations
PYTHON=python3 npm run test:ui          # Complete optional UI matrix
python3 scripts/check_publication.py
```

For style changes, run `npm run build` and include `web/assets/app.css`. Do not
edit vendored licenses or replace third-party attribution with the project license.

## Pull requests

Describe the concrete problem and resulting behavior, list relevant validation,
and note any migration or compatibility effect. Keep the patch focused. Add a
regression test for meaningful behavior changes; avoid tests that merely repeat
implementation details.

Report security vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
