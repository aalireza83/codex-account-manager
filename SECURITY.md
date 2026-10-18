# Security

This application stores authentication credentials and can change the active
Codex configuration or execute administrator-configured restart commands.

## Reporting a vulnerability

Do not report vulnerabilities containing tokens, API keys, account exports, or
private server details in a public issue or pull request. Use the repository's
**Security → Report a vulnerability** feature when available. If private reporting
is unavailable, contact the maintainer through an existing private channel and
request a secure reporting method before sending details.

Include a minimal reproduction with synthetic credentials, the affected version
or commit, the expected boundary, and the observed impact. Please test against the
latest source and avoid including real account data in attachments.

## Deployment boundary

The bundled HTTP server has no built-in login system. Its default loopback binding
is intentional. Remote access needs a trusted SSH tunnel or an authenticated HTTPS
reverse proxy protecting all UI and API paths. Trusted Host validation and
same-origin mutation checks do not replace authentication.

Stored credentials and exports are private files. Browser form drafts can include
API keys in tab-scoped session storage. Exported files contain credentials and must
be protected by the person who downloads them.

Restart commands run as the OS user of the manager. Saving commands does not run
them, but a later explicit or enabled automatic restart will. Use only commands
and executables appropriate for that service account.

The independent vault does not isolate the active Codex installation from other
clients changing its `auth.json` or `config.toml`. Do not treat this application as
an account-limit bypass, a network proxy, or an authorization boundary between
untrusted local users.
