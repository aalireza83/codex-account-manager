# Codex Account Manager

A standalone account manager for Codex with a command-line interface and an
optional TailAdmin-based WebUI. Login, account storage, switching, token refresh,
and usage retrieval run inside this project. Neither `codex-auth` nor
`codex-multi-auth` is required.

The manager stores its own account vault and settings. It does not discover,
merge, or write another account manager's registry.

## Features

- Browser OAuth with PKCE, manual callback URLs, and device-code login.
- Multiple accounts with editable aliases, removal, and isolated credential storage.
- Five-hour and weekly usage, reset times, and available reset counts.
- Account switching from the CLI or dashboard.
- Standard and CLIProxyAPI JSON imports and exports, including selected accounts.
- Separate API-provider profiles with editing, TOML parsing, usage, and exports.
- Manual Ping and optional Auto Ping without switching the active account.
- Activity counters for successful and failed Ping/switch operations.
- Configurable restart commands shared by manual and automatic restarts.
- Optional WebUI with light/dark themes, responsive navigation, and modal forms.
- Native account management with no third-party Python dependencies.

## Requirements

- Python **3.11 or newer**, including `tomllib`.
- Linux or macOS; file locking uses `fcntl`.
- Codex CLI on `PATH` for Ping and the default daemon restart command.
- Node.js and npm only when rebuilding CSS or running browser tests. Checked-in
  web assets work without Node.js, a CDN, or a frontend development server.

`systemctl` restart commands require Linux and suitable service permissions.
Account management works without systemd when automatic restart is disabled,
which is the default.

## Quick start

Run these commands from the repository directory:

```bash
python3 app.py --help
python3 app.py login --alias personal
python3 app.py list
python3 app.py web start
```

Open **http://127.0.0.1:8765**. The server binds to loopback by default.

The WebUI is disabled on a fresh installation. `web start` enables it and runs
it in the foreground. Running `python3 app.py` without a command prints help;
it does not start a server or worker.

For a remote machine, use device login or manual callback mode:

```bash
python3 app.py login --device --alias work
python3 app.py login --manual --alias work
```

Browser OAuth uses a separate local callback listener on port 1455. Adding an
account does not switch the active account.

## CLI

Every management command supports `--json`. Login emits an initial session
object and a completion object as JSON lines.

```bash
# Accounts and usage
python3 app.py list
python3 app.py limits --refresh
python3 app.py switch work
python3 app.py switch -
python3 app.py alias work company
python3 app.py alias company                 # Clear an alias
python3 app.py remove company
python3 app.py ping personal

# Import and export
python3 app.py import ./auth.json --alias imported
python3 app.py export ./accounts.json --query personal
python3 app.py export ./accounts-cpa.json --format cpa

# Settings, diagnostics, and activity
python3 app.py config show --json
python3 app.py config set restart_after_switch false
python3 app.py doctor
python3 app.py doctor --fix-permissions
python3 app.py activity --json
```

Selectors accept an account key, exact alias, email, or list number. Use a unique
alias or account key when multiple workspaces share the same email. An active
account cannot be removed. `switch -` is available in the CLI only.

Saved usage loads without an API request. `limits --refresh` explicitly fetches
current usage. Failed refreshes retain the last saved result and report the error;
unavailable limits are shown as unknown.

### API accounts

```bash
python3 app.py profiles list
python3 app.py profiles create --name Provider --api-key-file ./private-key
python3 app.py profiles create --toml ./provider.toml
python3 app.py profiles update PROFILE_ID --model MODEL
python3 app.py profiles update PROFILE_ID --file ./partial-fields.json
python3 app.py profiles detail PROFILE_ID
python3 app.py profiles parse ./provider.toml
python3 app.py profiles apply PROFILE_ID
python3 app.py profiles usage PROFILE_ID
python3 app.py profiles export --query PROFILE_ID --output ./api-accounts.json
python3 app.py profiles delete PROFILE_ID
```

Use `--file -` to read JSON from stdin, or `--prompt-key` to enter an API key
without echoing it. Omitted fields are preserved during updates, and a blank API
key keeps the stored key. Ordinary list/detail responses do not return API keys.

