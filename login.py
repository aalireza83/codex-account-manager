"""In-memory OAuth sessions shared by the CLI and WebUI.

Browser PKCE and device authorization use the public Codex OAuth client.
No passwords, authorization codes, or credentials are written to logs.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from accounts import CLIENT_ID, TOKEN_URL, RemoteError, request_json, validate_alias

REDIRECT_URI = "http://localhost:1455/auth/callback"
DEVICE_BASE = "https://auth.openai.com/api/accounts/deviceauth"
DEVICE_REDIRECT = "https://auth.openai.com/deviceauth/callback"


class LoginManager:
    def __init__(self, store_factory):
        self.store_factory = store_factory
        self.lock = threading.RLock()
        self.sessions = {}

    def start(self, method="browser", alias=""):
        if method not in ("browser", "manual", "device"):
            raise RuntimeError("Login method must be browser, manual, or device")
        alias = validate_alias(alias)
        with self.lock:
            for old in self.sessions.values():
                self._expire(old)
            if any(s["status"] in ("pending", "exchanging") for s in self.sessions.values()):
                raise RuntimeError("A login is already pending; complete or cancel it first")
            self.sessions.clear()
            session = {"id": secrets.token_urlsafe(32), "method": method, "alias": alias,
                       "status": "pending", "expires_at": time.time() + 900,
                       "stop": threading.Event(), "store": self.store_factory()}
            if method == "device":
                result = request_json(DEVICE_BASE + "/usercode", {"client_id": CLIENT_ID})
                if not all(isinstance(result.get(k), str) and result[k] for k in ("device_auth_id", "user_code")):
                    raise RuntimeError("Device authorization returned an invalid response")
                session.update(device_auth_id=result["device_auth_id"], user_code=result["user_code"],
                               url="https://auth.openai.com/codex/device")
                try:
                    session["interval"] = max(1, min(30, float(result.get("interval", 5))))
                    session["expires_at"] = time.time() + max(1, min(900, float(result.get("expires_in", 900))))
                except (ValueError, TypeError):
                    raise RuntimeError("Invalid device authorization timing") from None
            else:
                verifier = secrets.token_urlsafe(64)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                state = secrets.token_urlsafe(32)
                session.update(verifier=verifier, state=state)
                session["url"] = "https://auth.openai.com/oauth/authorize?" + urlencode({
                    "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
                    "scope": "openid profile email offline_access", "code_challenge": challenge,
                    "code_challenge_method": "S256", "state": state, "prompt": "login",
                    "id_token_add_organizations": "true", "codex_cli_simplified_flow": "true", "originator": "codex_cli_rs"})
            self.sessions[session["id"]] = session
            if method == "browser":
                self._listener(session)
            if method == "device":
                threading.Thread(target=self._device_worker, args=(session,), daemon=True).start()
            timer = threading.Timer(max(0, session["expires_at"] - time.time()), self._timeout, args=(session,))
            timer.daemon = True
            session["timer"] = timer
            timer.start()
            return self._public(session)

    def _public(self, session):
        return {key: session[key] for key in ("id", "method", "alias", "status", "expires_at", "url", "user_code", "message", "warning") if key in session}

    def _get(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            raise RuntimeError("Login session was not found")
        self._expire(session)
        return session

    def status(self, session_id):
        with self.lock:
            return self._public(self._get(session_id))

    def _expire(self, session):
        if time.time() >= session["expires_at"] and session["status"] in ("pending", "exchanging"):
            self._finish(session, "expired", "Login expired. Start a new login.")

    def _timeout(self, session):
        with self.lock:
            self._expire(session)

    def _finish(self, session, status, message):
        session.update(status=status, message=message)
        session["stop"].set()
        timer = session.pop("timer", None)
        if timer:
            timer.cancel()
        server = session.pop("server", None)
        if server:
            threading.Thread(target=self._close_server, args=(server,), daemon=True).start()
        for key in ("verifier", "state", "device_auth_id"):
            session.pop(key, None)

    @staticmethod
    def _close_server(server):
        server.shutdown()
        server.server_close()

    def cancel(self, session_id):
        with self.lock:
            session = self._get(session_id)
            if session["status"] in ("pending", "exchanging"):
                self._finish(session, "cancelled", "Login cancelled.")
            return self._public(session)

    def close(self):
        with self.lock:
            for session in self.sessions.values():
                if session["status"] in ("pending", "exchanging"):
                    self._finish(session, "cancelled", "Server stopped.")

    def complete(self, session_id, callback):
        if not isinstance(callback, str) or len(callback) > 16000:
            raise RuntimeError("Paste the full callback URL")
        parsed = urlparse(callback.strip())
        expected = urlparse(REDIRECT_URI)
        if parsed.scheme != expected.scheme or parsed.netloc != expected.netloc or parsed.path != expected.path:
            raise RuntimeError("Expected the full http://localhost:1455/auth/callback URL")
        query = parse_qs(parsed.query)
        with self.lock:
            session = self._get(session_id)
            if session["method"] == "device" or session["status"] != "pending":
                raise RuntimeError("This login is not awaiting a browser callback")
            states = query.get("state", [])
            if len(states) != 1 or not secrets.compare_digest(states[0], session["state"]):
                raise RuntimeError("OAuth state did not match. Use the URL from this login session.")
            if query.get("error"):
                self._finish(session, "error", "Authorization was declined. Start a new login to retry.")
                return self._public(session)
            codes = query.get("code", [])
            if len(codes) != 1 or not codes[0]:
                raise RuntimeError("Callback URL is missing its authorization code")
            verifier = session["verifier"]
            session["status"] = "exchanging"
        self._exchange(session, codes[0], verifier, REDIRECT_URI)
        return self.status(session_id)

    def _exchange(self, session, code, verifier, redirect):
        try:
            result = request_json(TOKEN_URL, {"grant_type": "authorization_code", "client_id": CLIENT_ID,
                                            "code": code, "code_verifier": verifier, "redirect_uri": redirect}, form=True)
            if not all(isinstance(result.get(k), str) and result[k] for k in ("access_token", "id_token", "refresh_token")):
                raise RuntimeError("Authorization returned incomplete credentials")
            document = {"auth_mode": "chatgpt", "OPENAI_API_KEY": None,
                        "tokens": {k: result[k] for k in ("access_token", "id_token", "refresh_token")},
                        "last_refresh": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            with self.lock:
                self._expire(session)
                if session["status"] != "exchanging":
                    return
                session["store"].import_documents(document, session["alias"])
                self._finish(session, "success", "Account saved. Select it from Accounts when you want to switch.")
        except (RuntimeError, OSError, ValueError):
            with self.lock:
                if session["status"] == "exchanging":
                    self._finish(session, "error", "Login could not be completed. Start a new login and try again.")

    def _listener(self, session):
        manager = self
        session_id = session["id"]

        class CallbackHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if urlparse(self.path).path != "/auth/callback":
                    self.send_error(404)
                    return
                try:
                    result = manager.complete(session_id, "http://localhost:1455" + self.path)
                    ok = result["status"] == "success"
                    body = ("Account saved. You can close this tab and return to the account manager." if ok else "Login failed. Return to the account manager to retry.").encode()
                    status = 200 if ok else 400
                except RuntimeError:
                    body = b"Invalid or expired callback. Return to the account manager."
                    status = 400
                self.send_response(status)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = ThreadingHTTPServer(("127.0.0.1", 1455), CallbackHandler)
        except OSError:
            session["warning"] = "Port 1455 is unavailable. Paste the full callback URL here or use device login."
            return
        server.daemon_threads = True
        session["server"] = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def _device_worker(self, session):
        device_id, user_code = session["device_auth_id"], session["user_code"]
        while not session["stop"].wait(session["interval"]):
            with self.lock:
                self._expire(session)
                if session["status"] != "pending":
                    return
            try:
                result = request_json(DEVICE_BASE + "/token", {"device_auth_id": device_id, "user_code": user_code})
            except RemoteError as error:
                if error.status in (403, 404, 408, 429, 502, 503, 504):
                    continue
                with self.lock:
                    if session["status"] == "pending":
                        self._finish(session, "error", "Device login failed. Start a new login to retry.")
                return
            if not all(isinstance(result.get(k), str) and result[k] for k in ("authorization_code", "code_verifier")):
                with self.lock:
                    if session["status"] == "pending":
                        self._finish(session, "error", "Invalid device authorization response.")
                return
            with self.lock:
                self._expire(session)
                if session["status"] != "pending":
                    return
                session["status"] = "exchanging"
            self._exchange(session, result["authorization_code"], result["code_verifier"], DEVICE_REDIRECT)
            return
