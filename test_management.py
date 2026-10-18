"""CLI/WebUI parity, lifecycle, credential preservation, and process safety."""
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import accounts
import app
from test_accounts import auth


class IsolatedManager(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.home=self.root/"codex"
        self.config=self.root/"settings.json"
        self.env={**os.environ,"CODEX_HOME":str(self.home),"CODEX_ACCOUNT_MANAGER_CONFIG":str(self.config)}
        self.environment=patch.dict(os.environ,self.env)
        self.environment.start();self.addCleanup(self.environment.stop)
        self.store=app.account_store()
        app.set_restart_after_switch(False)

    def cli(self,*args,code=0):
        process=subprocess.run([sys.executable,str(Path(app.__file__).resolve()),*args,"--json"],
                               env=self.env,text=True,capture_output=True,timeout=15)
        self.assertEqual(process.returncode,code,process.stdout+process.stderr)
        return json.loads(process.stdout)

    def fields_file(self,document,name="fields.json"):
        path=self.root/name;path.write_text(json.dumps(document));return str(path)


class ManagementTests(IsolatedManager):
    def test_cli_has_settings_api_profile_lifecycle_and_private_export(self):
        self.store.import_documents([auth(),auth("other")])
        self.assertFalse(self.cli("web","status")["enabled"])
        self.cli("config","set","auto_ping_enabled","true")
        self.cli("auto-ping","enable","--account","2")
        state=self.cli("auto-ping","status")
        self.assertTrue(state["enabled"])
        self.assertTrue(state["accounts"][1]["auto_ping_configured"])
        self.cli("auto-ping","disable","--account","2")
        self.cli("config","set","auto_ping_enabled","false")
        self.assertFalse(self.cli("config","show")["auto_ping_enabled"])
        self.assertFalse(self.cli("auto-ping","once")["refreshed"])
        profile=self.cli("profiles","create","--file",self.fields_file({"name":"API","api_key":"synthetic-key","model":"m1"}))["profile"]
        pid=profile["id"]
        self.cli("profiles","update",pid,"--model","m2")
        detail=self.cli("profiles","detail",pid)["profile"]
        self.assertEqual(detail["name"],"API")
        self.assertEqual(detail["model"],"m2")
        self.assertNotIn("synthetic-key",json.dumps(detail))
        self.assertEqual((app.profile_path(pid)/"token").read_text().strip(),"synthetic-key")
        self.cli("profiles","apply",pid)
        self.assertEqual(self.cli("status")["active_profile"],pid)
        self.cli("profiles","apply","default")
        self.cli("profiles","delete",pid)
        destination=self.root/"export.json"
        result=self.cli("export",str(destination),"--query","2")
        self.assertEqual(result["count"],1)
        self.assertNotIn("refresh_token",json.dumps(result))
        self.assertEqual(destination.stat().st_mode & 0o777,0o600)
        self.assertEqual(accounts.identity(json.loads(destination.read_text())[0])["email"],"other@example.test")
        self.cli("export",str(destination),code=1)
        self.assertEqual(len(json.loads(destination.read_text())),1)
        self.assertTrue(self.cli("doctor")["healthy"])

    def test_cli_service_errors_keep_complete_output(self):
        stream=io.StringIO()
        with patch("sys.argv",["app.py","restart","--json"]),patch("app.restart_services",side_effect=app.ServiceCommandError("restart failed","complete systemctl output")),redirect_stdout(stream):
            with self.assertRaises(SystemExit) as error:app.main()
        self.assertEqual(error.exception.code,1)
        self.assertEqual(json.loads(stream.getvalue())["output"],"complete systemctl output")

    def test_cli_human_service_output_is_visible(self):
        stream=io.StringIO()
        with redirect_stdout(stream):app.cli_output({"message":"Restarted.","output":"full status details"})
        self.assertIn("Restarted.",stream.getvalue())
        self.assertIn("full status details",stream.getvalue())

    def test_cli_toml_create_parse_and_key_file_never_echo_secret(self):
        toml=self.root/"provider.toml"
        toml.write_text('model_provider = "p"\nmodel = "m1"\n[model_providers.p]\nname = "Provider"\nbase_url = "https://ttmapi.site/v1"\nexperimental_bearer_token = "synthetic-key"\n')
        parsed=self.cli("profiles","parse",str(toml))
        self.assertNotIn("synthetic-key",json.dumps(parsed))
        self.assertTrue(parsed["has_api_key"])
        out=self.root/"parsed.json"
        self.cli("profiles","parse",str(toml),"--output",str(out))
        self.assertEqual(json.loads(out.read_text())["api_key"],"synthetic-key")
        created=self.cli("profiles","create","--toml",str(toml))["profile"]
        key=self.root/"key";key.write_text("replacement-key\n")
        result=self.cli("profiles","update",created["id"],"--api-key-file",str(key))
        self.assertNotIn("replacement-key",json.dumps(result))
        self.assertEqual((app.profile_path(created["id"])/"token").read_text().strip(),"replacement-key")

    def test_previous_switch_same_target_and_deleted_previous(self):
        keys=self.store.import_documents([auth(),auth("other")])
        app.do_switch("1");app.do_switch("2");app.do_switch("2")
        self.assertEqual(self.store.read()["previous_active_account_key"],keys[0])
        app.do_switch("-")
        self.assertEqual(self.store.read()["active_account_key"],keys[0])
        self.store.remove("2")
        with self.assertRaisesRegex(app.ServiceCommandError,"No previous"):
            app.do_switch("-")

    def test_relogin_same_active_identity_does_not_discard_new_credentials(self):
        old=auth();old["last_refresh"]="2025-01-01T00:00:00Z"
        self.store.import_documents(old)
        with self.store.switching("1"):pass
        new=copy.deepcopy(old);new["tokens"]["refresh_token"]="new-login-refresh";new["last_refresh"]="2026-10-18T00:00:00Z"
        self.store.import_documents(new)
        self.store.list()
        saved=json.loads(self.store.snapshot("user-1::account-1").read_text())
        self.assertEqual(saved["tokens"]["refresh_token"],"new-login-refresh")
        with self.store.switching("1"):pass
        self.assertEqual(json.loads((self.home/"auth.json").read_text())["tokens"]["refresh_token"],"new-login-refresh")

    def test_ping_keeps_rotated_credentials_on_success_and_failure_without_switching(self):
        self.store.import_documents([auth(),auth("other")])
        with self.store.switching("1"):pass
        original=(self.home/"auth.json").read_bytes()
        def ping(args,**kwargs):
            path=Path(kwargs["env"]["CODEX_HOME"])/"auth.json"
            updated=json.loads(path.read_text());updated["tokens"]["refresh_token"]="rotated-by-ping"
            updated["last_refresh"]="2026-10-18T00:00:00Z";path.write_text(json.dumps(updated))
            return subprocess.CompletedProcess(args,self.ping_code,"OK" if not self.ping_code else "","failure")
        for code in (0,1):
            self.ping_code=code
            with patch("app.shutil.which",return_value="/usr/bin/codex"),patch("app.subprocess.run",side_effect=ping):
                if code:
                    with self.assertRaisesRegex(RuntimeError,"failure"):app.run_account_ping("2")
                else:app.run_account_ping("2")
            self.assertEqual((self.home/"auth.json").read_bytes(),original)
            self.assertEqual(json.loads(self.store.snapshot("other::account-1").read_text())["tokens"]["refresh_token"],"rotated-by-ping")

    def test_relogin_in_same_timestamp_second_wins_over_older_active_file(self):
        old=auth();old["last_refresh"]="2026-10-18T00:00:00Z"
        self.store.import_documents(old)
        with self.store.switching("1"):pass
        new=copy.deepcopy(old);new["tokens"]["refresh_token"]="same-second-new"
        self.store.import_documents(new)
        same_time=(self.home/"auth.json").stat().st_mtime_ns
        os.utime(self.store.snapshot("user-1::account-1"),ns=(same_time,same_time))
        self.store.list()
        self.assertEqual(json.loads(self.store.snapshot("user-1::account-1").read_text())["tokens"]["refresh_token"],"same-second-new")

    def test_external_refresh_with_equal_file_time_is_detected_by_content(self):
        old=auth();old["last_refresh"]="2026-10-18T00:00:00Z"
        self.store.import_documents(old)
        with self.store.switching("1"):pass
        same_time=self.store.snapshot("user-1::account-1").stat().st_mtime_ns
        new=copy.deepcopy(old);new["tokens"]["refresh_token"]="external-same-time"
        active=self.home/"auth.json";active.write_text(json.dumps(new));os.utime(active,ns=(same_time,same_time))
        self.store.list()
        self.assertEqual(json.loads(self.store.snapshot("user-1::account-1").read_text())["tokens"]["refresh_token"],"external-same-time")

    def test_ping_does_not_overwrite_auth_changed_by_another_client(self):
        self.store.import_documents(auth())
        with self.store.switching("1"):pass
        with self.store.ping_credentials("1") as (document,label,save):
            external=copy.deepcopy(document);external["tokens"]["refresh_token"]="external-client"
            (self.home/"auth.json").write_text(json.dumps(external))
            ping=copy.deepcopy(document);ping["tokens"]["refresh_token"]="ping-token"
            save(ping)
        self.assertEqual(json.loads((self.home/"auth.json").read_text())["tokens"]["refresh_token"],"external-client")

    def test_default_cli_switch_works_without_web_or_systemd(self):
        self.store.import_documents(auth())
        self.config.unlink()
        result=self.cli("switch","1")
        self.assertIn("restart is disabled",result["message"])
        self.assertFalse(self.cli("web","status")["enabled"])
        self.assertFalse(self.cli("web","status")["running"])
        self.assertEqual(self.cli("status")["active_account"]["account_key"],"user-1::account-1")

    def test_background_ping_subprocess_is_cancelled_on_shutdown(self):
        stop=threading.Event()
        timer=threading.Timer(.1,stop.set);timer.start()
        started=time.monotonic()
        try:
            with self.assertRaisesRegex(RuntimeError,"cancelled"):
                app.execute_ping([sys.executable,"-c","import time; time.sleep(60)"],self.env,stop)
        finally:timer.cancel()
        self.assertLess(time.monotonic()-started,4)

    def test_export_round_trip_cpa_batch_and_diagnostics_permission_repair(self):
        self.store.import_documents([auth(),auth("other")])
        exported=self.store.export_documents(format_name="cpa")
        with patch("app.codex_home",return_value=self.root/"restored"):
            app.import_accounts_text(json.dumps(exported),"cpa")
            self.assertEqual(len(app.list_accounts(False)["accounts"]),2)
        path=self.store.snapshot("user-1::account-1");path.chmod(0o644)
        self.assertFalse(app.doctor_report()["healthy"])
        self.assertEqual(path.stat().st_mode & 0o777,0o644)
        self.assertTrue(app.doctor_report(True)["healthy"])
        self.assertEqual(path.stat().st_mode & 0o777,0o600)
        path.write_text('{"invalid":true}')
        report=self.cli("doctor",code=1)
        self.assertIn("invalid_snapshot",[issue["code"] for issue in report["issues"]])
        self.assertNotIn("refresh_token",json.dumps(report))

    def test_processes_update_settings_without_losing_account_selections(self):
        keys=self.store.import_documents([auth("parallel-"+str(i)) for i in range(6)])
        processes=[subprocess.Popen([sys.executable,app.__file__,"auto-ping","enable","--account",key,"--json"],env=self.env,
                                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for key in keys]
        for process in processes:
            stdout,stderr=process.communicate(timeout=15)
            self.assertEqual(process.returncode,0,stdout+stderr)
        self.assertEqual(set(app.read_webui_config()["auto_ping_accounts"]),set(keys))

    def test_failed_activation_keeps_concurrent_setting_change(self):
        self.store.import_documents([auth(),auth("other")])
        app.do_switch("1");app.set_restart_after_switch(True)
        calls=[]
        def restart(services):
            calls.append(services)
            if len(calls)==1:
                app.set_auto_ping(True)
                raise RuntimeError("failed")
            return {"message":"restored","output":""}
        with patch("app.restart_services",side_effect=restart):
            with self.assertRaises(app.ServiceCommandError):app.do_switch("2")
        self.assertTrue(app.auto_ping_enabled())
        self.assertEqual(self.store.read()["active_account_key"],"user-1::account-1")

    def test_interrupted_switch_rolls_back_auth_and_config(self):
        self.store.import_documents([auth(),auth("other")])
        app.do_switch("1")
        original=(self.home/"auth.json").read_bytes()
        app.set_restart_after_switch(True)
        with patch("app.restart_services",side_effect=[KeyboardInterrupt(),{"message":"restored","output":""}]):
            with self.assertRaises(KeyboardInterrupt):app.do_switch("2")
        self.assertEqual((self.home/"auth.json").read_bytes(),original)


class ParityHTTPTests(IsolatedManager):
    def setUp(self):
        super().setUp()
        self.store.import_documents([auth(),auth("other")])
        self.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler);self.server.quiet=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.shutdown)
        self.base=f"http://127.0.0.1:{self.server.server_port}"

    def shutdown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

    def request(self,path,document=None,headers=None):
        req=urllib.request.Request(self.base+path,data=json.dumps(document).encode() if document is not None else None,
                                   headers={"Content-Type":"application/json",**(headers or {})})
        with urllib.request.urlopen(req,timeout=5) as response:return json.load(response)

    def test_http_restart_commands_match_cli_and_invalid_commands_do_not_overwrite(self):
        commands={"app":["echo app"]}
        with patch("app.run_cmd") as run:
            self.request('/api/config/restart-commands',{"commands":commands})
            run.assert_not_called()
        self.assertEqual(self.cli('config','show')['restart_commands'],commands)
        self.assertEqual(self.request('/api/config')['restart_commands'],commands)
        for invalid in ({}, {"commands":{"app":[]}}):
            with self.assertRaises(urllib.error.HTTPError) as caught:self.request('/api/config/restart-commands',invalid)
            self.assertEqual(caught.exception.code,400);caught.exception.close()
        self.assertEqual(self.request('/api/config')['restart_commands'],commands)
        self.request('/api/config/restart-commands',{"commands":None})
        self.assertEqual(self.cli('config','show')['restart_commands'],app.default_restart_commands())

    def test_public_assets_are_served_without_exposing_other_files(self):
        for route, (filename, mime) in app.WEB_ASSETS.items():
            with self.subTest(route=route), urllib.request.urlopen(self.base+route+"?v=1") as response:
                self.assertEqual(response.headers["Content-Type"],mime)
                self.assertEqual(response.headers["X-Content-Type-Options"],"nosniff")
                self.assertEqual(response.read(),(app.WEB_ROOT/"assets"/filename).read_bytes())
        for route in ("/assets/../app.py", "/assets/%2e%2e/app.py", "/assets/.env", "/assets/", "/web/src/app.css"):
            with self.subTest(route=route),self.assertRaises(urllib.error.HTTPError) as context:
                urllib.request.urlopen(self.base+route)
            self.assertEqual(context.exception.code,404);context.exception.close()
        req=urllib.request.Request(self.base+"/assets/app.js",headers={"Host":"evil.test"})
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(req)
        self.assertEqual(context.exception.code,403);context.exception.close()

    def test_autoping_failures_and_retries_are_visible_in_both_interfaces(self):
        account=self.store.find("2")
        retry="2030-01-02T03:04:05+00:00"
        app.update_auto_ping_state(account["account_key"],{"last_result":"error","last_error":"Ping failed", "next_retry_at":retry})
        web=self.request("/api/accounts")["accounts"][1]
        cli=self.cli("list")["accounts"][1]
        for result in (web,cli):
            self.assertEqual(result["auto_ping_last_error"],"Ping failed")
            self.assertEqual(result["auto_ping_next_retry_at"],retry)
        app.update_auto_ping_state(account["account_key"],{"last_result":"ok","last_error":None,"next_retry_at":None})
        self.assertIsNone(self.request("/api/accounts")["accounts"][1]["auto_ping_last_error"])

    def test_activity_tracks_cli_web_failures_and_stable_account_keys(self):
        self.cli("switch","1")
        first=self.store.find("1")["account_key"]
        with patch("app._run_account_ping",return_value="OK"):
            self.request('/api/ping-account',{"query":"1"})
        with patch("app._run_account_ping",side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(urllib.error.HTTPError) as context:self.request('/api/ping-account',{"query":"1"})
            self.assertEqual(context.exception.code,400);context.exception.close()
        app.set_account_alias("1","Renamed")
        profile=app.create_api_profile({"name":"Stats API","base_url":app.API_BASE_URL,"api_key":"do-not-store"})
        self.cli("profiles","apply",profile["id"])
        self.request('/api/profiles/apply',{"profile_id":"default"})
        web=self.request('/api/activity')
        cli=self.cli("activity")
        self.assertEqual(web,cli)
        native=next(row for row in cli["accounts"] if row["id"]==first)
        self.assertEqual(native["name"],"Renamed")
        self.assertEqual(native["ping"]["success"],1)
        self.assertEqual(native["ping"]["failed"],1)
        self.assertEqual(native["switch"]["success"],2)
        api=next(row for row in cli["accounts"] if row["id"]==profile["id"])
        self.assertFalse(api["ping_available"])
        self.assertFalse(api["active"])
        self.assertTrue(native["active"])
        self.assertTrue(native["auth_active"])
        self.assertEqual(api["switch"]["success"],1)
        disk=(app.storage_root()/"activity.json").read_text()
        for secret in ("do-not-store","synthetic failure","refresh-user"):
            self.assertNotIn(secret,disk)
        self.assertEqual((app.storage_root()/"activity.json").stat().st_mode&0o777,0o600)

    def test_activity_counts_failed_switch_after_rollback(self):
        self.cli("switch","1");app.set_restart_after_switch(True)
        with patch("app.restart_services",side_effect=[RuntimeError("restart failed"),{"output":"restored"}]):
            with self.assertRaises(app.ServiceCommandError):app.do_switch("2")
        row=next(a for a in app.activity_status()["accounts"] if a["id"]==self.store.find("2")["account_key"])
        self.assertEqual(row["switch"]["failed"],1)
        self.assertEqual(row["switch"]["success"],0)
        self.assertEqual(self.store.read()["active_account_key"],self.store.find("1")["account_key"])

    def test_api_profile_export_is_explicit_and_matches_private_cli_export(self):
        first=app.create_api_profile({"name":"First","base_url":app.API_BASE_URL,"api_key":"first-key","model":"model-one","http_headers":{"x-custom":"kept"}})
        second=app.create_api_profile({"name":"Second","base_url":app.API_BASE_URL,"api_key":"second-key"})
        listed=self.request('/api/profiles')
        self.assertNotIn("first-key",json.dumps(listed))
        selected=self.request('/api/profiles/export',{"profile_ids":[first["id"]]})
        self.assertEqual(selected["count"],1)
        self.assertEqual(selected["documents"][0]["api_key"],"first-key")
        self.assertEqual(selected["documents"][0]["http_headers"],{"x-custom":"kept"})
        self.assertEqual(self.request('/api/profiles/export',{})["count"],2)
        output=self.root/"profiles-export.json"
        self.cli("profiles","export",first["id"],"--output",str(output))
        self.assertEqual(json.loads(output.read_text()),selected["documents"])
        self.assertEqual(output.stat().st_mode&0o777,0o600)
        self.cli("profiles","export","--query",first["id"],"--output",str(output),code=1)
        self.assertEqual(json.loads(output.read_text()),selected["documents"])
        for ids in ([],"invalid",["missing-profile"]):
            with self.assertRaises(urllib.error.HTTPError) as context:self.request('/api/profiles/export',{"profile_ids":ids})
            self.assertEqual(context.exception.code,400);context.exception.close()
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request('/api/profiles/export',{},headers={"Origin":"https://evil.test"})
        self.assertEqual(context.exception.code,400);context.exception.close()

    def test_web_exposes_export_doctor_settings_profile_edit_and_autoping(self):
        self.request('/api/config',{"name":"auto_ping_enabled","value":True})
        self.assertTrue(self.cli("config","show")["auto_ping_enabled"])
        self.request('/api/config/account-auto-ping',{"query":"2","enabled":True})
        self.assertTrue(self.cli("auto-ping","status")["accounts"][1]["auto_ping_configured"])
        profile=self.request('/api/profiles',{"name":"web","base_url":app.API_BASE_URL,"api_key":"web-key","model":"before"})["profile"]
        self.cli("profiles","update",profile["id"],"--model","after")
        updated=self.request('/api/profiles/detail?profile_id='+profile["id"])["profile"]
        self.assertEqual(updated["model"],"after")
        self.request('/api/profiles/update',{"profile_id":profile["id"],"name":"web-updated"})
        self.assertEqual(self.cli("profiles","detail",profile["id"])["profile"]["model"],"after")
        result=self.request('/api/export-accounts',{"queries":["2"],"format":"standard"})
        self.assertEqual(result["count"],1)
        self.assertEqual(accounts.identity(result["documents"][0])["email"],"other@example.test")
        self.assertTrue(self.request('/api/doctor',{"fix_permissions":False})["healthy"])
        with patch("app.auto_ping_worker_once",return_value={"pinged":[],"refreshed":False}):
            self.assertFalse(self.request('/api/auto-ping/once',{})["refreshed"])
        with patch("app.restart_services",return_value={"message":"ok","output":""}) as restart:
            self.request('/api/restart-services',{"service":"app"})
            restart.assert_called_once_with((app.CODEX_APP_SERVICE,))

    def test_explicit_proxy_host_accepts_matching_origin_only(self):
        self.server.allowed_hosts={"codex-auth.example.test"}
        headers={"Host":"codex-auth.example.test","Origin":"https://codex-auth.example.test"}
        self.assertTrue(self.request('/api/health',headers=headers)["ok"])
        self.request('/api/config',{"name":"restart_after_switch","value":False},headers)
        with self.assertRaises(urllib.error.HTTPError) as context:
            self.request('/api/config',{"name":"restart_after_switch","value":False},{**headers,"Origin":"https://untrusted.test"})
        self.assertEqual(context.exception.code,400);context.exception.close()

    def test_host_rebinding_cannot_read_or_export_credentials(self):
        for path,body in (('/api/accounts',None),('/api/export-accounts',{})):
            with self.assertRaises(urllib.error.HTTPError) as context:
                self.request(path,body,{"Host":"evil.test","Origin":"http://evil.test"})
            self.assertEqual(context.exception.code,403);context.exception.close()


class LifecycleTests(IsolatedManager):
    def available_port(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));return sock.getsockname()[1]

    def launch(self,port):
        process=subprocess.Popen([sys.executable,app.__file__,"web","serve","--port",str(port),"--quiet"],env=self.env,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop_process,process)
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            if process.poll() is not None:self.fail('WebUI exited: '+str(process.communicate()))
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health",timeout=.2):return process
            except (OSError,urllib.error.URLError):time.sleep(.03)
        self.fail("WebUI did not become ready")

    def stop_process(self,process):
        if process.poll() is None:
            process.terminate()
            try:process.communicate(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.communicate()
        else:process.communicate()

    def test_no_arguments_and_disabled_web_create_no_listener_or_worker(self):
        with patch("sys.argv",["app.py"]),patch("app.ThreadingHTTPServer") as server,patch("app.threading.Thread") as thread,redirect_stdout(io.StringIO()):
            app.main()
        server.assert_not_called();thread.assert_not_called()
        self.assertFalse(self.cli("web","status")["running"])
        self.cli("web","serve",code=1)
        self.assertFalse(self.cli("web","status")["enabled"])

    def test_cli_and_web_disable_release_port_and_startup_remains_disabled(self):
        port=self.available_port();self.cli("web","enable")
        process=self.launch(port)
        status=self.cli("web","status")
        self.assertTrue(status["running"]);self.assertEqual(status["port"],port)
        self.cli("web","serve","--port",str(self.available_port()),code=1)
        self.cli("web","disable")
        process.communicate(timeout=6);self.assertEqual(process.returncode,0)
        self.assertFalse(self.cli("web","status")["running"])
        self.cli("web","serve",code=1)
        self.cli("web","enable")
        process=self.launch(port)
        req=urllib.request.Request(f"http://127.0.0.1:{port}/api/config",data=b'{"name":"webui_enabled","value":false}',headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=5) as response:self.assertTrue(json.load(response)["ok"])
        process.communicate(timeout=6);self.assertEqual(process.returncode,0)
        self.assertFalse(self.cli("web","status")["running"])
        with self.assertRaises(OSError):socket.create_connection(("127.0.0.1",port),timeout=.2)
        self.assertEqual(self.cli("list")["accounts"],[])

    def test_independent_autoping_worker_has_no_web_socket_and_exits_on_signal(self):
        port=self.available_port()
        process=subprocess.Popen([sys.executable,app.__file__,"auto-ping","run"],env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        self.addCleanup(self.stop_process,process)
        time.sleep(.2)
        self.assertIsNone(process.poll())
        self.assertFalse(self.cli("web","status")["running"])
        process.terminate();process.communicate(timeout=5)
        self.assertEqual(process.returncode,130)