**Current provider limitation:** API profiles are restricted to
`https://ttmapi.site/v1`. Their Usage URL must be an HTTPS path under that same
host's `/v1/` prefix. Other provider URLs require a code change; this is not a
general-purpose provider router.

Selecting an API profile patches only its specified settings in Codex's
`config.toml`. Returning to a native account reverts unchanged settings owned by
the profile while preserving unrelated or subsequently edited settings.

### Auto Ping

```bash
python3 app.py auto-ping enable --account personal
python3 app.py auto-ping enable
python3 app.py auto-ping status --json
python3 app.py auto-ping once
python3 app.py auto-ping run
```

Auto Ping needs both global enablement and an enabled account. It checks saved
reset information, confirms eligible candidates with a usage refresh, and records
results and retry state. The WebUI runs its worker while enabled; `auto-ping run`
runs a standalone foreground worker without the WebUI.

Ping uses an isolated temporary Codex home and keeps the active account unchanged.
Its current command selects `gpt-5.6-luna` in `_run_account_ping` in `app.py`; model
availability depends on the installed Codex CLI and account. API profiles do not
currently support Ping.

### Restart commands

```bash
python3 app.py config restart-commands --file ./restart-commands.json
python3 app.py config restart-commands --defaults
python3 app.py restart --service app
```

The default ordered commands are:

```bash
systemctl restart codex-app-server
codex app-server daemon restart
```

The configuration file accepts one `app` array:

```json
{
  "app": [
    "systemctl restart codex-app-server",
    "codex app-server daemon restart"
  ]
}
```

Settings in the WebUI edits the same commands. Saving does not execute them.
Each line runs as the manager's OS user, with quoted arguments, no implicit shell,
a 60-second timeout, and a maximum of ten commands. Execution stops on failure.
Use an explicit `bash -lc '...'` only when shell syntax is intended.

Codex web is excluded from restarts. `--service all` remains an alias for the app
restart. To run the commands automatically after switching, explicitly enable
`restart_after_switch`.

## WebUI

| Page | Controls |
| --- | --- |
| Dashboard | Native Ping, native/API Switch, native usage, Refresh usage, Restart services |
| Accounts | Add/login/import, aliases, removal, Refresh usage, Standard/CPA export |
| API accounts | Add/edit/remove profiles, usage, Refresh usage, API export |
| Diagnostics | Local account/configuration checks and private-permission repair |
| Activity | Ping and switch counts, results, and timing |
| Settings | Auto Ping, restart preferences/commands, WebUI enablement and runtime status |

Add/edit and export forms open over a blurred background. Export supports all or
selected accounts. Modal forms support keyboard focus, Escape, and backdrop
closing. Account-form drafts, including entered keys/import text, are saved in
this browser tab's session storage until successfully submitted or discarded.
OAuth callback URLs are not cached. Use **Discard draft** to clear unfinished input.

The sign-in URL is read-only and selectable, with copy/open controls. Saved data
loads automatically when opening the interface; **Refresh usage** requests a
fresh remote result. The browser displays reset times in its own timezone.

