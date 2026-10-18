# WebUI / CLI audit

The migration contract in `tests/webui_contract.json` records retained DOM IDs
and event handlers from the interface before TailAdmin. The Python suite
checks that those controls and their JavaScript handlers remain available,
that DOM IDs are unique, and that JavaScript's static DOM references exist.
Behavioral tests exercise the controls against an isolated account store.

| Capability | CLI | WebUI | Verification |
| --- | --- | --- | --- |
| Cached accounts and live limits | `list`, `limits --refresh` | Automatic saved-data loading, Refresh usage | Browser checks both modes, reset credits, selected exports and failed refresh recovery |
| Active native/API account | `status`, `switch`, `profiles apply` | Account selection and overview | Changes made by CLI become visible on page reload; native/API switching is exercised; previous-account navigation is removed from the WebUI |
| Login | `login`, `--manual`, `--device` | Three login methods | OAuth/PKCE and device behavior have Python tests; browser covers manual completion, pending device recovery and cancellation |
| Aliases and removal | `alias`, `remove` | Set/Edit/Clear alias, Remove | Browser changes are checked with CLI; active-account deletion remains rejected by the shared backend |
| Import | `import --format auto/standard/cpa` | Import accounts | Browser tests standard and CPA imports, invalid JSON and CLI visibility |
| Export | `export --query --format` | Export panel on Accounts | Standard and CPA downloads, selected/all counts, selection retained during refresh |
| Ping | `ping` | Ping on each account | Browser invokes the shared endpoint with a synthetic Ping implementation; isolated credentials have backend tests |
| API provider management | `profiles list/detail/create/update/parse/apply/usage/delete/export` | Separate API accounts page, modal editor, Advanced fields, TOML parser, API Export | Browser CRUD/selection/usage, CLI edits, blank-key preservation, custom header preservation, invalid advanced JSON |
| Optional provider fields | Model, reasoning, headers, policy, sandbox, catalog flags | Form fields and Advanced JSON | Shared validation; browser verifies advanced fields survive edits |
| Settings | `config show/set` | Settings switches | Browser verifies both directions against CLI, and CLI changes against refreshed WebUI |
| Auto Ping | `auto-ping status/enable/disable/once/run` | Global/per-account switches, last check, automatic worker | Browser verifies selection while globally off, global toggle and manual check; errors/retries are exposed equally to CLI and WebUI |
| Service restart | `restart --service app` | Restart services, Restart Codex app server | Browser checks the app restart using a synthetic restart implementation; backend checks actual command construction |
| Diagnostics | `doctor`, `--fix-permissions` | Check health, Fix private file permissions | Browser makes a synthetic snapshot readable, detects it, repairs it and checks mode 0600 |
| Web lifecycle | `web enable/disable/start/serve/status` | Enable WebUI, server status | Browser compares runtime status with CLI, then disables the actual fixture server; Python tests CLI re-enable/start and released ports |

A disabled web server cannot display its UI or start itself. Re-enabling and
starting it remains a CLI action, as the WebUI's shutdown message explains.
CLI file/stdin flags correspond to the browser's paste fields/downloads; the
browser does not manipulate arbitrary server files.

The browser suite uses synthetic credentials and mocked OpenAI/usage/Ping and
service-restart responses. It does not claim to validate a real external OAuth
exchange or real provider availability. Desktop/mobile layout checks cover all
six pages and overlay forms at 375, 768, 1024, 1280 and 1536px, both themes, keyboard focus and
navigation after resizing. Production smoke checks are read-only.

Run:

```bash
python3.12 -m unittest discover
npm run build
PYTHON=python3.12 npm run test:ui
```

Account creation and editing use modal overlays with an inert, blurred background,
Escape/backdrop closing and keyboard focus containment. Draft tests cover login,
import, API creation and API editing, including session-cache recovery on reload,
separate draft keys, clearing after success, and explicit discard. API export
downloads are compared with private CLI exports; ordinary profile lists/details
continue to omit API keys. Native and API exports stay on their respective pages.

Dashboard is the default page and exclusively contains Automatic saved-data loading, Refresh usage
and Restart services. The browser suite checks visibility of the dashboard controls
on every page, absence of Previous account, and dashboard counters against CLI
`activity --json`. Native Ping (manual/automatic) and native/API switches record
success/failure and timing in a private file. Counters survive renames and process
restarts. API Ping is unavailable because API profiles have no Ping operation.

The dashboard now provides direct native Ping and native/API Switch controls.
It disables the active account's Switch button and prevents duplicate operations
while a request is pending. Successful, failed and cancelled operations refresh
its rows without navigating away. Detailed counters are on the separate Activity page before Settings.
`tests/dashboard_controls.cjs` verifies these flows against isolated accounts,
including API selection and return to the already authenticated native account.

Restart commands are editable for Codex app server in Settings and via
`config restart-commands --file ...` / `--defaults`. The HTTP and CLI share
validation, persistence and execution. Tests verify quoted arguments, failure
short-circuiting, timeouts, missing executables, private storage and invalid-input
rejection. Saving commands never executes them. Dashboard usage uses the native
cached 5-hour/weekly windows; monetary Credits are omitted from native cards.

Use `PYTHON=python3.12 npm run test:storage` for storage-related verification
without the general layout/theme/draft matrix. It exercises login and the shared
CLI/WebUI account, API profile, export, usage, diagnostics, settings and lifecycle
flows against the independent vault. Assertions verify that no legacy
`CODEX_HOME/accounts` directory is created. The saved-data button was removed;
opening the interface still loads saved state without a remote usage refresh.
