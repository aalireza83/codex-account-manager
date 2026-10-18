import unittest
from unittest.mock import call, patch
import subprocess
import json
import io
import tempfile
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from html.parser import HTMLParser
import re

import app

APP_JS = (app.WEB_ROOT / "assets/app.js").read_text()


class ParsingTests(unittest.TestCase):
    def test_pre_tailadmin_controls_and_handlers_are_retained(self):
        class Controls(HTMLParser):
            def __init__(self):super().__init__();self.ids=[]
            def handle_starttag(self,tag,attrs):
                value=dict(attrs).get("id")
                if value:self.ids.append(value)
        controls=Controls();controls.feed(app.HTML)
        baseline=json.loads((app.WEB_ROOT.parent/"tests/webui_contract.json").read_text())
        self.assertFalse(set(baseline["ids"])-set(controls.ids))
        self.assertEqual(len(controls.ids),len(set(controls.ids)),"Duplicate DOM IDs")
        for handler in baseline["handlers"]:
            self.assertRegex(APP_JS,r"(?:async )?function "+handler+r"\(")
        for element_id in re.findall(r"getElementById\(['\"]([^'\"]+)['\"]\)",APP_JS):
            self.assertIn(element_id,controls.ids)

    def test_decorative_copy_is_removed(self):
        for text in ("Self-hosted","YOUR CODEX WORKSPACE","Manage your accounts, usage limits and active configuration.",
                     "Account workspace","Stored in this workspace","Configured providers","Native accounts · Local storage"):
            self.assertNotIn(text,app.HTML+APP_JS)

    def test_human_cli_list_retains_usage_display(self):
        result={"accounts":[{"account_key":"synthetic","number":1,"alias":"work","active":False,
                "usage":{"primary":{"window_minutes":300,"used_percent":23},
                         "secondary":{"window_minutes":10080,"remaining_percent":60}}}]}
        output=io.StringIO()
        with patch("sys.stdout",output):app.cli_output(result)
        self.assertIn("5h: 77.0% remaining",output.getvalue())
        self.assertIn("Weekly: 60.0% remaining",output.getvalue())

    def test_import_accounts_tab_supports_standard_and_cpa(self):
        self.assertIn('id="dialogImportTab"',app.HTML)
        self.assertIn('id="view-import"',app.HTML)
        self.assertIn('id="authImportFormat"',app.HTML)
        self.assertIn('id="authImportText"',app.HTML)
        self.assertIn("/api/import-accounts",APP_JS)
        self.assertIn("Last ping:",APP_JS)

    @patch("app.account_store")
    @patch("app.run_cmd")
    def test_standard_import_uses_native_store(self,run_cmd,store):
        store.return_value.import_documents.return_value=["account-key"]
        result=app.import_accounts_text('{"tokens":{"id_token":"jwt","access_token":"access"}}',"auto","work")
        document,alias=store.return_value.import_documents.call_args.args
        self.assertEqual(alias,"work")
        self.assertEqual(document["tokens"]["access_token"],"access")
        self.assertIn("Imported 1",result["message"])
        run_cmd.assert_not_called()

    @patch("app.account_store")
    def test_cpa_auth_text_is_converted_to_standard(self,store):
        store.return_value.import_documents.return_value=["account-key"]
        app.import_accounts_text('{"id_token":"jwt","access_token":"access"}',"auto","")
        document=store.return_value.import_documents.call_args.args[0]
        self.assertEqual(document,{"auth_mode":"chatgpt","OPENAI_API_KEY":None,
                                   "tokens":{"id_token":"jwt","access_token":"access"}})

    def test_auth_normalizer_accepts_camel_case_nested_fields_and_arrays(self):
        source=[{"credentials":{"idToken":"id-1","accessToken":"access-1","refreshToken":"refresh-1",
                                "accountId":"account-1"},"lastRefresh":"2026-09-26T00:00:00Z"},
                {"openaiApiKey":"sk-test"}]
        normalized=app.normalize_auth_document(source)
        self.assertEqual(normalized[0]["tokens"],{"id_token":"id-1","access_token":"access-1",
                                                   "refresh_token":"refresh-1","account_id":"account-1"})
        self.assertEqual(normalized[0]["last_refresh"],"2026-09-26T00:00:00Z")
        self.assertEqual(normalized[1],{"auth_mode":"apikey","OPENAI_API_KEY":"sk-test"})

    def test_import_rejects_invalid_json_and_alias_for_multiple_accounts(self):
        with self.assertRaisesRegex(RuntimeError,"Invalid JSON"):
            app.import_accounts_text("{broken","auto","")
        with self.assertRaisesRegex(RuntimeError,"single imported account"):
            app.import_accounts_text("[{},{}]","standard","shared")
        with self.assertRaises(RuntimeError):
            app.import_accounts_text("[{}]","cpa","")

    def test_backup_ui_and_api_are_removed(self):
        self.assertNotIn('data-view="backups"',app.HTML)
        self.assertNotIn('id="view-backups"',app.HTML)
        self.assertNotIn('/api/backups',app.HTML)
        for removed in ("create_config_backup","list_backups","restore_config_backup","ensure_original_backup"):
            self.assertFalse(hasattr(app,removed))

    def test_api_base_url_is_fixed_and_usage_url_is_same_host(self):
        valid={"name":"API","base_url":app.API_BASE_URL,"api_key":"sk-test","usage_url":app.DEFAULT_USAGE_URL}
        self.assertEqual(app.validated_api_profile_fields(valid)["usage_url"],app.DEFAULT_USAGE_URL)
        with self.assertRaisesRegex(RuntimeError,"Base URL must be"):
            app.validated_api_profile_fields({**valid,"base_url":"https://other.test/v1"})
        with self.assertRaisesRegex(RuntimeError,"Usage URL must be"):
            app.validated_api_profile_fields({**valid,"usage_url":"https://other.test/v1/usage"})

    @patch("app.build_opener")
    def test_usage_fetch_uses_saved_key_without_returning_it(self,build_opener):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir()
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":app.API_BASE_URL,"api_key":"sk-test","usage_url":app.DEFAULT_USAGE_URL})
                opener=build_opener.return_value
                opener.open.return_value=io.BytesIO(b'{"quota":{"remaining":42},"usage":{"today":{"requests":3}}}')
                result=app.fetch_api_profile_usage(profile["id"])
                request=opener.open.call_args.args[0]
                self.assertEqual(request.full_url,app.DEFAULT_USAGE_URL)
                self.assertEqual(request.get_header("Authorization"),"Bearer sk-test")
                self.assertEqual(request.get_header("User-agent"),f"CodexAuthWebUI/{app.APP_VERSION}")
                self.assertEqual(result["quota"]["remaining"],42)
                self.assertNotIn("sk-test",str(result))

    def test_api_form_layout_and_hidden_settings(self):
        form=app.HTML.split('<section id="view-add-api"',1)[1].split('</section>',1)[0]
        self.assertLess(form.index('API account details'),form.index('Paste config.toml'))
        self.assertLess(form.index('id="profileModel"'),form.index('id="profileReasoning"'))
        self.assertNotIn('id="profileApproval"',form)
        self.assertNotIn('id="profileSandbox"',form)
        self.assertNotIn('id="profileCatalog"',form)
        self.assertIn('...profileHiddenFields,http_headers:headers',APP_JS)
        self.assertIn('<select id="profileBaseUrl"><option value="https://ttmapi.site/v1">',form)
        self.assertIn('id="profileUsageUrl"',form)
        self.assertIn('loadApiUsage(',APP_JS)

    def test_api_usage_includes_dated_daily_rows_and_collapsed_raw_output(self):
        renderer=APP_JS.split('function renderApiUsage(data){',1)[1].split('async function loadApiUsage',1)[0]
        self.assertIn('data.daily_usage',renderer)
        self.assertIn('Recent daily usage (dates as reported by provider)',renderer)
        self.assertIn('Today requests (provider)',renderer)
        self.assertIn('<details class="api-usage-raw"><summary>Raw output</summary>',renderer)
        self.assertNotIn('<details class="api-usage-raw" open',renderer)

    def test_profile_get_routes_are_reachable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir()
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":"https://ttmapi.site/v1","api_key":"sk-test"})
                server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
                server.quiet=True
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                try:
                    base=f"http://127.0.0.1:{server.server_port}"
                    with urllib.request.urlopen(base+"/api/profiles") as response:
                        profiles=json.load(response)
                    with urllib.request.urlopen(base+"/api/profiles/detail?profile_id="+profile["id"]) as response:
                        details=json.load(response)
                finally:
                    server.shutdown();server.server_close();thread.join()
                self.assertTrue(profiles["ok"])
                self.assertNotIn("backups",profiles)
                self.assertNotIn("original_backup_id",profiles)
                self.assertEqual(details["profile"]["id"],profile["id"])

    def test_removed_backup_endpoint_returns_404(self):
        server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler);server.quiet=True
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            request=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/backups",
                data=b'{}',headers={"Content-Type":"application/json"},method="POST")
            with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(request)
            self.assertEqual(caught.exception.code,404)
            caught.exception.close()
        finally:
            server.shutdown();server.server_close();thread.join()

    @patch("app.run_cmd")
    def test_restart_services_runs_app_commands_in_order(self, run_cmd):
        run_cmd.side_effect = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "app active", ""),
        ]

        with patch("app.read_webui_config",return_value={}):
            result = app.restart_services()

        self.assertEqual(
            run_cmd.call_args_list,
            [
                unittest.mock.call(["systemctl", "restart", "codex-app-server"], 60),
                unittest.mock.call(["codex", "app-server", "daemon", "restart"], 60),
            ],
        )
        self.assertEqual(result["message"], "Restarted codex-app-server.")
        self.assertIn("$ systemctl restart codex-app-server",result["output"])
        self.assertIn("app active",result["output"])
        self.assertIn("$ codex app-server daemon restart",result["output"])

    @patch("app.webui_config_path")
    def test_restart_after_switch_setting_is_persistent(self, config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            path=Path(temp_dir)/"config.json"
            config_path.return_value=path

            self.assertFalse(app.restart_after_switch_enabled())
            app.set_restart_after_switch(False)
            self.assertFalse(app.restart_after_switch_enabled())
            self.assertEqual(json.loads(path.read_text())["restart_after_switch"],False)

    @patch("app.apply_profile")
    @patch("app.restart_after_switch_enabled", return_value=False)
    def test_api_account_switch_skips_restart_when_disabled(self, _restart_enabled, apply_profile):
        apply_profile.return_value={"message":"staged","output":""}

        result=app.apply_account_profile("api-profile")

        apply_profile.assert_called_once_with("api-profile",False)
        self.assertEqual(result["message"],"staged")

    @patch("app.apply_profile")
    @patch("app.restart_after_switch_enabled", return_value=True)
    def test_api_account_switch_restarts_when_enabled(self, _restart_enabled, apply_profile):
        apply_profile.return_value={"message":"restarted","output":"status"}

        app.apply_account_profile("api-profile")

        apply_profile.assert_called_once_with("api-profile",True)

    @patch("app.find_account_record")
    @patch("app.webui_config_path")
    def test_account_auto_ping_respects_global_master_switch(self, config_path, find_record):
        with tempfile.TemporaryDirectory() as temp_dir:
            path=Path(temp_dir)/"config.json";config_path.return_value=path
            find_record.return_value={"account_key":"account-key","alias":"work","email":"work@example.com"}
            app.set_account_auto_ping("work",True)
            account={"account_key":"account-key","alias":"work","usage":{"5h":"100% (12:00)"}}
            snapshot=app.decorate_accounts_for_auto_ping([account])
            self.assertFalse(snapshot["enabled"])
            self.assertTrue(account["auto_ping_configured"])
            self.assertFalse(account["auto_ping_effective"])
            app.set_auto_ping(True)
            app.decorate_accounts_for_auto_ping([account])
            self.assertTrue(account["auto_ping_effective"])
            config=app.read_webui_config();config["auto_ping_state"]={"work":{"next_reset_at":"2026-09-18T12:00:00+00:00"}};app.write_webui_config(config)
            app.set_auto_ping(False)
            self.assertEqual(app.read_webui_config()["auto_ping_state"],{})

    @patch("app.webui_config_path")
    def test_accounts_include_last_auto_ping_time_for_ui(self,config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({"auto_ping_enabled":True,"auto_ping_accounts":{"stable-key":True},
                                    "auto_ping_state":{"stable-key":{"last_ping_at":"2026-09-22T12:00:00+00:00"}}})
            account={"account_key":"stable-key"}
            app.decorate_accounts_for_auto_ping([account])
            self.assertEqual(account["auto_ping_last_ping_at"],"2026-09-22T12:00:00+00:00")
            self.assertIn("Last ping:",APP_JS)
            self.assertIn("Last ping: Never",APP_JS)

    @patch("app.account_store")
    @patch("app.find_account_record")
    @patch("app.webui_config_path")
    def test_account_alias_set_and_clear_preserve_auto_ping_settings(self, config_path, find_record, store):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            find_record.return_value={"account_key":"account-key","alias":"work","email":"work@example.com"}
            app.write_webui_config({
                "auto_ping_accounts":{"account-key":True},
                "auto_ping_state":{"account-key":{"last_result":"ok"}},
            })
            store.return_value.set_alias.return_value="Alias updated"

            app.set_account_alias("account-key","work","account-key")
            config=app.read_webui_config()
            self.assertTrue(config["auto_ping_accounts"]["account-key"])
            store.return_value.set_alias.assert_called_with("account-key","work")

            app.set_account_alias("account-key",None,"account-key")
            config=app.read_webui_config()
            self.assertTrue(config["auto_ping_accounts"]["account-key"])
            store.return_value.set_alias.assert_called_with("account-key",None)

    def test_reset_time_parses_utc_text_and_epoch(self):
        now=datetime(2026,9,18,8,0,tzinfo=timezone.utc)
        self.assertEqual(app.parse_reset_time("100% (09:30)",now),datetime(2026,9,18,9,30,tzinfo=timezone.utc))
        self.assertEqual(app.parse_reset_time({"resets_at":now.timestamp()},now),now)

    @patch("app.run_account_ping")
    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_auto_ping_worker_confirms_cached_candidate_then_runs_once(self, config_path, list_accounts, run_ping):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({
                "auto_ping_enabled":True,"auto_ping_accounts":{"work":True},
                "auto_ping_state":{},
            })
            now=datetime(2026,9,18,8,1,tzinfo=timezone.utc);updated=now.timestamp()
            account={"alias":"work","usage":{"updated_at":updated,
                "primary":{"window_minutes":300,"used_percent":0,"resets_at":updated+300*60-3},
                "secondary":{"window_minutes":10080,"used_percent":25,"resets_at":updated+10080*60-3}}}
            list_accounts.return_value={"accounts":[account]}
            run_ping.return_value="Ping passed"
            first=app.auto_ping_worker_once(now)
            second=app.auto_ping_worker_once(now)
            self.assertEqual(first["pinged"],["work"])
            self.assertEqual(second["pinged"],[])
            run_ping.assert_called_once_with("work")
            self.assertEqual(list_accounts.call_args_list,[call(False),call(True),call(False)])
            state=app.read_webui_config()["auto_ping_state"]["work"]
            self.assertEqual(state["last_result"],"ok")
            self.assertEqual(state["last_ping_at"],"2026-09-18T08:01:00+00:00")

    @patch("app.run_account_ping")
    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_auto_ping_skips_without_exact_json_conditions(self,config_path,list_accounts,run_ping):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            now=datetime(2026,9,18,8,1,tzinfo=timezone.utc);updated=now.timestamp()
            for five_used,weekly_used,difference in ((1,25,-3),(0,100,-3),(0,25,-6),(0,25,1)):
                app.write_webui_config({"auto_ping_enabled":True,"auto_ping_accounts":{"work":True},
                    "auto_ping_state":{}})
                list_accounts.return_value={"accounts":[{"alias":"work","usage":{"updated_at":updated,
                    "primary":{"window_minutes":300,"used_percent":five_used,"resets_at":updated+300*60+difference},
                    "secondary":{"window_minutes":10080,"used_percent":weekly_used,"resets_at":updated+10080*60}}}]}
                self.assertEqual(app.auto_ping_worker_once(now)["pinged"],[])
            run_ping.assert_not_called()
            self.assertEqual(list_accounts.call_count,4)

    def test_json_usage_windows_are_selected_by_duration(self):
        account={"usage":{
            "primary":{"window_minutes":10080,"used_percent":25,"resets_at":123},
            "secondary":{"window_minutes":300,"used_percent":80,"resets_at":456},
        }}
        self.assertEqual(app.five_hour_usage(account)["resets_at"],456)
        self.assertEqual(app.weekly_usage(account)["resets_at"],123)
        weekly_only={"usage":{"primary":{"window_minutes":10080,"used_percent":25,"resets_at":123}}}
        self.assertIsNone(app.five_hour_usage(weekly_only))

    def test_auto_ping_json_timing_tolerance_is_exactly_five_seconds(self):
        updated=1_790_000_000
        def account(difference):
            return {"usage":{"updated_at":updated,
                "primary":{"window_minutes":300,"used_percent":0,"resets_at":updated+300*60+difference},
                "secondary":{"window_minutes":10080,"used_percent":25,"resets_at":updated+10080*60}}}
        self.assertTrue(app.auto_ping_json_eligible(account(0)))
        self.assertTrue(app.auto_ping_json_eligible(account(-5)))
        self.assertFalse(app.auto_ping_json_eligible(account(-5.001)))
        self.assertFalse(app.auto_ping_json_eligible(account(0.001)))

    @patch("app.run_account_ping")
    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_weekly_only_account_clears_stale_five_hour_schedule(self,config_path,list_accounts,run_ping):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({
                "auto_ping_enabled":True,"auto_ping_accounts":{"stable-key":True},
                "auto_ping_state":{"stable-key":{"next_reset_at":"2026-09-18T08:00:00+00:00"}},
            })
            list_accounts.return_value={"accounts":[{
                "account_key":"stable-key",
                "usage":{"primary":{"window_minutes":10080,"used_percent":25,"resets_at":123}},
            }]}
            result=app.auto_ping_worker_once(datetime(2026,9,18,8,1,tzinfo=timezone.utc))
            self.assertEqual(result,{"pinged":[],"candidates":[],"refreshed":True})
            self.assertIsNone(app.read_webui_config()["auto_ping_state"]["stable-key"]["next_reset_at"])
            self.assertEqual(list_accounts.call_args_list,[call(False),call(True)])
            run_ping.assert_not_called()

    @patch("app.run_account_ping")
    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_passed_five_hour_reset_triggers_one_live_refresh(self,config_path,list_accounts,run_ping):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({"auto_ping_enabled":True,"auto_ping_accounts":{"stable-key":True}})
            now=datetime(2026,9,22,12,0,tzinfo=timezone.utc)
            account={"account_key":"stable-key","usage":{"updated_at":now.timestamp()-60,
                "primary":{"window_minutes":300,"used_percent":50,"resets_at":now.timestamp()-1},
                "secondary":{"window_minutes":10080,"used_percent":25,"resets_at":now.timestamp()+3600}}}
            list_accounts.return_value={"accounts":[account]}
            result=app.auto_ping_worker_once(now)
            self.assertTrue(result["refreshed"])
            self.assertEqual(result["pinged"],[])
            self.assertEqual(list_accounts.call_args_list,[call(False),call(True)])
            run_ping.assert_not_called()

    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_scheduler_does_not_poll_when_auto_ping_is_off(self,config_path,list_accounts):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({"auto_ping_enabled":False,"auto_ping_accounts":{"stable-key":True}})
            result=app.auto_ping_worker_once(datetime(2026,9,22,12,0,tzinfo=timezone.utc))
            self.assertEqual(result,{"pinged":[],"candidates":[],"refreshed":False})
            list_accounts.assert_not_called()

    @patch("app.webui_config_path")
    def test_auto_ping_migrates_legacy_alias_and_email_keys(self,config_path):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({
                "auto_ping_accounts":{"work@example.com":True},
                "auto_ping_state":{"work@example.com":{"last_result":"ok"}},
            })
            account={"account_key":"stable-key","alias":"work","email":"work@example.com"}
            self.assertTrue(app.migrate_auto_ping_account_keys([account]))
            config=app.read_webui_config()
            self.assertEqual(config["auto_ping_accounts"],{"stable-key":True})
            self.assertEqual(config["auto_ping_state"]["stable-key"]["last_result"],"ok")

    @patch("app.run_account_ping")
    @patch("app.list_accounts")
    @patch("app.webui_config_path")
    def test_auto_ping_retries_transient_failure(self,config_path,list_accounts,run_ping):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path.return_value=Path(temp_dir)/"config.json"
            app.write_webui_config({
                "auto_ping_enabled":True,"auto_ping_accounts":{"stable-key":True},
                "auto_ping_state":{},
            })
            updated=datetime(2026,9,18,8,1,tzinfo=timezone.utc).timestamp()
            list_accounts.return_value={"accounts":[{
                "account_key":"stable-key","alias":"work",
                "usage":{"updated_at":updated,
                         "primary":{"window_minutes":300,"used_percent":0,"resets_at":updated+300*60-3},
                         "secondary":{"window_minutes":10080,"used_percent":25,"resets_at":updated+10080*60-3}},
            }]}
            run_ping.side_effect=[RuntimeError("temporary failure"),"Ping passed"]
            first=app.auto_ping_worker_once(datetime(2026,9,18,8,1,tzinfo=timezone.utc))
            early=app.auto_ping_worker_once(datetime(2026,9,18,8,4,tzinfo=timezone.utc))
            retried=app.auto_ping_worker_once(datetime(2026,9,18,8,6,tzinfo=timezone.utc))
            self.assertEqual(first["pinged"],[])
            self.assertEqual(early["pinged"],[])
            self.assertEqual(retried["pinged"],["stable-key"])
            self.assertEqual(run_ping.call_count,2)
            state=app.read_webui_config()["auto_ping_state"]["stable-key"]
            self.assertEqual(state["last_result"],"ok")
            self.assertEqual(state["attempt_count"],0)
            self.assertIsNone(state["next_retry_at"])



    @patch("app.shutil.which", return_value="/usr/bin/codex")
    @patch("app.account_store")
    @patch("app.subprocess.run")
    def test_account_ping_uses_temporary_codex_home(self, run, store, _which):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot=Path(temp_dir)/"account.auth.json"
            snapshot.write_text('{"tokens":{"access_token":"test-only"}}')
            store.return_value.ping_credentials.return_value.__enter__.return_value=({"tokens":{"access_token":"test-only"}},"work",lambda updated:None)
            observed={}

            def fake_run(args,**kwargs):
                isolated_home=Path(kwargs["env"]["CODEX_HOME"])
                observed["home"]=isolated_home
                observed["auth"]=json.loads((isolated_home/"auth.json").read_text())
                observed["args"]=args
                return subprocess.CompletedProcess(args,0,"OK\n","")

            run.side_effect=fake_run
            message=app._run_account_ping("work")

        self.assertEqual(observed["auth"]["tokens"]["access_token"],"test-only")
        self.assertFalse(observed["home"].exists())
        self.assertIn("--ephemeral",observed["args"])
        self.assertIn("read-only",observed["args"])
        self.assertIn("active account was not changed",message)

    def test_api_profile_keeps_secret_out_of_toml_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir()
            config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path):
                profile=app.create_api_profile({
                    "name":"Provider One","base_url":"https://ttmapi.site/v1","model":"gpt-test",
                    "api_key":"sk-super-secret","reasoning_effort":"high","approval_policy":"on-request",
                    "sandbox_mode":"workspace-write","http_headers":{"x-test":"yes"},
                })
                path=app.profile_path(profile["id"])
                config=(path/"config.toml").read_text()
                manifest=(path/"manifest.json").read_text()
                secret=(path/"secret.env").read_text()
                profiles_mode=app.profiles_dir().stat().st_mode & 0o777

            self.assertIn('[model_providers.',config)
            self.assertIn('.auth]',config)
            self.assertIn('command = "/usr/bin/cat"',config)
            self.assertNotIn("sk-super-secret",config+manifest)
            self.assertIn("sk-super-secret",secret)
            self.assertEqual((path/"token").read_text().strip(),"sk-super-secret")
            self.assertEqual((path/"token").stat().st_mode & 0o777,0o600)
            self.assertEqual((path/"secret.env").stat().st_mode & 0o777,0o600)
            self.assertEqual(profiles_mode,0o700)

    def test_optional_api_fields_are_omitted_from_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir()
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({
                    "name":"Minimal API","base_url":"https://ttmapi.site/v1","api_key":"sk-test",
                    "model":"  ","reasoning_effort":"","approval_policy":"","sandbox_mode":"",
                    "model_catalog_json":"","http_headers":{},
                })
                config=(app.profile_path(profile["id"])/"config.toml").read_text()
            self.assertRegex(config,r'model_provider = "webui_minimal_api_[a-f0-9]{8}"')
            for key in ("model =", "model_reasoning_effort =", "approval_policy =", "sandbox_mode =", "model_catalog_json =", "http_headers"):
                self.assertNotIn(key,config)

    def test_api_profile_can_be_edited_without_changing_id_or_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();(home/"config.toml").write_text('model = "default"\n')
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"Before","base_url":"https://ttmapi.site/v1","model":"old-model","api_key":"sk-original","reasoning_effort":"high"})
                profile_id=profile["id"];provider_id=app.read_profile_manifest(profile_id)["provider_id"]
                result=app.update_api_profile(profile_id,{"name":"After","base_url":"https://ttmapi.site/v1","model":"new-model","api_key":"","reasoning_effort":"","http_headers":{}})
                details=app.profile_details(profile_id)
                token=(app.profile_path(profile_id)/"token").read_text().strip()
                config=(home/"config.toml").read_text()
                updated_provider_id=app.read_profile_manifest(profile_id)["provider_id"]
            self.assertEqual(result["profile"]["id"],profile_id)
            self.assertEqual(details["name"],"After")
            self.assertEqual(details["reasoning_effort"],"")
            self.assertEqual(token,"sk-original")
            self.assertEqual(updated_provider_id,provider_id)
            self.assertIn('base_url = "https://ttmapi.site/v1"',config)
            self.assertNotIn("sk-original",str(details)+config)

    def test_editing_active_api_profile_updates_overlay_without_restart_when_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            config_file.write_text('model = "default"\nmodel_reasoning_effort = "medium"\n')
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":"https://ttmapi.site/v1","model":"old-model","api_key":"sk-test","reasoning_effort":"high"})
                app.set_restart_after_switch(False)
                app.apply_profile(profile["id"],restart=False)
                app.update_api_profile(profile["id"],{"name":"API","base_url":"https://ttmapi.site/v1","model":"new-model","api_key":"","reasoning_effort":"","http_headers":{}})
                active=config_file.read_text()
                app.apply_profile("default",restart=False)
                restored=config_file.read_text()
            self.assertIn('model = "new-model"',active)
            self.assertIn('model_reasoning_effort = "medium"',active)
            self.assertIn('model = "default"',restored)


    def test_settings_use_switches_instead_of_enable_disable_buttons(self):
        settings=app.HTML.split('<section id="view-settings"',1)[1].split('</section>',1)[0]
        for toggle in ("restartAfterSwitchToggle","autoPingToggle"):
            self.assertIn(f'id="{toggle}" type="checkbox"',settings)
        for removed in ("Usage API","Automatic account switching","thresholdSetting"):
            self.assertNotIn(removed,settings)
        self.assertNotIn('>Enable</button>',settings)
        self.assertNotIn('>Disable</button>',settings)

    def test_json_used_percent_is_rendered_as_remaining_percent(self):
        self.assertIn("p=100-used",APP_JS)
        self.assertIn("obj.remaining_percent,obj.remaining_pct,obj.percent_remaining",APP_JS)

    def test_tailadmin_assets_are_local_and_include_accessible_navigation(self):
        self.assertIn('/assets/app.css',app.HTML)
        self.assertIn('/assets/app.js',app.HTML)
        self.assertIn('aria-controls="sidebar"',app.HTML)
        self.assertIn('Skip to content',app.HTML)
        self.assertNotIn('fonts.googleapis.com',app.HTML)
        self.assertNotIn('<style>',app.HTML)

    def test_account_alias_editor_is_available(self):
        self.assertIn('id="aliasModal"',app.HTML)
        self.assertIn("openAliasEditor",APP_JS)
        self.assertIn("/api/account-alias",APP_JS)

    def test_parsing_missing_optional_fields_keeps_them_blank(self):
        fields=app.parse_profile_text('''model_provider = "example"\n[model_providers.example]\nname = "Example"\nbase_url = "https://example.test/v1"\n''')
        for key in ("model","reasoning_effort","approval_policy","sandbox_mode"):
            self.assertEqual(fields[key],"")

    def test_profile_activation_rolls_back_files_when_restart_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir()
            config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path):
                profile=app.create_api_profile({"name":"Broken","base_url":"https://ttmapi.site/v1","model":"gpt-test","api_key":"sk-test"})
                app.atomic_write(home/"config.toml",b"model = \"old\"\n")
                app.atomic_write(app.active_provider_env_path(),b"OLD=value\n")
                with patch("app.restart_services",side_effect=[
                    app.ServiceCommandError("restart failed","failed output"),
                    {"message":"Restarted.","output":"rollback output"},
                ]):
                    with self.assertRaises(app.ServiceCommandError) as caught:
                        app.apply_profile(profile["id"])

                self.assertEqual((home/"config.toml").read_text(),'model = "old"\n')
                self.assertEqual(app.active_provider_env_path().read_text(),"OLD=value\n")
                self.assertIn("rollback output",caught.exception.output)

    def test_api_profile_activation_does_not_create_backups(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();(home/"config.toml").write_text('model = "original"\n')
            config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path),patch("app.restart_services",return_value={"message":"Restarted.","output":"ok"}):
                profile=app.create_api_profile({"name":"API Account","base_url":"https://ttmapi.site/v1","model":"gpt-api","api_key":"sk-test"})
                result=app.apply_profile(profile["id"])

            self.assertIn('model = "gpt-api"',(home/"config.toml").read_text())
            self.assertNotIn("sk-test",(home/"config.toml").read_text())
            self.assertFalse((root/"webui"/"backups").exists())
            self.assertNotIn("Backup",result["message"])

    def test_profile_switch_only_patches_managed_config_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            original='''# keep this comment
model = "original"
custom_setting = "untouched"

[mcp_servers.keep]
command = "keep-command"
'''
            config_file.write_text(original);config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path),patch("app.restart_services",return_value={"message":"Restarted.","output":"ok"}):
                profile=app.create_api_profile({"name":"Patch API","base_url":"https://ttmapi.site/v1","model":"gpt-api","api_key":"sk-test"})
                app.apply_profile(profile["id"],restart=False)
                active=config_file.read_text()
                app.apply_profile("default",restart=False)
                restored=config_file.read_text()

            for content in (active,restored):
                self.assertIn("# keep this comment",content)
                self.assertIn('custom_setting = "untouched"',content)
                self.assertIn('[mcp_servers.keep]',content)
                self.assertIn('command = "keep-command"',content)
                self.assertIn(app.MANAGED_PROVIDER_BEGIN,content)
            self.assertIn('model = "gpt-api"',active)
            self.assertIn('model = "original"',restored)
            self.assertNotIn("CODEX_PROFILE_API_KEY",active)

    def test_reapplying_default_leaves_current_model_settings_untouched(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            config_file.write_text('model = "saved-default"\n');config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path):
                config_file.write_text('model_reasoning_effort = "low"\n')
                app.apply_profile("default",restart=False)
                self.assertEqual(config_file.read_text(),'model_reasoning_effort = "low"\n')

    def test_pasted_toml_fills_api_profile_fields(self):
        pasted='''```toml
model_provider = "codex_local_access"
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
approval_policy = "never"
sandbox_mode = "danger-full-access"
[model_providers.codex_local_access]
name = "Codex API Service"
base_url = "https://ttmapi.site/v1"
experimental_bearer_token = "sk-imported"
http_headers = { x-cockpit-instance-id = ".codex" }
```'''
        fields=app.parse_profile_text(pasted)
        self.assertEqual(fields["name"],"Codex API Service")
        self.assertEqual(fields["model"],"gpt-5.6-sol")
        self.assertEqual(fields["api_key"],"sk-imported")
        self.assertEqual(fields["sandbox_mode"],"danger-full-access")
        self.assertEqual(fields["http_headers"]["x-cockpit-instance-id"],".codex")

    def test_default_profile_does_not_restore_untracked_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            config_file.write_text('model = "original"\n');config_path=root/"webui"/"config.json"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=config_path),patch("app.restart_services",return_value={"message":"Restarted.","output":"ok"}):
                config_file.write_text('model = "api"\n')
                app.atomic_write(app.active_provider_env_path(),b"CODEX_PROFILE_API_KEY=secret\n")
                state=app.read_webui_config();state["active_profile"]="api-profile";app.write_webui_config(state)
                app.apply_profile("default")
                env_after=app.active_provider_env_path().read_text()

            self.assertEqual(config_file.read_text(),'model = "api"\n')
            self.assertIn("CODEX_PROFILE_API_KEY",env_after)

    def test_switch_reverts_only_owned_fields_and_preserves_manual_edits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            config_file.write_text('model = "original"\nmodel_reasoning_effort = "medium"\ncustom_setting = "before"\n')
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":"https://ttmapi.site/v1","model":"api-model","api_key":"sk-test","reasoning_effort":""})
                app.apply_profile(profile["id"],restart=False)
                active=config_file.read_text()
                self.assertIn('model = "api-model"',active)
                self.assertIn('model_reasoning_effort = "medium"',active)
                config_file.write_text(active.replace('model_reasoning_effort = "medium"','model_reasoning_effort = "high"').replace('custom_setting = "before"','custom_setting = "after"'))
                app.apply_profile("default",restart=False)
                restored=config_file.read_text()
            self.assertIn('model = "original"',restored)
            self.assertIn('model_reasoning_effort = "high"',restored)
            self.assertIn('custom_setting = "after"',restored)

    def test_manual_edit_to_owned_field_is_not_overwritten_on_return(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            config_file.write_text('model = "original"\n')
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":"https://ttmapi.site/v1","model":"api-model","api_key":"sk-test"})
                app.apply_profile(profile["id"],restart=False)
                config_file.write_text(config_file.read_text().replace('model = "api-model"','model = "user-choice"'))
                app.apply_profile("default",restart=False)
                self.assertIn('model = "user-choice"',config_file.read_text())

    def test_default_removes_stale_managed_provider_when_state_is_already_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root=Path(temp_dir);home=root/"codex";home.mkdir();config_file=home/"config.toml"
            with patch("app.codex_home",return_value=home),patch("app.webui_config_path",return_value=root/"webui"/"config.json"):
                profile=app.create_api_profile({"name":"API","base_url":app.API_BASE_URL,"model":"","api_key":"sk-test"})
                provider=app.read_profile_manifest(profile["id"])["provider_id"]
                config_file.write_text(f'model = "user-model"\nmodel_reasoning_effort = "low"\nmodel_provider = "{provider}"\n')
                app.write_webui_config({"active_profile":"default","managed_config_overlay":{}})
                app.apply_profile("default",restart=False)
                result=config_file.read_text()
            self.assertNotIn('model_provider =',result)
            self.assertIn('model = "user-model"',result)
            self.assertIn('model_reasoning_effort = "low"',result)
            self.assertIn(f'[model_providers.{provider}]',result)

if __name__ == "__main__":
    unittest.main()
