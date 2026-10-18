"""Behavioral tests use synthetic tokens and isolated homes; no live credentials."""
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlparse

import accounts
import app
import login


def jwt(claims):
    return "header." + base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=") + ".signature"


def auth(user="user-1", account="account-1", access=None):
    return {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {
        "id_token": jwt({"sub": user, "email": user + "@example.test", "https://api.openai.com/auth": {
            "chatgpt_user_id": user, "chatgpt_account_id": account, "chatgpt_plan_type": "plus"}}),
        "access_token": access or jwt({"exp": time.time() + 3600}), "refresh_token": "refresh-" + user,
        "account_id": account}}


def quota(used=23, primary_seconds=18000):
    return {"plan_type": "plus", "rate_limit": {
        "primary_window": {"used_percent": used, "limit_window_seconds": primary_seconds, "reset_at": 1800000000},
        "secondary_window": {"used_percent": 40, "limit_window_seconds": 604800, "reset_at": 1800400000}},
        "rate_limit_reset_credits": {"available_count": 3}, "credits": {"balance": "5", "unlimited": False}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        environment=patch.dict(os.environ,{"CODEX_ACCOUNT_MANAGER_CONFIG":str(self.home/"manager"/"config.json")})
        environment.start();self.addCleanup(environment.stop)
        self.store = accounts.AccountStore(self.home, directory=Path(self.home)/"manager"/"data"/"vault")

    def test_import_switch_and_private_files_without_external_cli(self):
        keys = self.store.import_documents([auth(), auth("user-2")])
        self.assertFalse((self.home / "auth.json").exists())
        self.store.set_alias(keys[1], "Work")
        with self.store.switching("work"):
            pass
        views = self.store.list()["accounts"]
        self.assertFalse(views[0]["active"])
        self.assertTrue(views[1]["active"])
        self.assertEqual(self.store.directory.stat().st_mode & 0o777, 0o700)
        for path in (self.store.registry_path, self.store.snapshot(keys[0]), self.home / "auth.json"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("access_token", json.dumps(views))
        self.assertNotIn("refresh-user", json.dumps(views))

    def test_migrated_metadata_and_unknown_fields_survive(self):
        self.store.directory.mkdir(parents=True)
        doc = {"schema_version": 4, "accounts": [{"account_key": "user-1::account-1", "alias": "Old", "email": "user-1@example.test",
                "chatgpt_account_id": "account-1", "last_usage": {"secondary": {"used_percent": 33, "window_minutes": 10080, "resets_at": 1800000000}},
                "last_usage_at": 1700000000, "future_metadata": True}], "future_config": {"enabled": True}}
        self.store.registry_path.write_text(json.dumps(doc))
        accounts.private_write(self.store.snapshot("user-1::account-1"),accounts.json_bytes(auth()))
        result = self.store.list()["accounts"][0]
        self.assertEqual(result["usage"]["secondary"]["remaining_percent"], 67)
        with self.store.switching("Old"):
            pass
        self.assertTrue(self.store.read()["future_config"]["enabled"])
        self.assertTrue(self.store.read()["accounts"][0]["future_metadata"])

    def test_organization_identity_and_access_claim_fallback(self):
        document=auth()
        document["tokens"].pop("account_id")
        document["tokens"]["id_token"]=jwt({"email":"person@example.test","https://api.openai.com/auth":{"organizations":[{"id":"org-1"},{"id":"org-2","is_default":True}]}})
        document["tokens"]["access_token"]=jwt({"https://api.openai.com/auth":{"chatgpt_user_id":"organization-user"}})
        self.assertEqual(self.store.import_documents(document),["organization-user::org-2"])

    def test_legacy_api_key_identity_is_reused_and_snapshot_validated(self):
        document={"auth_mode":"apikey","OPENAI_API_KEY":"synthetic-api-key"}
        digest=accounts.hashlib.sha256(b"synthetic-api-key").hexdigest()
        legacy="apikey::old-user::"+digest
        self.store.directory.mkdir(parents=True)
        self.store.registry_path.write_text(json.dumps({"accounts":[{"account_key":legacy,"auth_mode":"apikey","alias":"API"}]}))
        accounts.private_write(self.store.snapshot(legacy),accounts.json_bytes(document))
        (self.home/"auth.json").write_text(json.dumps(document))
        self.assertEqual(len(self.store.list()["accounts"]),1)
        self.assertEqual(self.store.import_documents(document),[legacy])
        self.store.snapshot(legacy).write_text(json.dumps({"OPENAI_API_KEY":"wrong-key"}))
        with self.assertRaisesRegex(RuntimeError,"different account"):
            self.store._auth(self.store.find(legacy))

    def test_registered_active_auth_refresh_is_saved_before_switch(self):
        current = auth(access="fresh-from-codex")
        self.store.import_documents(auth(), "Owned")
        self.store.import_documents(auth("user-2"), "Other")
        (self.home / "auth.json").write_text(json.dumps(current))
        self.store.list()
        with self.store.switching("Other"):
            pass
        saved = json.loads(self.store.snapshot("user-1::account-1").read_text())
        self.assertEqual(saved["tokens"]["access_token"], "fresh-from-codex")
        self.assertEqual(self.store.read()["previous_active_account_key"], "user-1::account-1")

    def test_switch_rollback_restores_auth_registry_and_config(self):
        self.store.import_documents([auth(), auth("user-2")])
        with self.store.switching("1"):
            pass
        original_auth = (self.home / "auth.json").read_bytes()
        original_registry = self.store.registry_path.read_bytes()
        config = self.home / "config.toml"
        config.write_text('model = "original"\n')
        with patch("app.codex_home", return_value=self.home), patch("app.webui_config_path", return_value=self.home / "webui" / "config.json"), patch("app.restart_services", side_effect=RuntimeError("restart failed")):
            app.set_restart_after_switch(True)
            with self.assertRaisesRegex(app.ServiceCommandError, "rolled back"):
                app.do_switch("2")
        self.assertEqual((self.home / "auth.json").read_bytes(), original_auth)
        self.assertEqual(self.store.registry_path.read_bytes(), original_registry)
        self.assertEqual(config.read_text(), 'model = "original"\n')

    def test_switch_success_restarts_only_when_enabled(self):
        self.store.import_documents([auth(), auth("user-2")])
        with patch("app.codex_home",return_value=self.home), patch("app.webui_config_path",return_value=self.home/"webui"/"settings.json"), patch("app.restart_services",return_value={"message":"Restarted.","output":"running"}) as restart:
            app.set_restart_after_switch(False)
            app.do_switch("1")
            restart.assert_not_called()
            app.set_restart_after_switch(True)
            result=app.do_switch("2")
            restart.assert_called_once_with((app.CODEX_APP_SERVICE,))
            self.assertEqual(result["output"],"running")
            self.assertEqual(json.loads((self.home/"auth.json").read_text())["tokens"]["refresh_token"],"refresh-user-2")

    def test_failed_restart_sees_old_auth_on_rollback_restart(self):
        self.store.import_documents([auth(),auth("user-2")])
        with self.store.switching("1"):pass
        seen=[]
        def restart(services):
            seen.append(json.loads((self.home/"auth.json").read_text())["tokens"]["refresh_token"])
            if len(seen)==1:raise RuntimeError("failed")
            return {"message":"restored","output":"running"}
        with patch("app.codex_home",return_value=self.home),patch("app.webui_config_path",return_value=self.home/"settings.json"),patch("app.restart_services",side_effect=restart):
            app.set_restart_after_switch(True)
            with self.assertRaises(app.ServiceCommandError):app.do_switch("2")
        self.assertEqual(seen,["refresh-user-2","refresh-user-1"])

    def test_invalid_batch_is_atomic_and_alias_collisions_are_rejected(self):
        with self.assertRaises(RuntimeError):
            self.store.import_documents([auth(), {"tokens": {}}])
        self.assertFalse(self.store.registry_path.exists())
        self.assertEqual(list((self.store.directory/"credentials").glob("*.json")), [])
        self.store.import_documents(auth(), "work")
        with self.assertRaisesRegex(RuntimeError, "already used"):
            self.store.import_documents(auth("user-2"), "WORK")
        self.assertEqual(len(self.store.read()["accounts"]), 1)

    def test_multiple_workspaces_require_unambiguous_selector(self):
        self.store.import_documents([auth(), auth(account="account-2")])
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            self.store.find("user-1@example.test")
        self.assertEqual(self.store.find("2")["account_key"], "user-1::account-2")

    def test_missing_or_wrong_snapshot_does_not_switch(self):
        keys = self.store.import_documents([auth(), auth("user-2")])
        with self.store.switching("1"):
            pass
        before = (self.home / "auth.json").read_bytes()
        self.store.snapshot(keys[1]).write_text(json.dumps(auth("other")))
        with self.assertRaisesRegex(RuntimeError, "different account"):
            with self.store.switching("2"):
                pass
        self.assertEqual((self.home / "auth.json").read_bytes(), before)

    def test_remove_active_is_refused_and_other_snapshot_is_deleted(self):
        keys = self.store.import_documents([auth(), auth("user-2")])
        with self.store.switching("1"):
            pass
        with self.assertRaisesRegex(RuntimeError, "Switch"):
            self.store.remove("1")
        self.store.remove("2")
        self.assertFalse(self.store.snapshot(keys[1]).exists())
        self.assertEqual(len(self.store.list()["accounts"]), 1)

    def test_corrupt_registry_is_preserved(self):
        self.store.directory.mkdir(parents=True)
        self.store.registry_path.write_text("{broken")
        with self.assertRaisesRegex(RuntimeError, "preserved"):
            self.store.list()
        self.assertEqual(self.store.registry_path.read_text(), "{broken")

    @patch("accounts.request_json")
    def test_usage_windows_remaining_and_reset_credits(self, request):
        self.store.import_documents(auth())
        request.return_value = quota()
        result = self.store.list(True)["accounts"][0]
        self.assertEqual(result["usage"]["primary"]["remaining_percent"], 77)
        self.assertEqual(result["usage"]["primary"]["window_minutes"], 300)
        self.assertEqual(result["usage"]["reset_credits"], 3)
        self.assertEqual(request.call_args.kwargs["headers"]["ChatGPT-Account-Id"], "account-1")
        request.reset_mock()
        self.assertEqual(self.store.list()["accounts"][0]["usage"]["primary"]["used_percent"], 23)
        request.assert_not_called()

    @patch("accounts.request_json")
    def test_weekly_primary_is_not_reported_as_five_hours(self, request):
        self.store.import_documents(auth())
        response = quota(primary_seconds=604800)
        response["rate_limit"].pop("secondary_window")
        request.return_value = response
        result = self.store.list(True)["accounts"][0]
        self.assertIsNone(app.five_hour_usage(result))
        self.assertEqual(app.weekly_usage(result)["remaining_percent"], 77)

    @patch("accounts.request_json")
    def test_refresh_failure_keeps_cache_and_does_not_hide_other_accounts(self, request):
        self.store.import_documents([auth(), auth("user-2")])
        request.return_value = quota()
        self.store.list(True)
        request.side_effect = [accounts.RemoteError(403, "token_revoked"), quota(used=50)]
        views = self.store.list(True)["accounts"]
        self.assertEqual(views[0]["usage"]["primary"]["remaining_percent"], 77)
        self.assertEqual(views[0]["usage"]["refresh"]["error_code"], "token_revoked")
        self.assertEqual(views[1]["usage"]["primary"]["remaining_percent"], 50)

    @patch("accounts.request_json")
    def test_expired_token_rotation_is_persisted_without_switching(self, request):
        self.store.import_documents([auth(), auth("user-2", access=jwt({"exp": 1}))])
        with self.store.switching("1"):
            pass
        active = (self.home / "auth.json").read_bytes()
        request.side_effect = [{"access_token": "new-access", "refresh_token": "new-refresh"}, quota()]
        self.store.refresh_auth("2")
        saved = json.loads(self.store.snapshot("user-2::account-1").read_text())
        self.assertEqual(saved["tokens"]["refresh_token"], "new-refresh")
        self.assertEqual((self.home / "auth.json").read_bytes(), active)

    @patch("accounts.request_json")
    def test_usage_401_refreshes_active_auth_once(self, request):
        self.store.import_documents(auth())
        with self.store.switching("1"):
            pass
        request.side_effect = [accounts.RemoteError(401), {"access_token": "new-access", "refresh_token": "rotated"}, quota()]
        view = self.store.list(True)["accounts"][0]
        self.assertEqual(view["usage"]["refresh"]["status"], "ok")
        self.assertEqual(request.call_count, 3)
        active = json.loads((self.home / "auth.json").read_text())
        self.assertEqual(active["tokens"]["refresh_token"], "rotated")
        self.assertTrue(view["active"])

    @patch("accounts.request_json")
    def test_credit_only_response_and_malformed_usage_are_handled(self,request):
        self.store.import_documents(auth())
        request.return_value={"rate_limit_reset_credits":{"available_count":5}}
        result=self.store.list(True)["accounts"][0]
        self.assertEqual(result["usage"]["reset_credits"],5)
        request.return_value={"rate_limit":[],"rate_limit_reset_credits":"invalid"}
        result=self.store.list(True)["accounts"][0]
        self.assertEqual(result["usage"]["reset_credits"],5)
        self.assertEqual(result["usage"]["refresh"]["status"],"error")

    def test_concurrent_imports_do_not_lose_accounts(self):
        errors = []
        def work(i):
            try:
                accounts.AccountStore(self.home, directory=Path(self.home)/"manager"/"data"/"vault").import_documents(auth("parallel-" + str(i)))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.store.list()["accounts"]), 8)


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = accounts.AccountStore(self.temp.name, directory=Path(self.temp.name)/"manager"/"data"/"vault")
        self.manager = login.LoginManager(lambda: self.store)
        self.addCleanup(self.manager.close)

    def callback(self, session, **overrides):
        query = parse_qs(urlparse(session["url"]).query)
        values = {"state": query["state"][0], "code": "synthetic-code"}
        values.update(overrides)
        return login.REDIRECT_URI + "?" + urlencode(values)

    def token_result(self):
        return {k: v for k, v in auth()["tokens"].items() if k != "account_id"}

    @patch("login.request_json")
    def test_pkce_callback_stores_account_and_never_activates_it(self, request):
        session = self.manager.start("manual", "work")
        query = parse_qs(urlparse(session["url"]).query)
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertNotIn("verifier", session)
        request.return_value = self.token_result()
        result = self.manager.complete(session["id"], self.callback(session))
        self.assertEqual(result["status"], "success")
        self.assertFalse((self.store.home / "auth.json").exists())
        self.assertEqual(self.store.list()["accounts"][0]["alias"], "work")
        verifier = request.call_args.args[1]["code_verifier"]
        challenge = base64.urlsafe_b64encode(__import__('hashlib').sha256(verifier.encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertNotIn("verifier", self.manager.sessions[session["id"]])
        self.assertNotIn("access_token", json.dumps(result))
        with self.assertRaises(RuntimeError):self.manager.complete(session["id"], self.callback(session))

    @patch("login.request_json")
    def test_wrong_state_or_origin_rejected_without_exchanging(self, request):
        session = self.manager.start("manual")
        for callback in (self.callback(session, state="wrong"), self.callback(session).replace("localhost", "evil.test"), "code-only"):
            with self.assertRaises(RuntimeError):self.manager.complete(session["id"], callback)
        self.assertEqual(self.manager.status(session["id"])["status"], "pending")
        request.assert_not_called()

    @patch("login.request_json")
    def test_cancelled_and_expired_sessions_cannot_save_credentials(self, request):
        session = self.manager.start("manual")
        self.manager.cancel(session["id"])
        with self.assertRaises(RuntimeError):self.manager.complete(session["id"], self.callback(session))
        other = self.manager.start("manual")
        self.manager.sessions[other["id"]]["expires_at"] = 0
        self.assertEqual(self.manager.status(other["id"])["status"], "expired")
        request.assert_not_called()
        self.assertFalse(self.store.registry_path.exists())

    @patch("login.request_json")
    def test_cancellation_during_token_exchange_prevents_import(self, request):
        session = self.manager.start("manual")
        def exchange(*args, **kwargs):
            self.manager.cancel(session["id"])
            return self.token_result()
        request.side_effect = exchange
        result = self.manager.complete(session["id"], self.callback(session))
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.store.registry_path.exists())

    @patch("login.request_json")
    def test_authorization_error_and_incomplete_tokens_are_not_saved(self, request):
        session = self.manager.start("manual")
        result = self.manager.complete(session["id"], self.callback(session, error="access_denied"))
        self.assertEqual(result["status"], "error")
        request.assert_not_called()
        session = self.manager.start("manual")
        request.return_value = {"access_token": "secret"}
        result = self.manager.complete(session["id"], self.callback(session))
        self.assertEqual(result["status"], "error")
        self.assertNotIn("secret", json.dumps(result))
        self.assertFalse(self.store.registry_path.exists())

    @patch("login.ThreadingHTTPServer", side_effect=OSError("busy"))
    def test_busy_browser_port_offers_manual_fallback(self, _server):
        session = self.manager.start("browser")
        self.assertIn("1455", session["warning"])
        with self.assertRaisesRegex(RuntimeError, "already pending"):
            self.manager.start("manual")

    @patch("login.request_json")
    def test_device_authorization_poll_and_exchange(self, request):
        # Run worker explicitly with a fake clock-free stop signal.
        class Immediate:
            def wait(self, timeout):return False
            def set(self):pass
        with patch("login.threading.Thread.start"), patch("login.threading.Timer.start"):
            request.return_value = {"device_auth_id": "device-id", "user_code": "ABCD-EFGH", "interval": "1"}
            session = self.manager.start("device", "device")
        internal = self.manager.sessions[session["id"]]
        internal["stop"] = Immediate()
        request.side_effect = [accounts.RemoteError(403), {"authorization_code": "code", "code_verifier": "verifier"}, self.token_result()]
        self.manager._device_worker(internal)
        self.assertEqual(self.manager.status(session["id"])["status"], "success")
        self.assertEqual(request.call_args.args[1]["redirect_uri"], login.DEVICE_REDIRECT)
        self.assertFalse((self.store.home / "auth.json").exists())
        self.assertNotIn("device_auth_id", session)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        from http.server import ThreadingHTTPServer
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=accounts.AccountStore(self.temp.name,Path(self.temp.name)/"manager-vault")
        self.manager=login.LoginManager(lambda:self.store)
        self.addCleanup(self.manager.close)
        self.manager_patch=patch("app.LOGIN_MANAGER",self.manager)
        self.manager_patch.start()
        self.addCleanup(self.manager_patch.stop)
        self.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def close_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,body=None,headers=None):
        import urllib.request
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
                                       headers={"Content-Type":"application/json",**(headers or {})})
        with urllib.request.urlopen(request,timeout=5) as response:return json.load(response)

    def test_health_reports_native_backend_without_codex_auth(self):
        with patch("app.shutil.which",return_value=None):
            self.assertEqual(self.request("/api/health")["account_backend"],"native")

    def test_account_response_reports_manager_version_without_external_cli_output(self):
        self.store.import_documents(auth(),"work")
        with patch("app.shutil.which",return_value=None),patch("app.account_store",return_value=self.store),patch("app.webui_config_path",return_value=Path(self.temp.name)/"settings.json"):
            result=self.request("/api/accounts?refresh=0")
        self.assertEqual(result["account_manager_version"],"Native account manager "+app.APP_VERSION)
        self.assertEqual(result["accounts"][0]["alias"],"work")
        self.assertNotIn("codex_auth_version",result)
        self.assertNotIn("raw",result)

    @patch("login.request_json")
    def test_web_login_routes_save_and_status_without_returning_tokens(self,request):
        request.return_value={k:v for k,v in auth()["tokens"].items() if k!="account_id"}
        session=self.request("/api/login/start",{"method":"manual","alias":"web"})["login"]
        state=parse_qs(urlparse(session["url"]).query)["state"][0]
        result=self.request("/api/login/complete",{"id":session["id"],"callback":login.REDIRECT_URI+"?"+urlencode({"state":state,"code":"code"})})
        self.assertEqual(result["login"]["status"],"success")
        self.assertNotIn("refresh_token",json.dumps(result))
        self.assertEqual(self.request("/api/login/status?id="+session["id"])["login"]["status"],"success")
        self.assertEqual(self.store.list()["accounts"][0]["alias"],"web")

    def test_cross_origin_and_non_object_bodies_rejected(self):
        import urllib.error
        for body,headers in (([],{}),({"method":"manual"},{"Origin":"https://untrusted.test"})):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.request("/api/login/start",body,headers)
            self.assertEqual(context.exception.code,400)
            context.exception.close()
        self.assertEqual(self.manager.sessions,{})

    @patch("login.request_json")
    def test_browser_callback_listener_completes_login(self,request):
        import urllib.request
        from http.server import ThreadingHTTPServer
        request.return_value={k:v for k,v in auth()["tokens"].items() if k!="account_id"}
        with patch("login.ThreadingHTTPServer",side_effect=lambda address,handler:ThreadingHTTPServer(("127.0.0.1",0),handler)):
            session=self.manager.start("browser")
        callback_server=self.manager.sessions[session["id"]]["server"]
        state=parse_qs(urlparse(session["url"]).query)["state"][0]
        url=f"http://127.0.0.1:{callback_server.server_port}/auth/callback?"+urlencode({"state":state,"code":"code"})
        with urllib.request.urlopen(url,timeout=5) as response:
            self.assertIn(b"Account saved",response.read())
        self.assertEqual(self.manager.status(session["id"])["status"],"success")


class CLITests(unittest.TestCase):
    def test_cli_import_list_alias_switch_and_remove_share_native_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            source.write_text(json.dumps([auth(), auth("user-2")]))
            env = {**os.environ, "CODEX_HOME": str(root / "codex"), "CODEX_ACCOUNT_MANAGER_CONFIG": str(root / "webui.json")}
            # Disable service restart without interacting with systemd.
            config_override = root / "settings.json"
            config_override.write_text('{"restart_after_switch":false}')
            env["CODEX_ACCOUNT_MANAGER_CONFIG"] = str(config_override)
            def run(*args):
                cp = subprocess.run([sys.executable, str(Path(app.__file__).resolve()), *args, "--json"], env=env,
                                    text=True, capture_output=True, timeout=15)
                self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
                return json.loads(cp.stdout)
            run("import", str(source))
            run("alias", "2", "work")
            views = run("list")["accounts"]
            self.assertEqual(views[1]["alias"], "work")
            run("switch", "work")
            self.assertTrue(run("list")["accounts"][1]["active"])
            run("remove", "1")
            self.assertEqual(len(run("list")["accounts"]), 1)
