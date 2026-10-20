# Deployment

The examples assume an existing Codex installation and a trusted OS user. Adapt
paths, usernames, and hostnames before installing them. No live server domain,
credentials, or machine-specific systemd unit is included in the repository.

## Local or SSH access

The default server binds to `127.0.0.1:8765`:

```bash
python3 app.py web start
```

From another machine, use an SSH tunnel:

```bash
ssh -N -L 8765:127.0.0.1:8765 user@server
```

Open `http://127.0.0.1:8765` locally. Do not bind an unauthenticated instance to a
public interface.

## Optional Telegram dependency

Install `requirements-telegram.txt` into a virtual environment and use that
interpreter for the WebUI process:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-telegram.txt
.venv/bin/python app.py web start
```

When running under systemd, change the unit's `ExecStart` interpreter to the
absolute path of `.venv/bin/python`, reload systemd, and restart the service.
Then configure **Settings → Telegram bot** and send `/start` in the bot's private
chat to show the keyboard. Native CLI/WebUI operations do not require aiogram.

## Per-user systemd service

`codex-account-manager.service` expects the checkout at
`~/Projects/codex-account-manager` and Python 3.11+ at `/usr/bin/python3`. Edit
those paths if needed, then run as the account that owns the Codex installation:

```bash
python3 app.py web enable
mkdir -p ~/.config/systemd/user
cp codex-account-manager.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now codex-account-manager.service
```

To run Auto Ping without the WebUI, use `codex-account-auto-ping.service` instead.
Configure global/per-account Auto Ping through the CLI first. Do not run a second
standalone worker solely because the WebUI already has one; shared locks prevent
overlapping worker cycles, but a single intended worker is easier to operate.

## System service

`deploy/codex-account-manager.system.service` is a template for a checkout at
`/opt/codex-account-manager`, running as user/group `codex` with home `/home/codex`.
Use the OS user that should own the accounts and operate the intended Codex
installation. That user's CLI and service must use the same config/data overrides.

1. Install Python 3.11+ and the project files, including the entire `web/` tree.
2. Adapt `User`, `Group`, `WorkingDirectory`, `HOME`, `CODEX_HOME`, executable
   paths, `CODEX_ACCOUNT_MANAGER_CONFIG`, and the trusted proxy hostname.
3. Run `python3 app.py web enable` as the service user with the same environment.
4. Install the edited unit as `/etc/systemd/system/codex-account-manager.service`.
5. Reload systemd and enable/start the service.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now codex-account-manager.service
sudo systemctl status codex-account-manager.service
```

The service stays on loopback. `--allowed-host manager.example.com` accepts the
proxy hostname; replace it with your actual hostname. Host validation is not
user authentication.

Default restart commands may need different permissions or commands on your
machine. Configure them through Settings or `config restart-commands --file`.
Commands run as the service user. There is no automatic privilege escalation.
Automatic restart after switching is disabled until explicitly enabled.

## Authenticated HTTPS reverse proxy

`deploy/nginx.conf` uses `manager.example.com` as a placeholder and protects the
entire UI/API with HTTP Basic authentication. `deploy/nginx-proxy.conf` is its
shared upstream snippet. Keep the Python upstream on loopback.

1. Point your hostname to the server and obtain its TLS certificate with your
   preferred ACME client.
2. Replace the example hostname and certificate paths in `deploy/nginx.conf`.
3. Create a password file with an interactive prompt; the example Nginx config
   expects `/etc/nginx/.codex-account-manager.htpasswd`:

   ```bash
   sudo htpasswd -c /etc/nginx/.codex-account-manager.htpasswd YOUR_USERNAME
   ```

   Use `-c` only when creating a new file. Grant the Nginx worker read access and
   restrict access for unrelated users.

4. Install `deploy/nginx-proxy.conf` as
   `/etc/nginx/snippets/codex-account-manager-proxy.conf` and install the edited
   site configuration according to your distribution.
5. Validate the configuration before reloading:

   ```bash
   sudo nginx -t
   sudo systemctl reload nginx
   ```

The template logs URL paths without query strings, disables proxy buffering/cache,
and accepts requests up to 11 MB for credential imports. Authentication covers
`/api/login/` too. The callback URL/code should never appear in access logs.
The certificate must already exist before the HTTPS template can validate.

## Storage and backups

Settings default to `~/.config/codex-account-manager/config.json`. The vault,
profiles, activity, locks, and migration backups default to the adjacent `data/`
directory. `XDG_CONFIG_HOME`, `CODEX_ACCOUNT_MANAGER_CONFIG`, and
`CODEX_ACCOUNT_MANAGER_DATA_DIR` allow relocation.

Back up both settings and data privately. They include OAuth tokens, provider
keys, account metadata, and configured commands. Stop the manager while making a
consistent backup or restoring one. Do not upload these files to GitHub.

The active Codex installation's `auth.json` and `config.toml` are integration
points rather than the account registry. Another client using the same Codex
home can change its active selection. The manager does not automatically enroll
unknown accounts selected by those clients.