Dark Mode follows the [shadcn.io Blue palette](https://www.shadcn.io/theme/blue)
through a color-only TailAdmin adapter. Light Mode and the existing layout remain
independent. See [Dark theme](docs/dark-theme.md) for exact tokens and focused checks.

### Optional WebUI lifecycle

```bash
python3 app.py web status --json
python3 app.py web enable
python3 app.py web serve
python3 app.py web disable
python3 app.py web start --host 127.0.0.1 --port 8765
```

Disabling the WebUI stops its listener and worker. Re-enable it with the CLI.
Only one WebUI instance can hold a runtime lease for a configuration. CLI account
commands do not start a web server or background worker.

## Independent storage

| Content | Default location |
| --- | --- |
| Settings | `~/.config/codex-account-manager/config.json` |
| Account metadata | `~/.config/codex-account-manager/data/vault/accounts.json` |
| Account credentials | `~/.config/codex-account-manager/data/vault/credentials/<sha256>.json` |
| API profiles | `~/.config/codex-account-manager/data/profiles/` |
| Activity | `~/.config/codex-account-manager/data/activity.json` |
| Migration backups | `~/.config/codex-account-manager/data/migration/` |

`XDG_CONFIG_HOME` is honored. `CODEX_ACCOUNT_MANAGER_CONFIG` overrides the
settings file; `CODEX_ACCOUNT_MANAGER_DATA_DIR` overrides the data directory.
Otherwise, data lives in `data/` beside the settings file.

`CODEX_HOME` identifies the Codex installation being controlled, defaulting to
`~/.codex`. Only its active `auth.json`, `config.toml`, and optional referenced
model catalog are integration points. Using another manager against the same
Codex installation can change that active selection, but its account registry
is never merged into this vault. An unfamiliar active account is not enrolled
automatically: add it through login or import.

Credential files use mode `0600`, with private directories using `0700`.
Process locks coordinate the manager's own CLI and WebUI writes.

### One-time migration

Migration is explicit and does not run during normal startup. Stop the previous
manager first, then run:

```bash
python3 scripts/migrate_storage.py \
  --source-config ~/.config/codex-auth-webui/config.json \
  --source-accounts ~/.codex/accounts
```

Use `--target-config`, `--target-data`, and `--codex-home` for custom locations.
Migration refuses existing destinations, validates credential identities, and
retains aliases, settings, usage, activity, and provider keys. Original source
files remain intact. Only this manager's old provider references in the active
Codex config are adjusted; the previous active config is backed up privately.

## Deployment

Start with loopback access or an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 user@server
```

For a service or reverse proxy, follow [Deployment](docs/deployment.md). The
checked-in systemd and Nginx examples use placeholder users, paths, and domains.
They are templates, not a description of a running server.

The bundled HTTP server does not implement authentication. Keep it on loopback,
or place authenticated HTTPS in front of every UI/API route before exposing it.
`--allowed-host` validates the proxy hostname; it is not an authentication control.

## Development and testing

```bash
npm ci
npm run build
python3 -m unittest test_independent_storage
```

Edit `web/index.html`, `web/assets/app.js`, or `web/src/app.css`; rebuild CSS only
when styles or scanned classes change. Keep the compiled assets checked in.

For browser tests:

```bash
npx playwright install chromium
PYTHON=python3 npm run test:storage
PYTHON=python3 npm run test:dashboard
PYTHON=python3 npm run test:theme       # Dark palette and Light Mode preservation
# Optional complete UI layout/draft matrix:
PYTHON=python3 npm run test:ui
```

Tests use temporary homes and synthetic credentials. Storage tests cover shared
CLI/WebUI account/provider workflows; the complete UI suite adds layout, theme,
and draft checks. See [the parity audit](docs/webui-parity.md) for coverage and
[Contributing](CONTRIBUTING.md) for the workflow.

A ready-to-use GitHub Actions template is provided at
`.github/workflow-templates/checks.yml`. It is intentionally inactive. To enable
it, copy it to `.github/workflows/checks.yml` using an account/token permitted to
manage workflows. It runs publication checks, the independent-storage tests,
and a frontend build check.

Before a public release, run `python3 scripts/check_publication.py`. It checks
publication files, local deployment identifiers, English-only authored text, and
common credential patterns without printing matched values. It is a useful
repository check, not proof that every possible secret has been detected.

## Security and limitations

Exports contain credentials. CLI exports use `0600` and refuse to overwrite an
existing file; browser exports are explicit downloads. Do not commit exports,
account vaults, private keys, browser state, or real-token test fixtures.

`doctor` checks local storage/configuration without network access or deleting
accounts; `--fix-permissions` repairs private modes. It does not validate remote
provider availability. Tests use mocked OAuth/usage responses and do not prove
that a live sign-in or provider request will succeed.

The project does not proxy Codex traffic, rotate accounts, inject a desktop app,
or bypass account limits. It is an independent project and is not affiliated
with or endorsed by OpenAI. See [Security](SECURITY.md) for vulnerability reporting.

## License and acknowledgments

Original project code is licensed under the **MIT License**.
Copyright (c) 2026 Alireza. See [LICENSE](LICENSE) for the full terms and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for vendored asset licenses.

Reference projects: [codex-auth](https://github.com/Loongphy/codex-auth) and
[codex-multi-auth](https://github.com/ndycode/codex-multi-auth). They are references,
not runtime dependencies. TailAdmin, Outfit, and the Lobe Icons Codex symbol retain
their respective upstream notices. Product names and logos identify their
respective owners.
