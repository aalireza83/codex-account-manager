"""Private account vault for Codex Account Manager.

The vault is independent of CODEX_HOME and other account managers. Only the
active Codex auth.json is synchronized when listing/switching accounts.
Network calls never follow redirects carrying credentials.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
import tempfile
import time
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from locking import file_lock

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
TOKEN_URL = "https://auth.openai.com/oauth/token"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


class RemoteError(RuntimeError):
    def __init__(self, status, code="request_failed"):
        self.status = status
        self.code = code
        super().__init__(f"Remote request failed ({status or 'network error'}; {code}).")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, data=None, headers=None, form=False):
    headers = dict(headers or {})
    body = None
    if data is not None:
        body = (urlencode(data) if form else json.dumps(data)).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
    headers.setdefault("User-Agent", "codex_cli_rs")
    try:
        with build_opener(NoRedirect()).open(Request(url, data=body, headers=headers), timeout=25) as response:
            result = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(result, dict):
                raise RemoteError(None, "invalid_response")
            return result
    except HTTPError as error:
        # Response bodies can contain tokens: expose only a bounded error identifier.
        code = "request_failed"
        try:
            doc = json.loads(error.read(65536))
            value = doc.get("error", {})
            value = value.get("code") or value.get("type") if isinstance(value, dict) else value
            if isinstance(value, str) and value.replace("_", "").isalnum() and len(value) < 80:
                code = value
        except (ValueError, AttributeError):
            pass
        finally:
            error.close()
        raise RemoteError(error.code, code) from None
    except (URLError, TimeoutError, OSError):
        raise RemoteError(None, "network_error") from None
    except (ValueError, TypeError):
        raise RemoteError(None, "invalid_response") from None


def jwt_claims(token):
    """Decode metadata only; these claims are never used to authorize requests."""
    try:
        segment = token.split(".")[1]
        value = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        return value if isinstance(value, dict) else {}
    except (ValueError, IndexError, TypeError, AttributeError):
        return {}


def private_write(path: Path, data: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".account-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def validate_alias(alias):
    if alias is None or alias == "":
        return ""
    if not isinstance(alias, str) or len(alias.strip()) > 100 or alias.strip().isdigit():
        raise RuntimeError("Alias must be at most 100 characters and cannot contain only digits")
    return alias.strip()


def identity(document):
    if not isinstance(document, dict):
        raise RuntimeError("Authentication must be a JSON object")
    key = document.get("OPENAI_API_KEY")
    if isinstance(key, str) and key.strip():
        digest = hashlib.sha256(key.encode()).hexdigest()
        return {"account_key": "apikey::" + digest, "email": "", "auth_mode": "apikey",
                "account_name": "API key " + digest[:8], "chatgpt_account_id": "", "chatgpt_user_id": ""}
    tokens = document.get("tokens")
    if not isinstance(tokens, dict) or not isinstance(tokens.get("access_token"), str) or not tokens["access_token"]:
        raise RuntimeError("ChatGPT authentication requires an access token")
    claims = jwt_claims(tokens.get("id_token", ""))
    access = jwt_claims(tokens["access_token"])
    auth = {}
    for source in (access, claims):
        value = source.get("https://api.openai.com/auth")
        if isinstance(value, dict):
            auth.update({k: v for k, v in value.items() if v is not None})
    profile = access.get("https://api.openai.com/profile")
    profile = profile if isinstance(profile, dict) else {}
    organizations = auth.get("organizations")
    organizations = [org for org in organizations if isinstance(org, dict) and isinstance(org.get("id"), str) and org["id"]] if isinstance(organizations, list) else []
    organization = next((org for org in organizations if org.get("is_default") is True), organizations[0] if organizations else {})
    account_id = tokens.get("account_id") or auth.get("chatgpt_account_id") or organization.get("id")
    user_id = auth.get("chatgpt_user_id") or auth.get("user_id") or claims.get("sub") or access.get("sub")
    if not isinstance(account_id, str) or not account_id or not isinstance(user_id, str) or not user_id:
        raise RuntimeError("Authentication is missing the ChatGPT account or user identity")
    tokens["account_id"] = account_id
    return {"account_key": f"{user_id}::{account_id}", "email": str(claims.get("email") or profile.get("email") or "").lower(),
            "chatgpt_account_id": account_id, "chatgpt_user_id": user_id,
            "plan": auth.get("chatgpt_plan_type"), "auth_mode": "chatgpt"}


def auth_timestamp(document, path):
    """Compare credential generations, using file time only as a fallback."""
    value = document.get("last_refresh")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp(),path.stat().st_mtime_ns
        except ValueError:
            pass
    tokens = document.get("tokens") or {}
    issued = number(jwt_claims(tokens.get("access_token", "")).get("iat"))
    return (issued if issued is not None else path.stat().st_mtime),path.stat().st_mtime_ns


def credential_fingerprint(document):
    return hashlib.sha256(json.dumps(document,sort_keys=True,separators=(",", ":")).encode()).hexdigest()


def matches_identity(record, metadata):
    key = record["account_key"]
    expected = metadata["account_key"]
    if key == expected:
        return True
    # Older codex-auth API-key records include an extra user-id component.
    return metadata["auth_mode"] == "apikey" and key.startswith("apikey::") and key.endswith("::" + expected.split("::")[-1])


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def quota_view(saved):
    """Allowlist legacy cache fields; malformed values remain unknown."""
    saved = saved if isinstance(saved, dict) else {}
    usage = {"reset_credits": number(saved.get("reset_credits"))}
    for name in ("primary", "secondary"):
        window = saved.get(name)
        if not isinstance(window, dict):
            continue
        usage[name] = {key: number(window.get(key)) for key in ("used_percent", "window_minutes", "resets_at")}
        used = usage[name]["used_percent"]
        if used is not None:
            usage[name]["remaining_percent"] = max(0, min(100, 100 - used))
    credits = saved.get("credits")
    if isinstance(credits, dict):
        usage["credits"] = {"has_credits": credits.get("has_credits") is True,
                            "unlimited": credits.get("unlimited") is True, "balance": number(credits.get("balance"))}
    return usage


def manager_config_path():
    configured=os.environ.get("CODEX_ACCOUNT_MANAGER_CONFIG")
    root=Path(os.environ.get("XDG_CONFIG_HOME",Path.home()/".config"))
    return Path(configured).expanduser() if configured else root/"codex-account-manager"/"config.json"


def manager_data_root():
    configured=os.environ.get("CODEX_ACCOUNT_MANAGER_DATA_DIR")
    return Path(configured).expanduser() if configured else manager_config_path().parent/"data"


class AccountStore:
    def __init__(self, home, directory=None):
        self.home = Path(home)
        self.directory = Path(directory) if directory is not None else manager_data_root()/"vault"
        self.registry_path = self.directory / "accounts.json"

    @contextmanager
    def locked(self, harden=True):
        with file_lock(self.directory / ".manager.lock"):
            if harden:
                self.directory.chmod(0o700)
            yield

    def read(self):
        try:
            doc = json.loads(self.registry_path.read_text())
        except FileNotFoundError:
            return {"schema_version": 4, "accounts": [], "active_account_key": None}
        except (OSError, ValueError):
            raise RuntimeError("Account registry cannot be read; existing files were preserved") from None
        if not isinstance(doc, dict) or not isinstance(doc.get("accounts"), list) or any(
                not isinstance(a, dict) or not isinstance(a.get("account_key"), str) for a in doc["accounts"]):
            raise RuntimeError("Invalid account registry; existing files were preserved")
        return doc

    def snapshot(self, key):
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self.directory / "credentials" / (digest + ".json")

    def _commit(self, files):
        """Roll back all files if a multi-file write fails."""
        previous = {path: path.read_bytes() if path.exists() else None for path in files}
        try:
            for path, data in files.items():
                private_write(path, data)
        except BaseException:
            for path, data in previous.items():
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    private_write(path, data)
            raise

    def _upsert(self, registry, document, alias=None):
        metadata = identity(document)
        record = next((a for a in registry["accounts"] if matches_identity(a, metadata)), None)
        if record is None:
            record = {"created_at": int(time.time()), "alias": ""}
            registry["accounts"].append(record)
        metadata["account_key"] = record.get("account_key", metadata["account_key"])
        if metadata.get("plan") is None and record.get("plan"):
            metadata.pop("plan", None)
        record.update(metadata)
        if alias is not None:
            alias = validate_alias(alias)
            if alias and any(a is not record and str(a.get("alias", "")).casefold() == alias.casefold() for a in registry["accounts"]):
                raise RuntimeError("Alias is already used by another account")
            record["alias"] = alias
        return record

    def import_documents(self, documents, alias=""):
        documents = copy.deepcopy(documents if isinstance(documents, list) else [documents])
        if not documents:
            raise RuntimeError("No accounts to import")
        alias = validate_alias(alias)
        if alias and len(documents) != 1:
            raise RuntimeError("Alias can only be applied to a single imported account")
        with self.locked():
            registry = self.read()
            files = {}
            records = []
            for document in documents:
                record = self._upsert(registry, document, alias or None)
                files[self.snapshot(record["account_key"])] = json_bytes(document)
                records.append(record["account_key"])
            files[self.registry_path] = json_bytes(registry)
            self._commit(files)
        return records

    def _sync_active(self, registry):
        """Capture tokens refreshed by Codex before switching away from an account."""
        try:
            document = json.loads((self.home / "auth.json").read_text())
            metadata=identity(document)
        except FileNotFoundError:
            registry["active_account_key"] = None
            return
        except (ValueError, RuntimeError):
            registry["active_account_key"] = None
            return  # Keyring-only or unrelated auth is left untouched.
        if not any(matches_identity(record,metadata) for record in registry['accounts']):
            # An account selected by another client is not enrolled in this vault.
            registry['active_account_key']=None
            return
        record = self._upsert(registry, document)
        saved_usage=record.get("last_usage")
        if isinstance(saved_usage,dict) and isinstance(saved_usage.get("plan_type"),str):
            record["plan"]=saved_usage["plan_type"]
        key = record["account_key"]
        snapshot = self.snapshot(key)
        fingerprint = credential_fingerprint(document)
        last_seen = record.get("last_seen_active_auth")
        should_save = True
        if snapshot.exists():
            try:
                saved = json.loads(snapshot.read_text())
                if matches_identity(record, identity(saved)):
                    if credential_fingerprint(saved) == fingerprint or last_seen == fingerprint:
                        should_save = False
                    else:
                        active_time = auth_timestamp(document, self.home / "auth.json")
                        saved_time = auth_timestamp(saved, snapshot)
                        # A known active file changed externally. Allow equal-time
                        # generations without mistaking an unchanged active file
                        # for the credentials just imported by a new login.
                        should_save = active_time > saved_time or (last_seen is not None and active_time[0] >= saved_time[0])
            except (OSError, ValueError, RuntimeError):
                pass
        if should_save:
            private_write(snapshot, json_bytes(document))
        record["last_seen_active_auth"] = fingerprint
        registry["active_account_key"] = key

    def resolve(self, registry, query):
        query = str(query).strip()
        if not query or len(query) > 300:
            raise RuntimeError("Invalid account selector")
        exact = [a for a in registry["accounts"] if a["account_key"] == query]
        if len(exact)>1:raise RuntimeError("Account key is duplicated in the registry; inspect doctor output")
        if exact:
            return exact[0]
        matches = [a for i, a in enumerate(registry["accounts"], 1) if query == str(i) or any(
            isinstance(a.get(k), str) and a[k].casefold() == query.casefold() for k in ("email", "alias", "account_name"))]
        if len(matches) != 1:
            raise RuntimeError("Account was not found or the selector is ambiguous; use its account key")
        return matches[0]

    def find(self, query):
        with self.locked():
            return copy.deepcopy(self.resolve(self.read(), query))

    def set_alias(self, query, alias):
        alias = validate_alias(alias)
        with self.locked():
            registry = self.read()
            record = self.resolve(registry, query)
            self._upsert_alias(registry, record, alias)
            private_write(self.registry_path, json_bytes(registry))
        return "Alias updated." if alias else "Alias cleared."

    @staticmethod
    def _upsert_alias(registry, record, alias):
        if alias and any(a is not record and str(a.get("alias", "")).casefold() == alias.casefold() for a in registry["accounts"]):
            raise RuntimeError("Alias is already used by another account")
        record["alias"] = alias

    def _auth(self, record):
        try:
            document = json.loads(self.snapshot(record["account_key"]).read_text())
            metadata = identity(document)
        except (OSError, ValueError):
            raise RuntimeError("Account authentication snapshot is missing or invalid") from None
        if not matches_identity(record, metadata):
            raise RuntimeError("Authentication snapshot belongs to a different account")
        return document

    @contextmanager
    def switching(self, query):
        """Keep account/auth rollback coordinated with the caller's config transaction."""
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            if query == "-":
                query = registry.get("previous_active_account_key")
                if not query:
                    raise RuntimeError("No previous account is available")
            record = self.resolve(registry, query)
            auth = self._auth(record)
            auth_path = self.home / "auth.json"
            previous_auth = auth_path.read_bytes() if auth_path.exists() else None
            previous_registry = self.registry_path.read_bytes() if self.registry_path.exists() else None
            if record["account_key"] != registry.get("active_account_key"):
                registry["previous_active_account_key"] = registry.get("active_account_key")
            registry["active_account_key"] = record["account_key"]
            record["last_used_at"] = int(time.time())
            record["last_seen_active_auth"] = credential_fingerprint(auth)
            self._commit({auth_path: json_bytes(auth), self.registry_path: json_bytes(registry)})
            try:
                yield copy.deepcopy(record)
            except BaseException:
                for path, data in ((auth_path, previous_auth), (self.registry_path, previous_registry)):
                    if data is None:
                        path.unlink(missing_ok=True)
                    else:
                        private_write(path, data)
                raise

    def remove(self, query):
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            record = self.resolve(registry, query)
            if record["account_key"] == registry.get("active_account_key"):
                raise RuntimeError("Switch to another account before removing the active account")
            registry["accounts"].remove(record)
            if registry.get("previous_active_account_key")==record["account_key"]:
                registry["previous_active_account_key"]=None
            private_write(self.registry_path, json_bytes(registry))
            self.snapshot(record["account_key"]).unlink(missing_ok=True)
        return "Account removed."

    def export_documents(self, queries=None, format_name="standard"):
        if format_name not in ("standard", "cpa"):
            raise RuntimeError("Export format must be standard or cpa")
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            records = [self.resolve(registry, query) for query in queries] if queries else registry["accounts"]
            documents = []
            for record in records:
                document = self._auth(record)
                if format_name == "cpa":
                    if document.get("OPENAI_API_KEY"):
                        raise RuntimeError("CLIProxyAPI export supports ChatGPT accounts only; use standard")
                    document = {**document["tokens"], "last_refresh": document.get("last_refresh"), "type": "codex"}
                documents.append(document)
            private_write(self.registry_path, json_bytes(registry))
            return documents

    def doctor(self, fix_permissions=False):
        issues = []
        def issue(code, message, account_key=None):
            issues.append({"code": code, "message": message, "account_key": account_key})
        if not self.directory.exists():
            return {"healthy": True, "issues": [], "accounts_checked": 0, "message": "No account store yet."}
        with self.locked(harden=False):
            try:
                registry = self.read()
            except RuntimeError:
                issue("invalid_registry", "Registry is unreadable; preserve it and restore/import a valid copy.")
                return {"healthy": False, "issues": issues, "accounts_checked": 0}
            paths = [(self.directory, 0o700), (self.directory/"credentials",0o700), (self.registry_path, 0o600)]
            aliases, keys, expected = set(), set(), set()
            for record in registry["accounts"]:
                key = record["account_key"]
                if key in keys:
                    issue("duplicate_identity", "Duplicate account key in registry.", key)
                keys.add(key)
                alias = str(record.get("alias") or "").casefold()
                if alias and alias in aliases:
                    issue("duplicate_alias", "Account alias is ambiguous.", key)
                aliases.add(alias)
                snapshot = self.snapshot(key)
                expected.add(snapshot.name)
                paths.append((snapshot, 0o600))
                try:
                    doc = self._auth(record)
                    expiry = number(jwt_claims((doc.get("tokens") or {}).get("access_token", "")).get("exp"))
                    if expiry is not None and expiry <= time.time() and not (doc.get("tokens") or {}).get("refresh_token"):
                        issue("login_required", "Access token expired and no refresh token is stored; sign in again.", key)
                except RuntimeError:
                    issue("invalid_snapshot", "Auth snapshot is missing, invalid, or belongs to a different account.", key)
            for path in (self.directory/"credentials").glob("*.json"):
                if path.name not in expected:
                    issue("unregistered_snapshot", "An unregistered snapshot is present; import it to recover it.")
            active = self.home / "auth.json"
            if active.exists():
                paths.append((active, 0o600))
                try:
                    identity(json.loads(active.read_text()))
                except (OSError, ValueError, RuntimeError):
                    issue("invalid_active_auth", "Current auth.json is invalid or uses unsupported credentials.")
            for path, mode in paths:
                if path.exists() and path.stat().st_mode & 0o777 != mode:
                    if fix_permissions:
                        path.chmod(mode)
                    else:
                        issue("permissions", f"{path.name} permissions should be {mode:04o}.")
            return {"healthy": not issues, "issues": issues, "accounts_checked": len(registry["accounts"]),
                    "permissions_fixed": fix_permissions}

    @contextmanager
    def ping_credentials(self, query):
        # Serialize token rotation with other manager processes for this home.
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            record = self.resolve(registry, query)
            document = self._auth(record)
            private_write(self.registry_path, json_bytes(registry))
            active_path = self.home / "auth.json"
            active_before = active_path.read_bytes() if active_path.exists() else None
            def save(updated):
                if not matches_identity(record, identity(updated)):
                    raise RuntimeError("Ping returned credentials for a different account")
                files = {self.snapshot(record["account_key"]): json_bytes(updated)}
                active = self.home / "auth.json"
                try:
                    if active.read_bytes() == active_before and matches_identity(record, identity(json.loads(active.read_text()))):
                        files[active] = json_bytes(updated)
                        record["last_seen_active_auth"] = credential_fingerprint(updated)
                        files[self.registry_path] = json_bytes(registry)
                except (OSError, ValueError, RuntimeError):
                    pass
                self._commit(files)
            label = record.get("alias") or record.get("email") or record.get("account_name") or query
            yield document, label, save

    def _refresh_tokens(self, record, document):
        tokens = document["tokens"]
        active_path = self.home / "auth.json"
        active_before = active_path.read_bytes() if active_path.exists() else None
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise RuntimeError("Session expired; sign in again")
        response = request_json(TOKEN_URL, {"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                            "refresh_token": refresh}, form=True)
        if not isinstance(response.get("access_token"), str) or not response["access_token"]:
            raise RuntimeError("Token refresh returned an invalid response")
        for key in ("access_token", "id_token", "refresh_token"):
            if isinstance(response.get(key), str) and response[key]:
                tokens[key] = response[key]
        document["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        files = {self.snapshot(record["account_key"]): json_bytes(document)}
        # Never change which account is active during a quota refresh.
        active_path = self.home / "auth.json"
        try:
            active = json.loads(active_path.read_text())
            if active_path.read_bytes() == active_before and identity(active)["account_key"] == record["account_key"]:
                files[active_path] = json_bytes(document)
                record["last_seen_active_auth"] = credential_fingerprint(document)
        except (OSError, ValueError, RuntimeError):
            pass
        self._commit(files)
        return document

    def refresh_auth(self, query):
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            record = self.resolve(registry, query)
            document = self._auth(record)
            expiry = jwt_claims(document.get("tokens", {}).get("access_token", "")).get("exp")
            if isinstance(expiry, (int, float)) and expiry <= time.time() + 60:
                document = self._refresh_tokens(record, document)
            private_write(self.registry_path, json_bytes(registry))
            return document

    def _usage(self, record):
        document = self._auth(record)
        if record.get("auth_mode") == "apikey" or document.get("OPENAI_API_KEY"):
            return {"requested": True, "status": "unsupported", "error_code": "api_key_usage_unavailable"}
        expiry = jwt_claims(document["tokens"]["access_token"]).get("exp")
        refreshed = False
        if isinstance(expiry, (int, float)) and expiry <= time.time() + 60:
            document = self._refresh_tokens(record, document)
            refreshed = True
        def fetch():
            return request_json(USAGE_URL, headers={"Authorization": "Bearer " + document["tokens"]["access_token"],
                                                  "ChatGPT-Account-Id": record["chatgpt_account_id"]})
        try:
            response = fetch()
        except RemoteError as error:
            if error.status != 401 or refreshed:
                raise
            document = self._refresh_tokens(record, document)
            response = fetch()
        rate = response.get("rate_limit")
        rate = rate if isinstance(rate, dict) else {}
        reset_credits = response.get("rate_limit_reset_credits")
        reset_credits = reset_credits if isinstance(reset_credits, dict) else {}
        usage = {"plan_type": response.get("plan_type"), "credits": response.get("credits"),
                 "reset_credits": number(reset_credits.get("available_count"))}
        for source, target in (("primary_window", "primary"), ("secondary_window", "secondary")):
            window = rate.get(source)
            if not isinstance(window, dict):
                continue
            used = number(window.get("used_percent"))
            if used is None:
                continue
            seconds = number(window.get("limit_window_seconds"))
            reset = number(window.get("reset_at"))
            after = number(window.get("reset_after_seconds"))
            if reset is None and after is not None:
                reset = int(time.time() + after)
            usage[target] = {"used_percent": used,
                             "window_minutes": math.ceil(seconds / 60) if seconds is not None and seconds > 0 else None,
                             "resets_at": reset}
        if not any(usage.get(k) is not None for k in ("primary", "secondary", "reset_credits", "credits")):
            raise RuntimeError("Usage response contains no rate limit information")
        record["last_usage"] = usage
        record["last_usage_at"] = int(time.time())
        if response.get("plan_type"):
            record["plan"] = response["plan_type"]
        return {"requested": True, "status": "ok"}

    def list(self, refresh=False, cancel_event=None):
        with self.locked():
            registry = self.read()
            self._sync_active(registry)
            views = []
            for number, record in enumerate(registry["accounts"], 1):
                if refresh and cancel_event is not None and cancel_event.is_set():break
                status = {"requested": False, "status": "cached"}
                if refresh:
                    try:
                        status = self._usage(record)
                    except RemoteError as error:
                        status = {"requested": True, "status": "error", "http_status": error.status, "error_code": error.code}
                    except (RuntimeError, ValueError, TypeError, KeyError):
                        status = {"requested": True, "status": "error", "error_code": "invalid_auth_or_usage"}
                # Explicit allowlist prevents imported registry fields leaking secrets.
                view = {key: record.get(key) for key in ("account_key", "email", "alias", "account_name", "plan", "auth_mode", "created_at", "last_used_at")}
                usage = quota_view(record.get("last_usage"))
                usage.update(updated_at=record.get("last_usage_at"), source="api" if status["status"] == "ok" else "cache", refresh=status)
                view.update(number=number, active=record["account_key"] == registry.get("active_account_key"), usage=usage)
                views.append(view)
            private_write(self.registry_path, json_bytes(registry))
        return {"accounts": views, "mode": "Native · " + ("remote refresh" if refresh else "local/cached"), "warning": None}
