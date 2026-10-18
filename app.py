#!/usr/bin/env python3
"""
Codex Account Manager
- Python standard library only
- No proxying of Codex traffic
- Native account storage, OAuth login, switching, and usage
- Optional Auto Ping uses the same native cache
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import uuid
import sys
import signal
import getpass
import ipaddress
import functools
from contextlib import contextmanager

from locking import FileRLock
from activity import ActivityStore
from web_runtime import WebRuntime
import time
import webbrowser

from accounts import AccountStore, json_bytes, private_write, manager_config_path
from login import LoginManager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    tomllib = None

APP_VERSION = "3.1.0"
API_BASE_URL = "https://ttmapi.site/v1"
DEFAULT_USAGE_URL = "https://ttmapi.site/v1/usage"

CODEX_APP_SERVICE = "codex-app-server"
MANAGED_PROVIDER_BEGIN = "# BEGIN codex-account-manager managed providers"
MANAGED_PROVIDER_END = "# END codex-account-manager managed providers"
MANAGED_TOP_LEVEL_KEYS = (
    "model_provider","model","model_reasoning_effort","approval_policy","sandbox_mode","model_catalog_json",
)

WEB_ROOT = Path(__file__).resolve().parent / "web"
HTML = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
# Explicit public files only: account files and arbitrary paths are never served.
WEB_ASSETS = {
    "/assets/codex-symbol-color.svg": ("codex-symbol-color.svg", "image/svg+xml"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/assets/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/assets/outfit-latin.woff2": ("outfit-latin.woff2", "font/woff2"),
}
# Serve one consistent UI release for the lifetime of this process.
WEB_ASSET_CONTENT = {route: (WEB_ROOT / "assets" / filename).read_bytes()
                     for route, (filename, _) in WEB_ASSETS.items()}

def run_cmd(args: list[str], timeout: int = 45) -> subprocess.CompletedProcess:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, env=os.environ.copy())

class ServiceCommandError(RuntimeError):
    def __init__(self,message: str,output: str):
        super().__init__(message)
        self.output=output

def format_command_result(args: list[str],cp: subprocess.CompletedProcess) -> str:
    return "\n".join([
        "$ "+shlex.join(args),
        f"exit code: {cp.returncode}",
        "stdout:",
        cp.stdout.rstrip() or "(empty)",
        "stderr:",
        cp.stderr.rstrip() or "(empty)",
    ])

def restart_services(services: tuple[str, ...] = (CODEX_APP_SERVICE,)) -> dict:
    restarted=[]
    reports=[]
    commands=configured_restart_commands()
    if any(service!=CODEX_APP_SERVICE for service in services):raise RuntimeError("Unknown restart target")
    for service in services:
        target="app"
        for index,command in enumerate(commands[target]):
            args=shlex.split(command)
            timeout=60
            try:
                cp=run_cmd(args,timeout)
            except (OSError,subprocess.TimeoutExpired) as error:
                reports.append("$ "+shlex.join(args)+"\n"+str(error))
                raise ServiceCommandError(f"Could not execute restart command for {service}.","\n\n".join(reports)) from error
            reports.append(format_command_result(args,cp))
            if cp.returncode!=0:
                completed=f" Restarted before failure: {', '.join(restarted)}." if restarted else ""
                raise ServiceCommandError(f"Command {index+1} failed for {service}.{completed}","\n\n".join(reports))
        restarted.append(service)
    return {"message":"Restarted " + " and ".join(restarted) + ".","output":"\n\n".join(reports)}

WEBUI_CONFIG_LOCK=FileRLock(lambda: webui_config_path().with_name(webui_config_path().name+".lock"))
CONFIG_OPERATION_LOCK=FileRLock(lambda: storage_root()/".config-operation.lock")
AUTO_PING_WAKE=threading.Event()
AUTO_PING_STOP=threading.Event()
AUTO_PING_CONTEXT=threading.local()
AUTO_PING_RETRY_DELAY=timedelta(minutes=5)
AUTO_PING_MAX_ATTEMPTS=3
AUTO_PING_TIMING_TOLERANCE_SECONDS=5

def webui_config_path() -> Path:
    return manager_config_path()

def read_webui_config() -> dict:
    path=webui_config_path()
    with WEBUI_CONFIG_LOCK:
        try:
            data=json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError,json.JSONDecodeError) as e:
            raise RuntimeError(f"Could not read WebUI config: {e}") from e
    return data if isinstance(data,dict) else {}

def write_webui_config(data: dict) -> None:
    path=webui_config_path()
    with WEBUI_CONFIG_LOCK:
        try:
            private_write(path,json_bytes(data))
        except OSError as e:
            raise RuntimeError(f"Could not write WebUI config: {e}") from e


def default_restart_commands() -> dict:
    return {"app":["systemctl restart codex-app-server", "codex app-server daemon restart"]}


def configured_restart_commands() -> dict:
    commands=read_webui_config().get("restart_commands")
    if commands is None:return default_restart_commands()
    # Existing installations may still have a saved web list; never execute it.
    if isinstance(commands,dict) and set(commands)=={"app","web"}:
        commands={"app":commands["app"]}
    return validate_restart_commands(commands)


def validate_restart_commands(commands) -> dict:
    if not isinstance(commands,dict) or set(commands)!={"app"}:
        raise RuntimeError("Restart commands must contain only the app command list")
    result={}
    for target,lines in commands.items():
        if not isinstance(lines,list) or not 1<=len(lines)<=10:
            raise RuntimeError(f"{target}: provide between 1 and 10 commands")
        result[target]=[]
        for line in lines:
            if not isinstance(line,str) or not line.strip() or len(line)>4096 or any(c in line for c in "\x00\r\n"):
                raise RuntimeError(f"{target}: each command must be a non-empty single line (up to 4096 characters)")
            try:args=shlex.split(line)
            except ValueError as error:raise RuntimeError(f"{target}: invalid command quoting: {error}") from error
            if not args or not args[0]:raise RuntimeError(f"{target}: command executable is required")
            result[target].append(line.strip())
    return result


def set_restart_commands(commands) -> dict:
    validated=validate_restart_commands(commands) if commands is not None else None
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        if validated is None:config.pop("restart_commands",None)
        else:config["restart_commands"]=validated
        write_webui_config(config)
    return {"message":"Restart commands saved. No commands were executed.",
            "restart_commands":validated or default_restart_commands()}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def storage_root() -> Path:
    configured=os.environ.get("CODEX_ACCOUNT_MANAGER_DATA_DIR")
    return Path(configured).expanduser() if configured else webui_config_path().parent/"data"

def activity_store():
    return ActivityStore(storage_root()/"activity.json")


def record_activity(kind, identifier, action, success, started):
    try:activity_store().record(kind,str(identifier),action,success,(time.monotonic()-started)*1000)
    except (OSError,ValueError):print("Could not save activity statistics",file=sys.stderr)


def activity_status():
    snapshot=activity_store().snapshot()
    rows=[]
    active_profile=read_webui_config().get("active_profile","default")
    for account in list_accounts(False)["accounts"]:
        key=account["account_key"]
        rows.append({"id":key,"kind":"account","name":account.get("alias") or account.get("email") or account.get("account_name") or key,
                     "usage":account.get("usage"),
                     "ping_available":True,"auth_active":bool(account.get("active")),
                     "active":bool(account.get("active")) and active_profile=="default",**snapshot["records"].get("account:"+key,{})})
    for profile in list_profiles():
        if profile.get("kind")=="api":
            rows.append({"id":profile["id"],"kind":"api","name":profile["name"],"ping_available":False,"active":active_profile==profile["id"],
                         **snapshot["records"].get("api:"+profile["id"],{})})
    return {"tracking_since":snapshot["tracking_since"],"accounts":rows}


def profiles_dir() -> Path:
    return storage_root()/"profiles"


def active_provider_env_path(home: Path | None = None) -> Path:
    return storage_root()/"active-provider.env"

def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.chmod(0o700)

def atomic_write(path: Path,data: bytes,mode: int = 0o600) -> None:
    ensure_private_dir(path.parent)
    temp=path.with_name(path.name+f".tmp-{os.getpid()}-{threading.get_ident()}")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temp.chmod(mode)
        os.replace(temp,path)
    finally:
        try: temp.unlink()
        except FileNotFoundError: pass

def replace_bytes(path: Path,data: bytes | None) -> None:
    if data is None:
        try: path.unlink()
        except FileNotFoundError: pass
    else:
        atomic_write(path,data)

def validate_toml_bytes(data: bytes,source: str = "config.toml") -> dict:
    if tomllib is None:
        raise RuntimeError("TOML validation requires Python 3.11 or newer")
    try:
        doc=tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError,tomllib.TOMLDecodeError) as e:
        raise RuntimeError(f"Invalid TOML in {source}: {e}") from e
    if not isinstance(doc,dict): raise RuntimeError(f"Invalid TOML document in {source}")
    return doc

def toml_string(value: str) -> str:
    return json.dumps(value,ensure_ascii=False)

def systemd_env_value(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise RuntimeError("API key contains an invalid newline or NUL character")
    return '"'+value.replace("\\","\\\\").replace('"','\\"')+'"'

def safe_profile_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}",value or ""):
        raise RuntimeError("Invalid profile id")
    return value

def profile_path(profile_id: str) -> Path:
    return profiles_dir()/safe_profile_id(profile_id)

def read_profile_manifest(profile_id: str) -> dict:
    path=profile_path(profile_id)/"manifest.json"
    try: data=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError) as e: raise RuntimeError(f"Could not read profile: {e}") from e
    if not isinstance(data,dict) or data.get("id")!=profile_id: raise RuntimeError("Invalid profile manifest")
    return data

def profile_summary(manifest: dict) -> dict:
    summary={k:manifest.get(k) for k in (
        "id","name","kind","model","base_url","usage_url","reasoning_effort","approval_policy","sandbox_mode","created_at","updated_at"
    )}
    if "usage_url" not in manifest and manifest.get("base_url")==API_BASE_URL:
        summary["usage_url"]=DEFAULT_USAGE_URL
    return summary

def provider_id_for_profile(profile_id: str) -> str:
    safe_profile_id(profile_id)
    return "webui_"+profile_id.replace("-","_")

def build_provider_lines(provider_id: str,name: str,base_url: str,headers: dict[str,str],token_path: Path) -> list[str]:
    if not re.fullmatch(r"[A-Za-z0-9_]{1,100}",provider_id): raise RuntimeError("Invalid provider id")
    lines=[
        f"[model_providers.{provider_id}]",
        f"name = {toml_string(name)}",
        f"base_url = {toml_string(base_url)}",
        'wire_api = "responses"',
        "supports_websockets = false",
    ]
    if headers:
        lines.extend(["",f"[model_providers.{provider_id}.http_headers]"])
        for key,value in sorted(headers.items()): lines.append(f"{toml_string(key)} = {toml_string(value)}")
    lines.extend([
        "",f"[model_providers.{provider_id}.auth]",
        f"command = {toml_string('/usr/bin/cat')}",
        f"args = [{toml_string(str(token_path))}]",
    ])
    return lines

def build_api_profile_config(name: str,base_url: str,model: str,reasoning: str,
                             approval: str,sandbox: str,headers: dict[str,str],catalog: str,
                             provider_id: str,token_path: Path) -> bytes:
    lines=[
        f"model_provider = {toml_string(provider_id)}",
    ]
    if model: lines.append(f"model = {toml_string(model)}")
    if reasoning: lines.append(f"model_reasoning_effort = {toml_string(reasoning)}")
    if approval: lines.append(f"approval_policy = {toml_string(approval)}")
    if sandbox: lines.append(f"sandbox_mode = {toml_string(sandbox)}")
    if catalog: lines.append(f"model_catalog_json = {toml_string(catalog)}")
    lines.extend(["",*build_provider_lines(provider_id,name,base_url,headers,token_path)])
    data=("\n".join(lines)+"\n").encode()
    validate_toml_bytes(data,"generated profile")
    return data

def validated_api_profile_fields(data: dict,existing_token: str | None = None) -> dict:
    name=str(data.get("name","")).strip()
    base_url=str(data.get("base_url","")).strip().rstrip("/")
    usage_url=str(data.get("usage_url",DEFAULT_USAGE_URL)).strip()
    model=str(data.get("model","")).strip()
    submitted_key=str(data.get("api_key","")).strip()
    api_key=submitted_key or existing_token or ""
    reasoning=str(data.get("reasoning_effort") or "").strip()
    approval=str(data.get("approval_policy") or "").strip()
    sandbox=str(data.get("sandbox_mode") or "").strip()
    catalog=str(data.get("model_catalog_json","")).strip()
    headers=data.get("http_headers") or {}
    if not name or len(name)>100: raise RuntimeError("Profile name is required and must be at most 100 characters")
    if base_url!=API_BASE_URL: raise RuntimeError(f"Base URL must be {API_BASE_URL}")
    if usage_url:
        parsed=urlparse(usage_url)
        if (parsed.scheme!="https" or parsed.hostname!="ttmapi.site" or parsed.port is not None
                or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith("/v1/")):
            raise RuntimeError("Usage URL must be an HTTPS URL under https://ttmapi.site/v1/")
    if len(model)>100: raise RuntimeError("Model must be at most 100 characters")
    if not api_key or len(api_key)>2048: raise RuntimeError("API key is required and must be at most 2048 characters")
    if reasoning and reasoning not in {"minimal","low","medium","high","xhigh"}: raise RuntimeError("Invalid reasoning effort")
    if approval and approval not in {"on-request","never"}: raise RuntimeError("Invalid approval policy")
    if sandbox and sandbox not in {"read-only","workspace-write","danger-full-access"}: raise RuntimeError("Invalid sandbox mode")
    if catalog:
        catalog_path=codex_home()/catalog
        if catalog_path.resolve().parent!=codex_home().resolve():raise RuntimeError("Model catalog must be a filename inside CODEX_HOME")
        if not catalog_path.is_file(): raise RuntimeError(f"Model catalog does not exist: {catalog}")
        catalog=str(catalog_path)
    if not isinstance(headers,dict) or len(headers)>20: raise RuntimeError("HTTP headers must be an object with at most 20 entries")
    clean_headers={}
    for key,value in headers.items():
        if not isinstance(key,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}",key): raise RuntimeError("Invalid HTTP header name")
        if not isinstance(value,str) or len(value)>500 or "\n" in value or "\r" in value: raise RuntimeError("Invalid HTTP header value")
        clean_headers[key]=value
    return {"name":name,"base_url":base_url,"usage_url":usage_url,"model":model,"api_key":api_key,
            "reasoning_effort":reasoning,"approval_policy":approval,"sandbox_mode":sandbox,
            "model_catalog_json":catalog,"http_headers":clean_headers}

def create_api_profile(data: dict) -> dict:
    fields=validated_api_profile_fields(data)
    name=fields["name"]
    profile_id=(re.sub(r"[^a-z0-9]+","-",name.casefold()).strip("-")[:42] or "profile")
    profile_id=f"{profile_id}-{uuid.uuid4().hex[:8]}"
    path=profile_path(profile_id)
    provider_id=provider_id_for_profile(profile_id);token_path=path/"token"
    config_data=build_api_profile_config(
        fields["name"],fields["base_url"],fields["model"],fields["reasoning_effort"],
        fields["approval_policy"],fields["sandbox_mode"],fields["http_headers"],
        fields["model_catalog_json"],provider_id,token_path)
    created=utc_now()
    manifest={
        "schema_version":1,"id":profile_id,"name":fields["name"],"kind":"api","model":fields["model"],
        "base_url":fields["base_url"],"usage_url":fields["usage_url"],"reasoning_effort":fields["reasoning_effort"],
        "approval_policy":fields["approval_policy"],"sandbox_mode":fields["sandbox_mode"],
        "model_catalog_json":fields["model_catalog_json"],"http_headers":fields["http_headers"],"provider_id":provider_id,
        "created_at":created,"updated_at":created,"has_secret":True,
    }
    with CONFIG_OPERATION_LOCK:
        ensure_private_dir(storage_root())
        ensure_private_dir(profiles_dir())
        path.mkdir(mode=0o700)
        try:
            atomic_write(path/"config.toml",config_data)
            atomic_write(token_path,(fields["api_key"]+"\n").encode())
            atomic_write(path/"secret.env",f"CODEX_PROFILE_API_KEY={systemd_env_value(fields['api_key'])}\n".encode())
            atomic_write(path/"manifest.json",(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n").encode())
        except BaseException:
            shutil.rmtree(path,ignore_errors=True)
            raise
    return profile_summary(manifest)

def config_operation(function):
    @functools.wraps(function)
    def locked(*args,**kwargs):
        with CONFIG_OPERATION_LOCK:return function(*args,**kwargs)
    return locked

@config_operation
def profile_details(profile_id: str) -> dict:
    material=api_profile_material(profile_id);manifest=material["manifest"]
    catalog=manifest.get("model_catalog_json","") or ""
    return {**profile_summary(manifest),"model_catalog_json":Path(catalog).name if catalog else "",
            "http_headers":manifest.get("http_headers",{}) or {},"has_secret":bool(material["token"])}

class NoUsageRedirects(HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,new_url):
        return None

@config_operation
def fetch_api_profile_usage(profile_id: str) -> dict:
    material=api_profile_material(profile_id);manifest=material["manifest"]
    url=str(profile_summary(manifest).get("usage_url") or "")
    if not url: raise RuntimeError("Set a Usage URL for this API account first")
    parsed=urlparse(url)
    if (parsed.scheme!="https" or parsed.hostname!="ttmapi.site" or parsed.port is not None
            or parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith("/v1/")):
        raise RuntimeError("Stored Usage URL is not allowed")
    request=Request(url,headers={"Authorization":"Bearer "+material["token"],"Accept":"application/json",
                                 "User-Agent":f"CodexAccountManager/{APP_VERSION}"})
    try:
        with build_opener(NoUsageRedirects).open(request,timeout=15) as response:
            body=response.read(1024*1024+1)
    except HTTPError as error:
        raise RuntimeError(f"Usage API returned HTTP {error.code}") from None
    except (URLError,TimeoutError) as error:
        raise RuntimeError(f"Usage API request failed: {error.reason if isinstance(error,URLError) else 'timed out'}") from None
    if len(body)>1024*1024:raise RuntimeError("Usage API response is too large")
    try:result=json.loads(body)
    except (UnicodeDecodeError,json.JSONDecodeError):raise RuntimeError("Usage API returned invalid JSON") from None
    if not isinstance(result,dict):raise RuntimeError("Usage API returned an unexpected response")
    return result

def update_api_profile(profile_id: str,data: dict) -> dict:
    with CONFIG_OPERATION_LOCK:
        material=api_profile_material(profile_id);old_manifest=material["manifest"]
        fields=validated_api_profile_fields(data,material["token"])
        path=profile_path(profile_id);provider_id=str(old_manifest["provider_id"])
        new_config=build_api_profile_config(
            fields["name"],fields["base_url"],fields["model"],fields["reasoning_effort"],
            fields["approval_policy"],fields["sandbox_mode"],fields["http_headers"],
            fields["model_catalog_json"],provider_id,path/"token")
        manifest={**old_manifest,**{key:value for key,value in fields.items() if key!="api_key"},
                  "updated_at":utc_now()}
        paths=(path/"config.toml",path/"manifest.json",path/"token",path/"secret.env")
        previous={item:item.read_bytes() if item.exists() else None for item in paths}
        active_config_path=codex_home()/"config.toml"
        previous_active_config=active_config_path.read_bytes() if active_config_path.exists() else None
        try:
            atomic_write(path/"config.toml",new_config)
            atomic_write(path/"manifest.json",(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n").encode())
            if fields["api_key"]!=material["token"]:
                atomic_write(path/"token",(fields["api_key"]+"\n").encode())
                atomic_write(path/"secret.env",f"CODEX_PROFILE_API_KEY={systemd_env_value(fields['api_key'])}\n".encode())
            if read_webui_config().get("active_profile")==profile_id:
                apply_profile(profile_id,restart_after_switch_enabled())
            else:
                patched=patch_provider_catalog(previous_active_config,all_api_profile_materials())
                if patched!=previous_active_config:
                    atomic_write(active_config_path,patched)
        except BaseException:
            for item,content in previous.items():replace_bytes(item,content)
            if active_config_path.exists() and active_config_path.read_bytes()!=previous_active_config:
                replace_bytes(active_config_path,previous_active_config)
            raise
    return {"profile":profile_summary(manifest),"message":"API account updated."}

def list_profiles() -> list[dict]:
    result=[{"id":"default","name":"Default Codex config","kind":"default"}]
    root=profiles_dir()
    if root.is_dir():
        for path in sorted(root.iterdir()):
            if not path.is_dir(): continue
            try: result.append(profile_summary(read_profile_manifest(path.name)))
            except RuntimeError: continue
    return result

def read_profile_token(path: Path) -> str:
    token_path=path/"token"
    try:
        token=token_path.read_text(encoding="utf-8").rstrip("\r\n")
        if token:return token
    except FileNotFoundError:pass
    except OSError as e:raise RuntimeError(f"Could not read profile token: {e}") from e
    try:
        line=next((line for line in (path/"secret.env").read_text(encoding="utf-8").splitlines()
                   if line.startswith("CODEX_PROFILE_API_KEY=")),"")
    except OSError as e:raise RuntimeError(f"Could not read legacy profile secret: {e}") from e
    raw=line.partition("=")[2].strip()
    try:parts=shlex.split(raw)
    except ValueError as e:raise RuntimeError(f"Could not decode legacy profile secret: {e}") from e
    token=parts[0] if len(parts)==1 else ""
    if not token:raise RuntimeError("Profile secret is missing")
    atomic_write(token_path,(token+"\n").encode())
    return token

@config_operation
def api_profile_material(profile_id: str) -> dict:
    manifest=read_profile_manifest(profile_id);path=profile_path(profile_id)
    try:stored_config=(path/"config.toml").read_bytes()
    except OSError as e:raise RuntimeError(f"Could not read profile config: {e}") from e
    document=validate_toml_bytes(stored_config,f"profile {manifest.get('name',profile_id)}")
    old_provider_id=document.get("model_provider")
    providers=document.get("model_providers",{})
    provider=providers.get(old_provider_id,{}) if isinstance(providers,dict) and isinstance(old_provider_id,str) else {}
    if not isinstance(provider,dict):provider={}
    token=read_profile_token(path);token_path=path/"token"
    provider_id=str(manifest.get("provider_id") or provider_id_for_profile(profile_id))
    headers=manifest.get("http_headers",provider.get("http_headers",{}))
    if not isinstance(headers,dict):headers={}
    clean_headers={str(key):str(value) for key,value in headers.items()}
    catalog=str(manifest.get("model_catalog_json",document.get("model_catalog_json","") or ""))
    updates={
        "provider_id":provider_id,"model_catalog_json":catalog,"http_headers":clean_headers,
        "legacy_provider_compat":manifest.get("legacy_provider_compat") is True or old_provider_id=="webui_profile",
    }
    changed=any(manifest.get(key)!=value for key,value in updates.items())
    manifest.update(updates)
    rebuilt=build_api_profile_config(
        str(manifest.get("name") or provider.get("name") or profile_id),
        str(manifest.get("base_url") or provider.get("base_url") or ""),
        str(manifest.get("model",document.get("model","") or "")),
        str(manifest.get("reasoning_effort",document.get("model_reasoning_effort","") or "")),
        str(manifest.get("approval_policy",document.get("approval_policy","") or "")),
        str(manifest.get("sandbox_mode",document.get("sandbox_mode","") or "")),
        clean_headers,catalog,provider_id,token_path,
    )
    if changed or rebuilt!=stored_config:
        atomic_write(path/"config.toml",rebuilt)
        atomic_write(path/"manifest.json",(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n").encode())
    return {"manifest":manifest,"token_path":token_path,"token":token}

@config_operation
def all_api_profile_materials() -> list[dict]:
    materials=[]
    for summary in list_profiles()[1:]:materials.append(api_profile_material(str(summary["id"])))
    return materials

def set_active_profile_state(profile_id: str,overlay: dict | None = None) -> None:
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config();config["active_profile"]=profile_id
        config["managed_config_overlay"]=overlay or {}
        write_webui_config(config)

def strip_managed_provider_block(text: str) -> str:
    pattern=re.compile(r"(?:^|\n)"+re.escape(MANAGED_PROVIDER_BEGIN)+r"\n.*?\n"+re.escape(MANAGED_PROVIDER_END)+r"(?:\n|$)",re.S)
    return pattern.sub("\n",text)

def strip_provider_sections(text: str,provider_ids: set[str]) -> str:
    lines=text.splitlines(keepends=True);out=[];skip=False
    for line in lines:
        match=re.match(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$",line.rstrip("\r\n"))
        if match:
            table=match.group(1).strip()
            skip=any(table==f"model_providers.{provider_id}" or table.startswith(f"model_providers.{provider_id}.")
                     for provider_id in provider_ids)
        if not skip:out.append(line)
    return "".join(out)

def patch_top_level_settings(text: str,settings: dict[str,str | None]) -> str:
    lines=text.splitlines(keepends=True)
    first_table=next((index for index,line in enumerate(lines) if re.match(r"^\s*\[",line)),len(lines))
    prefix=lines[:first_table];rest=lines[first_table:];out=[];written=set()
    for line in prefix:
        match=re.match(r"^\s*([A-Za-z0-9_-]+)\s*=",line)
        key=match.group(1) if match else None
        if key not in settings:
            out.append(line);continue
        if key in written:continue
        written.add(key);value=settings[key]
        if value is not None:out.append(f"{key} = {toml_string(value)}\n")
    missing=[key for key in MANAGED_TOP_LEVEL_KEYS if key in settings and key not in written and settings[key] is not None]
    if missing:
        if out and out[-1].strip():out.append("\n")
        out.extend(f"{key} = {toml_string(settings[key])}\n" for key in missing)
        if rest:out.append("\n")
    return "".join(out+rest)

def material_settings(material: dict) -> dict[str,str | None]:
    manifest=material["manifest"]
    return {
        "model_provider":str(manifest["provider_id"]),
        "model":str(manifest.get("model") or "") or None,
        "model_reasoning_effort":str(manifest.get("reasoning_effort") or "") or None,
        "approval_policy":str(manifest.get("approval_policy") or "") or None,
        "sandbox_mode":str(manifest.get("sandbox_mode") or "") or None,
        "model_catalog_json":str(manifest.get("model_catalog_json") or "") or None,
    }

def top_level_settings_from_data(data: bytes | None,source: str) -> dict[str,str | None]:
    document=validate_toml_bytes(data,source) if data is not None else {}
    return {key:(str(document[key]) if key in document and isinstance(document[key],str) else None)
            for key in MANAGED_TOP_LEVEL_KEYS}

def managed_provider_block(materials: list[dict]) -> str:
    if not materials:return ""
    lines=[MANAGED_PROVIDER_BEGIN]
    for material in materials:
        manifest=material["manifest"]
        lines.extend(["",*build_provider_lines(
            str(manifest["provider_id"]),str(manifest.get("name") or manifest["id"]),
            str(manifest.get("base_url") or ""),dict(manifest.get("http_headers") or {}),material["token_path"],
        )])
    legacy=next((material for material in materials if material["manifest"].get("legacy_provider_compat") is True),None)
    if legacy:
        manifest=legacy["manifest"]
        lines.extend(["",*build_provider_lines(
            "webui_profile",str(manifest.get("name") or manifest["id"]),str(manifest.get("base_url") or ""),
            dict(manifest.get("http_headers") or {}),legacy["token_path"],
        )])
    lines.extend(["",MANAGED_PROVIDER_END])
    return "\n".join(lines)+"\n"

def patch_provider_catalog(data: bytes | None,materials: list[dict]) -> bytes:
    try:text=(data or b"").decode("utf-8")
    except UnicodeDecodeError as e:raise RuntimeError(f"Current config.toml is not UTF-8: {e}") from e
    text=strip_managed_provider_block(text)
    provider_ids={"webui_profile",*(str(item["manifest"]["provider_id"]) for item in materials)}
    text=strip_provider_sections(text,provider_ids)
    block=managed_provider_block(materials)
    if block:text=text.rstrip()+"\n\n"+block
    result=text.encode()
    validate_toml_bytes(result,"patched config.toml")
    return result

def build_active_config(profile_id: str,current_data: bytes | None,previous_overlay: dict | None = None) -> tuple[bytes,dict]:
    materials=all_api_profile_materials();by_id={item["manifest"]["id"]:item for item in materials}
    if profile_id!="default" and profile_id not in by_id:raise RuntimeError("Profile was not found")
    data=patch_provider_catalog(current_data,materials)
    text=data.decode("utf-8")
    current=top_level_settings_from_data(data,"current config.toml")
    revert={}
    if isinstance(previous_overlay,dict):
        for key,item in previous_overlay.items():
            if key in MANAGED_TOP_LEVEL_KEYS and isinstance(item,dict) and isinstance(item.get("applied"),str):
                before=item.get("before")
                if (before is None or isinstance(before,str)) and current.get(key)==item["applied"]:
                    revert[key]=before
    if revert:
        text=patch_top_level_settings(text,revert)
        current=top_level_settings_from_data(text.encode(),"reverted config.toml")
    new_overlay={}
    if profile_id=="default":
        managed_provider_ids={"webui_profile",*(str(item["manifest"]["provider_id"]) for item in materials)}
        if current.get("model_provider") in managed_provider_ids:
            text=patch_top_level_settings(text,{"model_provider":None})
    else:
        desired={key:value for key,value in material_settings(by_id[profile_id]).items() if value is not None}
        changes={}
        for key,value in desired.items():
            if current.get(key)!=value:
                new_overlay[key]={"before":current.get(key),"applied":value}
                changes[key]=value
        if changes:text=patch_top_level_settings(text,changes)
    result=text.encode();validate_toml_bytes(result,"patched config.toml")
    return result,new_overlay

def restore_profile_state(previous: dict) -> None:
    with WEBUI_CONFIG_LOCK:
        current=read_webui_config()
        for key in ("active_profile","managed_config_overlay"):
            if key in previous:current[key]=previous[key]
            else:current.pop(key,None)
        write_webui_config(current)

def apply_profile(profile_id: str,restart: bool = True,
                  services: tuple[str,...] = (CODEX_APP_SERVICE,)) -> dict:
    config_path=codex_home()/"config.toml"
    with CONFIG_OPERATION_LOCK:
        previous_config=config_path.read_bytes() if config_path.exists() else None
        previous_state=read_webui_config()
        previous_overlay=previous_state.get("managed_config_overlay")
        if not isinstance(previous_overlay,dict):previous_overlay={}
        config_data,new_overlay=build_active_config(profile_id,previous_config,previous_overlay)
        replace_bytes(config_path,config_data)
        try:
            result=restart_services(services) if restart else {"message":"Profile staged; restart is disabled.","output":""}
            set_active_profile_state(profile_id,new_overlay)
        except BaseException as e:
            replace_bytes(config_path,previous_config);restore_profile_state(previous_state)
            rollback_output=""
            try: rollback_output=restart_services(services)["output"] if restart else ""
            except Exception as rollback_error: rollback_output=getattr(rollback_error,"output",str(rollback_error))
            output=getattr(e,"output","")
            if not isinstance(e,Exception):raise
            raise ServiceCommandError(f"Profile activation failed and was rolled back: {e}",output+"\n\n--- rollback restart ---\n"+rollback_output) from e
    name="Default Codex config" if profile_id=="default" else str(read_profile_manifest(profile_id).get("name",profile_id))
    return {"message":f"Activated profile {name}. "+result["message"],"output":result["output"]}

def apply_account_profile(profile_id: str) -> dict:
    identifier=profile_id
    kind="api"
    if profile_id=="default":
        kind="account"
        identifier=account_store().read().get("active_account_key")
    # Validate first; invalid profile IDs are not account operations.
    track=bool(identifier) and (kind=="account" or (profile_path(profile_id)/"manifest.json").is_file())
    started=time.monotonic()
    try:result=apply_profile(profile_id,restart_after_switch_enabled())
    except BaseException:
        if track:record_activity(kind,identifier,"switch",False,started)
        raise
    if track:record_activity(kind,identifier,"switch",True,started)
    return result

def delete_profile(profile_id: str) -> str:
    safe_profile_id(profile_id)
    with CONFIG_OPERATION_LOCK:
        config=read_webui_config()
        if config.get("active_profile")==profile_id: raise RuntimeError("Cannot delete the active profile; activate Default first")
        path=profile_path(profile_id)
        if not path.is_dir(): raise RuntimeError("Profile was not found")
        shutil.rmtree(path)
    return "Profile deleted."

@config_operation
def profiles_status() -> dict:
    ensure_private_dir(storage_root());ensure_private_dir(profiles_dir())
    config=read_webui_config()
    return {"profiles":list_profiles(),"active_profile":config.get("active_profile","default"),
            "config_file":str(codex_home()/"config.toml"),
            "provider_env_file":str(active_provider_env_path())}

def parse_profile_text(text: str) -> dict:
    if not isinstance(text,str) or not text.strip() or len(text)>60000: raise RuntimeError("Paste a config.toml snippet up to 60,000 characters")
    candidates=[]
    candidates.extend(match.group(1).strip() for match in re.finditer(r"```(?:toml)?\s*([\s\S]*?)```",text,re.I))
    candidates.append(text.strip())
    start=re.search(r"(?m)^\s*(?:model_catalog_json|model_provider|model)\s*=",text)
    if start: candidates.append(text[start.start():].strip())
    document=None
    for candidate in candidates:
        try:
            parsed=validate_toml_bytes(candidate.encode(),"pasted config")
            if "model" in parsed or "model_provider" in parsed:
                document=parsed;break
        except RuntimeError: continue
    if document is None: raise RuntimeError("No valid Codex TOML configuration was found in the pasted text")
    providers=document.get("model_providers",{})
    provider_id=document.get("model_provider")
    provider=providers.get(provider_id,{}) if isinstance(providers,dict) and isinstance(provider_id,str) else {}
    if not provider and isinstance(providers,dict) and len(providers)==1: provider_id,provider=next(iter(providers.items()))
    if not isinstance(provider,dict): provider={}
    token=provider.get("experimental_bearer_token","")
    headers=provider.get("http_headers",{})
    if not isinstance(headers,dict): headers={}
    catalog=document.get("model_catalog_json","")
    catalog_name=Path(str(catalog)).name if catalog else ""
    catalog_available=bool(catalog_name and (codex_home()/catalog_name).is_file())
    notes=[]
    if token: notes.append("The pasted direct bearer token was moved to a private token file.")
    else: notes.append("Parsed successfully. Enter the API key if it was not included in the text.")
    if catalog_name and not catalog_available: notes.append(f"Model catalog {catalog_name} was ignored because it is not present in CODEX_HOME.")
    return {
        "name":str(provider.get("name") or provider_id or "Imported API profile"),
        "base_url":str(provider.get("base_url", "")),"model":str(document.get("model", "")),
        "api_key":str(token) if isinstance(token,str) else "",
        "reasoning_effort":str(document.get("model_reasoning_effort","")),
        "approval_policy":str(document.get("approval_policy","")),
        "sandbox_mode":str(document.get("sandbox_mode","")),
        "model_catalog_json":catalog_name if catalog_available else "",
        "http_headers":{str(k):str(v) for k,v in headers.items()},
        "warning":" ".join(notes),
    }

def restart_after_switch_enabled() -> bool:
    value=read_webui_config().get("restart_after_switch",False)
    return value if isinstance(value,bool) else False

def set_restart_after_switch(enabled: bool) -> str:
    if not isinstance(enabled,bool): raise RuntimeError("enabled must be a boolean")
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        config["restart_after_switch"]=enabled
        write_webui_config(config)
    return f"Restart after account switch {'enabled' if enabled else 'disabled'}."

def auto_ping_enabled() -> bool:
    return read_webui_config().get("auto_ping_enabled") is True

def set_auto_ping(enabled: bool) -> str:
    if not isinstance(enabled,bool): raise RuntimeError("enabled must be a boolean")
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        config["auto_ping_enabled"]=enabled
        if not enabled:config["auto_ping_state"]={}
        write_webui_config(config)
    AUTO_PING_WAKE.set()
    suffix="Selected accounts will ping once after each 5-hour reset." if enabled else "All account Auto Ping workers are paused."
    return f"Auto Ping {'enabled' if enabled else 'disabled'}. {suffix}"

def set_account_auto_ping(query: str,enabled: bool) -> str:
    if not isinstance(enabled,bool): raise RuntimeError("enabled must be a boolean")
    if not query or len(query)>300: raise RuntimeError("Invalid account selector")
    account=find_account_record(query)
    canonical=account_selector(account)
    label=str(account.get("alias") or account.get("email") or account.get("account_name") or query)
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        accounts=config.get("auto_ping_accounts",{})
        if not isinstance(accounts,dict): accounts={}
        for selector in account_selectors(account):accounts.pop(selector,None)
        if enabled:accounts[canonical]=True
        config["auto_ping_accounts"]=accounts
        if not enabled:
            state=config.get("auto_ping_state",{})
            if isinstance(state,dict):
                for selector in account_selectors(account):state.pop(selector,None)
        write_webui_config(config)
    AUTO_PING_WAKE.set()
    return f"Auto Ping {'enabled' if enabled else 'disabled'} for {label}."

def set_account_alias(selector: str,alias: str | None,previous_key: str = "") -> str:
    selector=str(selector).strip()
    previous_key=str(previous_key).strip()
    if not selector or len(selector)>300: raise RuntimeError("Invalid account selector")
    canonical=account_selector(find_account_record(selector))
    if alias is not None:
        if not isinstance(alias,str) or not alias.strip(): raise RuntimeError("Alias cannot be empty")
    message=account_store().set_alias(canonical,alias)

    if previous_key and previous_key!=canonical:
        with WEBUI_CONFIG_LOCK:
            config=read_webui_config()
            changed=False
            for section in ("auto_ping_accounts","auto_ping_state"):
                values=config.get(section,{})
                if isinstance(values,dict) and previous_key in values:
                    previous=values.pop(previous_key)
                    if section=="auto_ping_state" and isinstance(previous,dict):
                        current=values.get(canonical,{})
                        if not isinstance(current,dict):current={}
                        previous.update(current);values[canonical]=previous
                    elif canonical not in values:values[canonical]=previous
                    config[section]=values;changed=True
            if changed:write_webui_config(config)
        AUTO_PING_WAKE.set()
    action=f"Alias set to {alias}." if alias is not None else "Alias cleared."
    return message

def account_store() -> AccountStore:
    return AccountStore(codex_home(),storage_root()/"vault")

LOGIN_MANAGER=LoginManager(account_store)

def account_manager_version() -> str:
    return "Native account manager " + APP_VERSION


def list_accounts(refresh: bool) -> dict:
    return account_store().list(refresh,cancel_event=getattr(AUTO_PING_CONTEXT,"stop",None))

def account_selector(account: dict) -> str:
    """Return the most stable available account identifier."""
    for key in ("account_key","email","alias","account_name","number"):
        value=account.get(key)
        if value is not None and str(value).strip(): return str(value).strip()
    return ""

def account_selectors(account: dict) -> list[str]:
    selectors=[]
    for key in ("account_key","email","alias","account_name","number"):
        value=account.get(key)
        if value is None:continue
        value=str(value).strip()
        if value and value not in selectors:selectors.append(value)
    return selectors


def usage_window_value(account: dict,window_minutes: int,names: tuple[str,...]):
    """Find a usage window by duration, with compatibility fallbacks for old text output."""
    usage=account.get("usage",{})
    if not isinstance(usage,dict):return None
    for value in usage.values():
        if not isinstance(value,dict):continue
        try:minutes=int(value.get("window_minutes"))
        except (TypeError,ValueError):continue
        if minutes==window_minutes:return value
    for name in names:
        if name not in usage:continue
        value=usage[name]
        if isinstance(value,dict) and value.get("window_minutes") is not None:
            try:
                if int(value["window_minutes"])!=window_minutes:continue
            except (TypeError,ValueError):continue
        return value
    return None

def five_hour_usage(account: dict):
    return usage_window_value(account,300,("five_hour","5h","primary","session","session_5h"))

def weekly_usage(account: dict):
    return usage_window_value(account,10080,("weekly","7d","secondary","week"))


def remaining_usage_percent(value) -> float | None:
    """Normalize saved usage windows for the human-readable CLI list."""
    if isinstance(value,dict):
        for key in ("remaining_percent","remaining_pct","percent_remaining","remaining","value"):
            if key in value:return remaining_usage_percent(value[key])
        if "used_percent" in value:
            used=remaining_usage_percent(value["used_percent"])
            return 100-used if used is not None else None
        return None
    if isinstance(value,(int,float)) and not isinstance(value,bool):return float(value)
    if isinstance(value,str):
        match=re.search(r"(-?\d+(?:\.\d+)?)\s*%",value)
        if match:return float(match.group(1))
    return None

def raw_used_percent(value) -> float | None:
    if not isinstance(value,dict):return None
    used=value.get("used_percent")
    if isinstance(used,(int,float)) and not isinstance(used,bool):return float(used)
    try:return float(used) if isinstance(used,str) and used.strip() else None
    except ValueError:return None

def usage_window_difference_seconds(account: dict,value) -> float | None:
    usage=account.get("usage",{})
    if not isinstance(usage,dict) or not isinstance(value,dict):return None
    try:
        updated_at=float(usage["updated_at"])
        resets_at=float(value["resets_at"])
        window_seconds=float(value["window_minutes"])*60
    except (KeyError,TypeError,ValueError):return None
    return resets_at-updated_at-window_seconds

def auto_ping_json_eligible(account: dict) -> bool:
    five=five_hour_usage(account);weekly=weekly_usage(account)
    difference=usage_window_difference_seconds(account,five)
    five_used=raw_used_percent(five);weekly_used=raw_used_percent(weekly)
    return (five_used==0 and weekly_used is not None and weekly_used<100 and difference is not None
            and -AUTO_PING_TIMING_TOLERANCE_SECONDS<=difference<=0)

def account_usage_reset_passed(account: dict,now: datetime) -> bool:
    for value in (five_hour_usage(account),weekly_usage(account)):
        reset=parse_reset_time(value,now)
        if reset is not None and reset<=now:return True
    return False

def auto_ping_attempt_ready(state: dict,now: datetime) -> bool:
    last_ping=parse_reset_time(state.get("last_ping_at"),now)
    if last_ping and now-last_ping<timedelta(hours=5):return False
    last_attempt=parse_reset_time(state.get("last_attempt_at"),now)
    try:attempts=int(state.get("attempt_count",0))
    except (TypeError,ValueError):attempts=0
    if last_attempt is None or now-last_attempt>=timedelta(hours=5):attempts=0
    if attempts>=AUTO_PING_MAX_ATTEMPTS:return False
    retry_at=parse_reset_time(state.get("next_retry_at"),now)
    return retry_at is None or retry_at<=now

def parse_reset_time(value,now: datetime | None = None) -> datetime | None:
    now=now or datetime.now(timezone.utc)
    if isinstance(value,dict):
        for key in ("resets_at","reset_at","reset_time","reset"):
            if key in value:return parse_reset_time(value[key],now)
        return None
    if isinstance(value,(int,float)):
        timestamp=float(value)/(1000 if value>1_000_000_000_000 else 1)
        try:return datetime.fromtimestamp(timestamp,timezone.utc)
        except (ValueError,OSError,OverflowError):return None
    if not isinstance(value,str):return None
    raw=value.strip()
    iso_match=re.search(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:?\d\d)?",raw)
    if iso_match:
        iso=iso_match.group(0).replace("Z","+00:00")
        try:
            parsed=datetime.fromisoformat(iso)
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
        except ValueError:pass
    match=re.search(r"(\d{1,2}):(\d{2})(?:\s+on\s+(\d{1,2})\s+([A-Za-z]{3}))?",raw,re.I)
    if not match:return None
    hour,minute=int(match.group(1)),int(match.group(2))
    if hour>23 or minute>59:return None
    if match.group(3):
        months={name.lower():index for index,name in enumerate(("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"),1)}
        month=months.get(match.group(4).lower())
        if not month:return None
        try:parsed=datetime(now.year,month,int(match.group(3)),hour,minute,tzinfo=timezone.utc)
        except ValueError:return None
        if parsed<now-timedelta(days=183):parsed=parsed.replace(year=parsed.year+1)
        return parsed
    parsed=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if parsed<now-timedelta(minutes=5):parsed+=timedelta(days=1)
    return parsed

def auto_ping_snapshot() -> dict:
    config=read_webui_config()
    accounts=config.get("auto_ping_accounts",{})
    state=config.get("auto_ping_state",{})
    return {
        "enabled":config.get("auto_ping_enabled") is True,
        "accounts":accounts if isinstance(accounts,dict) else {},
        "state":state if isinstance(state,dict) else {},
    }

def migrate_auto_ping_account_keys(current_accounts: list[dict]) -> bool:
    """Move legacy email/alias keys to the stable account_key."""
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        enabled=config.get("auto_ping_accounts",{})
        state=config.get("auto_ping_state",{})
        if not isinstance(enabled,dict):enabled={}
        if not isinstance(state,dict):state={}
        original_enabled=dict(enabled);original_state=dict(state)
        for account in current_accounts:
            canonical=account_selector(account)
            if not canonical:continue
            selectors=account_selectors(account)
            legacy=[item for item in selectors if item!=canonical]

            if canonical not in enabled and any(enabled.get(item) is True for item in legacy):
                enabled[canonical]=True
            for item in legacy:enabled.pop(item,None)

            merged={}
            for item in legacy:
                value=state.pop(item,None)
                if isinstance(value,dict):merged.update(value)
            current=state.get(canonical)
            if isinstance(current,dict):merged.update(current)
            if merged:state[canonical]=merged
        changed=enabled!=original_enabled or state!=original_state
        if changed:
            config["auto_ping_accounts"]=enabled
            config["auto_ping_state"]=state
            write_webui_config(config)
        return changed

def decorate_accounts_for_auto_ping(accounts: list[dict]) -> dict:
    migrate_auto_ping_account_keys(accounts)
    snapshot=auto_ping_snapshot()
    for account in accounts:
        selector=account_selector(account)
        selected=snapshot["accounts"].get(selector) is True
        state=snapshot["state"].get(selector,{})
        if not isinstance(state,dict):state={}
        account["auto_ping_configured"]=selected
        account["auto_ping_effective"]=snapshot["enabled"] and selected
        account["auto_ping_next_reset_at"]=state.get("next_reset_at")
        account["auto_ping_last_ping_at"]=state.get("last_ping_at")
        account["auto_ping_last_result"]=state.get("last_result")
        account["auto_ping_last_error"]=state.get("last_error")
        account["auto_ping_next_retry_at"]=state.get("next_retry_at")
    return snapshot

def update_auto_ping_state(selector: str,values: dict) -> None:
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config()
        state=config.get("auto_ping_state",{})
        if not isinstance(state,dict):state={}
        current=state.get(selector,{})
        if not isinstance(current,dict):current={}
        current.update(values);state[selector]=current;config["auto_ping_state"]=state
        write_webui_config(config)

def auto_ping_worker_once(now: datetime | None = None) -> dict:
    with FileRLock(lambda: storage_root()/".auto-ping.lock"):
        return _auto_ping_worker_once(now)

def _auto_ping_worker_once(now: datetime | None = None) -> dict:
    stop=getattr(AUTO_PING_CONTEXT,"stop",None)
    if stop is not None and stop.is_set():return {"pinged":[],"candidates":[],"refreshed":False}
    now=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    snapshot=auto_ping_snapshot()
    if not snapshot["enabled"]:return {"pinged":[],"candidates":[],"refreshed":False}
    selected=[selector for selector,enabled in snapshot["accounts"].items() if enabled is True]
    if not selected:return {"pinged":[],"candidates":[],"refreshed":False}

    # Poll only the native local JSON cache. A live API refresh is allowed
    # only after the cached standard fields identify at least one candidate.
    cached_result=list_accounts(False)
    migrate_auto_ping_account_keys(cached_result.get("accounts",[]))
    snapshot=auto_ping_snapshot()
    selected=[selector for selector,enabled in snapshot["accounts"].items() if enabled is True]
    selected_set=set(selected)
    cached_accounts={account_selector(account):account for account in cached_result.get("accounts",[])
                     if account_selector(account) in selected_set}
    candidates=[]
    refresh_due=False
    observed_at=now.isoformat(timespec="seconds")
    for selector,account in cached_accounts.items():
        if account_usage_reset_passed(account,now):refresh_due=True
        reset=parse_reset_time(five_hour_usage(account),now)
        update_auto_ping_state(selector,{"next_reset_at":reset.isoformat(timespec="seconds") if reset else None,
                                         "observed_at":observed_at})
        state=snapshot["state"].get(selector,{})
        if not isinstance(state,dict):state={}
        if auto_ping_json_eligible(account) and auto_ping_attempt_ready(state,now):candidates.append(selector)
    if not candidates and not refresh_due:return {"pinged":[],"candidates":[],"refreshed":False}

    # Refresh once when a selected account crossed either reset, or when a
    # cached candidate needs live API confirmation before a ping.
    live_result=list_accounts(True)
    stop=getattr(AUTO_PING_CONTEXT,"stop",None)
    if stop is not None and stop.is_set():return {"pinged":[],"candidates":[],"refreshed":True}
    migrate_auto_ping_account_keys(live_result.get("accounts",[]))
    live_accounts={account_selector(account):account for account in live_result.get("accounts",[])}
    check_selectors=[selector for selector in selected if selector in live_accounts] if refresh_due else candidates
    confirmed_candidates=[]
    pinged=[]
    for selector in check_selectors:
        if stop is not None and stop.is_set():break
        account=live_accounts.get(selector)
        state=auto_ping_snapshot()["state"].get(selector,{})
        if not isinstance(state,dict):state={}
        if account is not None:
            reset=parse_reset_time(five_hour_usage(account),now)
            update_auto_ping_state(selector,{"next_reset_at":reset.isoformat(timespec="seconds") if reset else None,
                                             "observed_at":observed_at})
        if account is None or not auto_ping_json_eligible(account) or not auto_ping_attempt_ready(state,now):continue
        confirmed_candidates.append(selector)
        last_attempt=parse_reset_time(state.get("last_attempt_at"),now)
        try:attempts=int(state.get("attempt_count",0))
        except (TypeError,ValueError):attempts=0
        if last_attempt is None or now-last_attempt>=timedelta(hours=5):attempts=0
        attempt=attempts+1
        update_auto_ping_state(selector,{"last_attempt_at":observed_at,"attempt_count":attempt,
                                         "last_result":"running","last_error":None,"next_retry_at":None})
        try:
            message=run_account_ping(selector)
            update_auto_ping_state(selector,{"last_ping_at":observed_at,"attempt_count":0,"last_result":"ok",
                                             "last_message":message,"last_error":None,"next_retry_at":None})
            pinged.append(selector)
        except Exception as error:
            retry_time=(now+AUTO_PING_RETRY_DELAY).isoformat(timespec="seconds") if attempt<AUTO_PING_MAX_ATTEMPTS else None
            update_auto_ping_state(selector,{"last_result":"error","last_error":str(error)[:1000],
                                             "next_retry_at":retry_time})
    return {"pinged":pinged,"candidates":confirmed_candidates,"refreshed":True}

def auto_ping_worker_loop(interval_seconds: int = 60) -> None:
    AUTO_PING_CONTEXT.stop=AUTO_PING_STOP
    try:
        while not AUTO_PING_STOP.is_set():
            try:auto_ping_worker_once()
            except Exception as error:
                if AUTO_PING_STOP.is_set():break
                try:
                    with WEBUI_CONFIG_LOCK:
                        config=read_webui_config();config["auto_ping_worker_error"]={"at":utc_now(),"message":str(error)[:1000]};write_webui_config(config)
                except Exception:pass
            AUTO_PING_WAKE.wait(interval_seconds);AUTO_PING_WAKE.clear()
    finally:
        del AUTO_PING_CONTEXT.stop

def do_switch(query: str) -> dict:
    started=time.monotonic();target=None
    if not query or len(query)>300: raise RuntimeError("Invalid account selector")
    restart=restart_after_switch_enabled()
    services=(CODEX_APP_SERVICE,)
    # Restore auth and config before restarting the previous session on failure.
    with CONFIG_OPERATION_LOCK:
        config_path=codex_home()/"config.toml"
        previous_config=config_path.read_bytes() if config_path.exists() else None
        previous_state=read_webui_config()
        restart_attempted=False
        try:
            with account_store().switching(query) as target:
                profile_result=apply_profile("default",False,services)
                if restart:
                    restart_attempted=True
                    service_result=restart_services(services)
                    profile_result["message"]=service_result["message"]
                    profile_result["output"]=service_result["output"]
        except BaseException as error:
            if target:record_activity("account",target["account_key"],"switch",False,started)
            replace_bytes(config_path,previous_config)
            restore_profile_state(previous_state)
            rollback_output=""
            if restart_attempted:
                try:rollback_output=restart_services(services)["output"]
                except Exception as rollback_error:rollback_output=getattr(rollback_error,"output",str(rollback_error))
            if not isinstance(error,Exception):raise
            raise ServiceCommandError(f"Account switch failed and was rolled back: {error}",
                                      getattr(error,"output","")+"\n"+rollback_output) from error
    record_activity("account",target["account_key"],"switch",True,started)
    label=target.get("alias") or target.get("email") or target.get("account_name") or query
    return {"message":f"Switched to {label}.\n"+profile_result["message"],"output":profile_result["output"]}

def get_status() -> dict:
    accounts=list_accounts(False)["accounts"]
    return {
        "restart_after_switch_enabled":restart_after_switch_enabled(),
        "auto_ping_enabled":auto_ping_enabled(),
        "webui":web_status(),
        "active_profile":read_webui_config().get("active_profile","default"),
        "active_account":next((account for account in accounts if account.get("active")),None),
        "account_count":len(accounts),
        "summary":account_manager_version(),
    }

def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home()/".codex")).expanduser()

def find_account_record(query: str, home: Path | None = None) -> dict:
    return (AccountStore(home,storage_root()/"vault") if home is not None else account_store()).find(query)


def execute_ping(args,env,stop=None):
    if stop is None:return subprocess.run(args,text=True,capture_output=True,timeout=180,env=env)
    with subprocess.Popen(args,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,start_new_session=True) as process:
        def terminate():
            if process.poll() is not None:return
            try:os.killpg(process.pid,signal.SIGTERM)
            except ProcessLookupError:return
            try:process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.communicate()
        deadline=time.monotonic()+180
        try:
            while True:
                if stop.is_set():raise RuntimeError("Auto Ping cancelled during shutdown")
                if time.monotonic()>=deadline:raise subprocess.TimeoutExpired(args,180)
                try:
                    stdout,stderr=process.communicate(timeout=.2)
                    return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
                except subprocess.TimeoutExpired:continue
        except BaseException:
            terminate()
            raise

def run_account_ping(query: str) -> str:
    target=account_store().find(query)["account_key"]
    started=time.monotonic()
    try:result=_run_account_ping(target)
    except BaseException:
        record_activity("account",target,"ping",False,started)
        raise
    record_activity("account",target,"ping",True,started)
    return result


def _run_account_ping(query: str) -> str:
    if shutil.which("codex") is None: raise RuntimeError("codex was not found on PATH")
    args=[
        "codex","exec","--ephemeral","--skip-git-repo-check",
        "-m","gpt-5.6-luna","-c",'model_reasoning_effort="none"',
        "--sandbox","read-only","Reply only with OK",
    ]
    with account_store().ping_credentials(query) as (document,label,save):
        with tempfile.TemporaryDirectory(prefix="codex-account-manager-ping-") as temp_dir:
            temp_home=Path(temp_dir)
            auth_file=temp_home/"auth.json"
            private_write(auth_file,json_bytes(document))
            env=os.environ.copy()
            env["CODEX_HOME"]=str(temp_home)
            try:
                cp=execute_ping(args,env,getattr(AUTO_PING_CONTEXT,"stop",None))
            finally:
                try:updated=json.loads(auth_file.read_text())
                except (OSError,ValueError):updated=None
                if updated is not None and updated!=document:save(updated)
    if cp.returncode!=0:
        error=cp.stderr.strip() or cp.stdout.strip() or "Codex account ping failed"
        raise RuntimeError(error)
    output=cp.stdout.strip()
    if output!="OK":
        raise RuntimeError(f"Unexpected Codex response: {output or '(empty output)'}")
    return f"Ping passed for {label}: OK. The active account was not changed."


def normalized_auth_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]","",str(key).lower())

def auth_field(document: dict,names: tuple[str,...]):
    wanted={normalized_auth_key(name) for name in names}
    for key,value in document.items():
        if normalized_auth_key(key) in wanted and value is not None:return value
    return None

def normalize_auth_document(document):
    """Convert common camelCase/snake_case auth exports to Codex standard auth JSON."""
    if isinstance(document,list):return [normalize_auth_document(item) for item in document]
    if not isinstance(document,dict):raise RuntimeError("Every imported account must be a JSON object")

    api_key=auth_field(document,("OPENAI_API_KEY","openaiApiKey","apiKey"))
    if isinstance(api_key,str) and api_key.strip():
        return {"auth_mode":"apikey","OPENAI_API_KEY":api_key.strip()}

    containers=[document]
    for name in ("tokens","token","auth","authentication","credentials"):
        value=auth_field(document,(name,))
        if isinstance(value,dict) and value not in containers:containers.insert(0,value)

    def token_value(*names: str):
        for container in containers:
            value=auth_field(container,tuple(names))
            if isinstance(value,str) and value.strip():return value.strip()
        return None

    id_token=token_value("id_token","idToken")
    access_token=token_value("access_token","accessToken")
    refresh_token=token_value("refresh_token","refreshToken")
    account_id=token_value("account_id","accountId","chatgpt_account_id","chatgptAccountId")
    last_refresh=token_value("last_refresh","lastRefresh")
    if not id_token and not access_token:
        # Let the native store return its validation error for incomplete objects.
        return document

    tokens={}
    if id_token:tokens["id_token"]=id_token
    if access_token:tokens["access_token"]=access_token
    if refresh_token:tokens["refresh_token"]=refresh_token
    if account_id:tokens["account_id"]=account_id
    standard={"auth_mode":"chatgpt","OPENAI_API_KEY":None,"tokens":tokens}
    if last_refresh:standard["last_refresh"]=last_refresh
    return standard

def import_accounts_text(text: str,format_name: str = "auto",alias: str = "") -> dict:
    if not isinstance(text,str) or not text.strip():raise RuntimeError("Authentication JSON is required")
    if len(text.encode("utf-8"))>10*1024*1024:raise RuntimeError("Authentication JSON must be 10 MB or smaller")
    try:document=json.loads(text)
    except json.JSONDecodeError as e:raise RuntimeError(f"Invalid JSON at line {e.lineno}, column {e.colno}") from e
    if not isinstance(document,(dict,list)):raise RuntimeError("Authentication JSON must be an object or array")

    format_name=str(format_name or "auto").strip().lower()
    if format_name not in ("auto","standard","cpa"):raise RuntimeError("Invalid import format")

    normalized_document=normalize_auth_document(document)

    alias=str(alias or "").strip()
    if len(alias)>100:raise RuntimeError("Alias is too long")
    if alias.isdigit():raise RuntimeError("Alias cannot contain only digits")
    if isinstance(document,list) and len(document)!=1 and alias:
        raise RuntimeError("Alias can only be applied to a single imported account")

    keys=account_store().import_documents(normalized_document,alias)
    return {"message":f"Imported {len(keys)} account(s) into native storage.","output":"Accounts saved; active account unchanged."}

class Handler(BaseHTTPRequestHandler):
    server_version="CodexAccountManager/"+APP_VERSION
    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def valid_host(self):
        try:
            headers=self.headers.get_all("Host",[])
            if len(headers)!=1:return False
            parsed=urlparse("//"+headers[0])
            if parsed.username or parsed.password or parsed.path:return False
            host=parsed.hostname
            bound=self.server.server_address[0]
            allowed=set(getattr(self.server,"allowed_hosts",()))|{bound}
            if ipaddress.ip_address(bound).is_loopback:allowed|={"localhost","127.0.0.1","::1"}
            return bound in ("0.0.0.0","::") or host in allowed
        except ValueError:return False

    def log_message(self,fmt,*args):
        if getattr(self.server,"quiet",False):return
        if urlparse(self.path).path.startswith("/api/login/"):return
        super().log_message(fmt,*args)

    def send_json(self,obj,status=200):
        body=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        content_type=self.headers.get_content_type()
        if content_type!="application/json":
            raise ValueError("Content-Type must be application/json")
        n=int(self.headers.get("Content-Length","0"))
        if n<=0 or n>11*1024*1024: raise ValueError("Invalid request body size")
        origin=self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            raise ValueError("Cross-origin requests are not allowed")
        document=json.loads(self.rfile.read(n))
        if not isinstance(document,dict):raise ValueError("Request body must be a JSON object")
        return document

    def do_GET(self):
        if not self.valid_host():self.send_error(403,"Unrecognized host");return
        p=urlparse(self.path)
        if p.path=="/":
            body=HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers();self.wfile.write(body);return

        if p.path in WEB_ASSETS:
            filename, content_type = WEB_ASSETS[p.path]
            body = WEB_ASSET_CONTENT[p.path]
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body); return

        if p.path=="/api/accounts":
            try:
                q=parse_qs(p.query);refresh=q.get("refresh",["0"])[0] in ("1","true","yes")
                result=list_accounts(refresh);auto_ping=decorate_accounts_for_auto_ping(result.get("accounts",[]))
                result.update(ok=True,account_manager_version=account_manager_version(),auto_ping_enabled=auto_ping["enabled"])
                self.send_json(result)
            except subprocess.TimeoutExpired:self.send_json({"ok":False,"error":"Usage refresh timed out"},504)
            except Exception as e:self.send_json({"ok":False,"error":str(e)},500)
            return

        if p.path=="/api/login/status":
            try:self.send_json({"ok":True,"login":LOGIN_MANAGER.status(parse_qs(p.query).get("id",[""])[0])})
            except RuntimeError as e:self.send_json({"ok":False,"error":str(e)},400)
            return

        if p.path in ("/api/config","/api/web/status"):
            try:self.send_json({"ok":True,**(settings_status() if p.path=="/api/config" else web_status())})
            except Exception as e:self.send_json({"ok":False,"error":str(e)},400)
            return

        if p.path=="/api/activity":
            try:self.send_json({"ok":True,**activity_status()})
            except (RuntimeError,OSError,ValueError) as error:self.send_json({"ok":False,"error":str(error)},500)
            return

        if p.path=="/api/status":
            try:
                result=get_status();result["ok"]=True;self.send_json(result)
            except Exception as e:self.send_json({"ok":False,"error":str(e)},500)
            return

        if p.path=="/api/profiles":
            try: self.send_json({"ok":True,**profiles_status()})
            except Exception as e:self.send_json({"ok":False,"error":str(e)},500)
            return

        if p.path=="/api/profiles/detail":
            try:self.send_json({"ok":True,"profile":profile_details(parse_qs(p.query).get("profile_id",[""])[0])})
            except Exception as e:self.send_json({"ok":False,"error":str(e)},400)
            return

        if p.path=="/api/profiles/usage":
            try:self.send_json({"ok":True,"usage":fetch_api_profile_usage(parse_qs(p.query).get("profile_id",[""])[0])})
            except Exception as e:self.send_json({"ok":False,"error":str(e)},400)
            return

        if p.path=="/api/health":
            self.send_json({"ok":True,"version":APP_VERSION,"account_backend":"native","json_api":True})
            return

        self.send_error(404)

    def do_POST(self):
        if not self.valid_host():self.send_error(403,"Unrecognized host");return
        p=urlparse(self.path)
        try:
            data=self.read_json()
            if p.path=="/api/config":
                self.send_json({"ok":True,"message":set_setting(data.get("name"),data.get("value"))});return
            if p.path=="/api/config/restart-commands":
                if "commands" not in data:raise RuntimeError("commands is required; use null to restore defaults")
                self.send_json({"ok":True,**set_restart_commands(data["commands"])});return
            if p.path=="/api/doctor":
                fix=data.get("fix_permissions",False)
                if not isinstance(fix,bool):raise RuntimeError("fix_permissions must be a boolean")
                self.send_json({"ok":True,**doctor_report(fix)});return
            if p.path=="/api/profiles/export":
                self.send_json({"ok":True,**api_profile_export(data.get("profile_ids"))});return
            if p.path=="/api/export-accounts":
                self.send_json({"ok":True,**account_export(data.get("queries"),data.get("format","standard"))});return
            if p.path=="/api/auto-ping/once":
                self.send_json({"ok":True,**auto_ping_worker_once()});return
            if p.path=="/api/login/start":
                self.send_json({"ok":True,"login":LOGIN_MANAGER.start(data.get("method","browser"),data.get("alias",""))});return
            if p.path=="/api/login/complete":
                self.send_json({"ok":True,"login":LOGIN_MANAGER.complete(data.get("id",""),data.get("callback",""))});return
            if p.path=="/api/login/cancel":
                self.send_json({"ok":True,"login":LOGIN_MANAGER.cancel(data.get("id",""))});return
            if p.path=="/api/account-remove":
                self.send_json({"ok":True,"message":account_store().remove(data.get("query",""))});return
            if p.path=="/api/switch":
                self.send_json({"ok":True,**do_switch(str(data.get("query","")).strip())});return
            if p.path=="/api/restart-services":
                choice=data.get("service","all")
                services={"all":(CODEX_APP_SERVICE,),"app":(CODEX_APP_SERVICE,)}
                if choice not in services:raise RuntimeError("Invalid service selection")
                self.send_json({"ok":True,**restart_services(services[choice])});return
            if p.path=="/api/import-accounts":
                self.send_json({"ok":True,**import_accounts_text(data.get("text",""),data.get("format","auto"),data.get("alias",""))});return
            if p.path=="/api/profiles":
                self.send_json({"ok":True,"profile":create_api_profile(data),"message":"API profile created securely."});return
            if p.path=="/api/profiles/update":
                self.send_json({"ok":True,**update_api_profile_partial(str(data.get("profile_id","")),data)});return
            if p.path=="/api/profiles/parse":
                self.send_json({"ok":True,"fields":parse_profile_text(data.get("text",""))});return
            if p.path=="/api/profiles/apply":
                self.send_json({"ok":True,**apply_account_profile(str(data.get("profile_id","")))});return
            if p.path=="/api/profiles/delete":
                self.send_json({"ok":True,"message":delete_profile(str(data.get("profile_id","")))});return
            if p.path in ("/api/ping-account","/api/test-account"):
                self.send_json({"ok":True,"message":run_account_ping(str(data.get("query","")).strip())});return
            if p.path=="/api/account-alias":
                alias=data.get("alias")
                if alias is not None and not isinstance(alias,str):raise ValueError("alias must be a string or null")
                self.send_json({"ok":True,"message":set_account_alias(data.get("selector",""),alias,data.get("previous_key",""))});return
            if p.path=="/api/config/restart-after-switch":
                if not isinstance(data.get("enabled"),bool):raise ValueError("enabled must be a boolean")
                self.send_json({"ok":True,"message":set_restart_after_switch(data["enabled"])});return
            if p.path=="/api/config/auto-ping":
                if not isinstance(data.get("enabled"),bool):raise ValueError("enabled must be a boolean")
                self.send_json({"ok":True,"message":set_auto_ping(data["enabled"])});return
            if p.path=="/api/config/account-auto-ping":
                if not isinstance(data.get("enabled"),bool):raise ValueError("enabled must be a boolean")
                self.send_json({"ok":True,"message":set_account_auto_ping(str(data.get("query","")).strip(),data["enabled"])});return
            self.send_error(404)
        except ServiceCommandError as e:self.send_json({"ok":False,"error":str(e),"output":e.output},500)
        except subprocess.TimeoutExpired:self.send_json({"ok":False,"error":"Command timed out"},504)
        except Exception as e:self.send_json({"ok":False,"error":str(e)},400)

def webui_enabled() -> bool:
    return read_webui_config().get("webui_enabled",False) is True


def web_status() -> dict:
    return {"enabled":webui_enabled(),**WebRuntime(webui_config_path()).status()}


def set_webui_enabled(enabled: bool) -> str:
    if not isinstance(enabled,bool):raise RuntimeError("enabled must be a boolean")
    with WEBUI_CONFIG_LOCK:
        config=read_webui_config();config["webui_enabled"]=enabled;write_webui_config(config)
    return "WebUI enabled. Run 'python3 app.py web' to start it." if enabled else "WebUI disabled. Any running WebUI will stop; CLI remains available."


def settings_status() -> dict:
    return {"webui_enabled":webui_enabled(),"restart_after_switch":restart_after_switch_enabled(),
            "restart_commands":configured_restart_commands(),
            "restart_command_defaults":default_restart_commands(),
            "auto_ping_enabled":auto_ping_enabled(),"auto_ping":auto_ping_snapshot()}


def set_setting(name: str,value: bool) -> str:
    setters={"webui_enabled":set_webui_enabled,"restart_after_switch":set_restart_after_switch,"auto_ping_enabled":set_auto_ping}
    if name not in setters:raise RuntimeError("Unknown setting")
    return setters[name](value)


def bool_value(value):
    if isinstance(value,bool):return value
    if isinstance(value,str) and value.lower() in ("true","on","yes","1"):return True
    if isinstance(value,str) and value.lower() in ("false","off","no","0"):return False
    raise RuntimeError("Use true/false or on/off")


def account_export(queries=None,format_name="standard") -> dict:
    if queries is not None and (not isinstance(queries,list) or not all(isinstance(q,str) for q in queries)):
        raise RuntimeError("queries must be an array of account selectors")
    documents=account_store().export_documents(queries,format_name)
    return {"documents":documents,"count":len(documents),"format":format_name}


@config_operation
def api_profile_export(profile_ids=None) -> dict:
    if profile_ids is not None and (not isinstance(profile_ids,list) or not profile_ids or not all(isinstance(value,str) for value in profile_ids)):
        raise RuntimeError("profile_ids must be a non-empty array of profile IDs")
    identifiers=list(dict.fromkeys(profile_ids)) if profile_ids is not None else [profile["id"] for profile in list_profiles() if profile.get("kind")=="api"]
    documents=[]
    for profile_id in identifiers:
        material=api_profile_material(profile_id)
        fields=profile_details(profile_id)
        documents.append({key:fields.get(key,"") for key in ("name","base_url","usage_url","model","reasoning_effort","approval_policy","sandbox_mode","model_catalog_json","http_headers")})
        documents[-1]["api_key"]=material["token"]
    return {"documents":documents,"count":len(documents),"format":"api-profiles"}


def save_private_export(path: Path,documents) -> None:
    # Atomic no-overwrite publication: existing files, including symlinks, survive.
    path=path.expanduser()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,name=tempfile.mkstemp(prefix=".export-",dir=path.parent)
    try:
        with os.fdopen(fd,"wb") as handle:
            os.fchmod(handle.fileno(),0o600)
            handle.write(json_bytes(documents));handle.flush();os.fsync(handle.fileno())
        os.link(name,path)
    except FileExistsError:
        raise RuntimeError("Export destination already exists; choose a new filename") from None
    finally:
        Path(name).unlink(missing_ok=True)


def update_api_profile_partial(profile_id: str,data: dict) -> dict:
    if not isinstance(data,dict):raise RuntimeError("Profile fields must be a JSON object")
    with CONFIG_OPERATION_LOCK:
        current=profile_details(profile_id)
        current.update(data)
        return update_api_profile(profile_id,current)


def doctor_report(fix_permissions=False) -> dict:
    with CONFIG_OPERATION_LOCK:
        report=account_store().doctor(fix_permissions)
        profile_issues=[]
        if profiles_dir().exists():
            for path in sorted(profiles_dir().iterdir()):
                if not path.is_dir():continue
                try:
                    read_profile_manifest(path.name)
                    validate_toml_bytes((path/"config.toml").read_bytes(),"stored profile")
                    if not any(file.is_file() and file.stat().st_size for file in (path/"token",path/"secret.env")):
                        raise RuntimeError("Missing profile key")
                except (RuntimeError,OSError,ValueError):
                    profile_issues.append({"code":"invalid_profile","profile_id":path.name,"message":"API profile files are missing or invalid."})
                for file in (path,path/"token",path/"secret.env",path/"manifest.json",path/"config.toml"):
                    mode=0o700 if file==path else 0o600
                    if file.exists() and file.stat().st_mode & 0o777 != mode:
                        if fix_permissions:file.chmod(mode)
                        else:profile_issues.append({"code":"permissions","profile_id":path.name,"message":f"{file.name} permissions should be {mode:04o}."})
        report["issues"].extend(profile_issues)
        try:
            config=read_webui_config()
            if config.get("active_profile","default") not in {p["id"] for p in list_profiles()}:
                report["issues"].append({"code":"missing_active_profile","message":"The selected API profile no longer exists."})
            validate_toml_bytes((codex_home()/"config.toml").read_bytes(),"config.toml") if (codex_home()/"config.toml").exists() else None
        except (RuntimeError,OSError,ValueError):
            report["issues"].append({"code":"invalid_config","message":"Manager configuration or Codex config.toml is invalid."})
        report["healthy"]=not report["issues"]
        report["codex_cli_found"]=bool(shutil.which("codex"))
        return report


@contextmanager
def termination_signals():
    previous={}
    def stop(signum,frame):raise KeyboardInterrupt()
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM,signal.SIGINT):
            previous[signum]=signal.signal(signum,stop)
    try:yield
    finally:
        for signum,handler in previous.items():signal.signal(signum,handler)


def run_web(args):
    # No socket, OAuth listener, timer, or worker is created while disabled.
    if not webui_enabled():raise RuntimeError("WebUI is disabled. Run 'python3 app.py web start' to enable and start it.")
    runtime=WebRuntime(webui_config_path())
    with runtime.lease(args.host,args.port), termination_signals():
        srv=ThreadingHTTPServer((args.host,args.port),Handler);srv.quiet=args.quiet;srv.timeout=.25;srv.allowed_hosts={args.host,*getattr(args,"allowed_host",[])};srv.daemon_threads=False
        private_write(runtime.info_path,json_bytes({"pid":os.getpid(),"host":args.host,"port":srv.server_port}))
        AUTO_PING_STOP.clear()
        worker=threading.Thread(target=auto_ping_worker_loop,name="auto-ping-worker",daemon=True)
        worker.start()
        print(f"Codex Account Manager: http://{args.host}:{srv.server_port}",flush=True)
        try:
            while webui_enabled():srv.handle_request()
        except KeyboardInterrupt:pass
        finally:
            AUTO_PING_STOP.set();AUTO_PING_WAKE.set();LOGIN_MANAGER.close();srv.server_close()
            worker.join()


def allowed_host(value: str) -> str:
    host=value.lower().strip()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?",host) or ".." in host:
        raise argparse.ArgumentTypeError("Expected a hostname without scheme, port, path, or wildcard")
    return host


def cli_parser():
    parser=argparse.ArgumentParser(description="Native Codex account manager: CLI and optional WebUI")
    parser.add_argument("--version",action="version",version=APP_VERSION)
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--quiet",action="store_true")
    commands=parser.add_subparsers(dest="command")
    web=commands.add_parser("web",help="Enable, disable, inspect, or run the optional WebUI")
    web.add_argument("action",choices=("serve","start","enable","disable","status","check-enabled"),nargs="?",default="serve")
    web.add_argument("--host",default=argparse.SUPPRESS)
    web.add_argument("--allowed-host",type=allowed_host,action="append",default=[],help="Additional trusted Host behind a reverse proxy; repeat for more hosts")
    web.add_argument("--port",type=int,default=argparse.SUPPRESS)
    web.add_argument("--quiet",action="store_true",default=argparse.SUPPRESS)
    web.add_argument("--json",action="store_true")
    for name in ("list","limits","status","switch","alias","remove","import","export","ping","login","profiles","config","auto-ping","restart","doctor","activity"):
        cmd=commands.add_parser(name)
        cmd.add_argument("--json",action="store_true",help="Machine-readable output; login emits JSON lines")
        if name in ("list","limits"):
            mode=cmd.add_mutually_exclusive_group()
            mode.add_argument("--refresh","--api",action="store_true",help="Fetch current usage from ChatGPT")
            mode.add_argument("--skip-api",action="store_true",help="Read saved usage only")
        if name in ("switch","alias","remove","ping"):
            cmd.add_argument("query",nargs="?" if name=="switch" else None,help="Account key, email, alias, number; switch - returns to previous")
        if name=="alias":cmd.add_argument("value",nargs="?",help="Omit to clear alias")
        if name=="import":
            cmd.add_argument("file",help="Auth JSON file, or - for stdin")
            cmd.add_argument("--alias",default="")
            cmd.add_argument("--format",choices=("auto","standard","cpa"),default="auto")
        if name=="export":
            cmd.add_argument("file",help="Private output file; existing files are never overwritten")
            cmd.add_argument("--query",action="append",help="Repeat to export selected accounts; omit for all")
            cmd.add_argument("--format",choices=("standard","cpa"),default="standard")
        if name=="login":
            methods=cmd.add_mutually_exclusive_group()
            methods.add_argument("--manual",action="store_true",help="Paste the callback URL (remote hosts)")
            methods.add_argument("--device","--device-auth",action="store_true",help="Sign in with a device code")
            cmd.add_argument("--no-browser",action="store_true")
            cmd.add_argument("--alias",default="")
        if name=="profiles":
            cmd.add_argument("action",choices=("list","detail","create","update","parse","apply","usage","delete","export"),nargs="?",default="list")
            cmd.add_argument("profile_id",nargs="?",help="Profile ID, or TOML filename for parse")
            cmd.add_argument("--file",help="Profile fields as JSON file, or - for stdin")
            cmd.add_argument("--toml",help="Create/update fields from a TOML file")
            cmd.add_argument("--api-key-file",help="Read key from a private file or - for stdin")
            cmd.add_argument("--prompt-key",action="store_true",help="Read API key without echoing it")
            cmd.add_argument("--query",action="append",help="Repeat profile IDs to export; omit for all")
            cmd.add_argument("--output",help="Save parsed fields, including key, to a new private JSON file")
            for flag in ("name","model","reasoning-effort","usage-url","approval-policy","sandbox-mode","model-catalog-json"):
                cmd.add_argument("--"+flag,default=None)
            cmd.add_argument("--headers-file",help="HTTP headers as a JSON object")
        if name=="config":
            cmd.add_argument("action",choices=("show","set","restart-commands"),nargs="?",default="show")
            source=cmd.add_mutually_exclusive_group()
            source.add_argument("--file",help="JSON object with an app array of command strings")
            source.add_argument("--defaults",action="store_true",help="Restore default restart commands")
            cmd.add_argument("name",nargs="?",choices=("webui_enabled","restart_after_switch","auto_ping_enabled"))
            cmd.add_argument("value",nargs="?",help="true/false or on/off")
        if name=="auto-ping":
            cmd.add_argument("action",choices=("status","enable","disable","once","run"),nargs="?",default="status")
            cmd.add_argument("--account",help="Set Auto Ping for one account instead of globally")
        if name=="restart":cmd.add_argument("--service",choices=("all","app"),default="app")
        if name=="doctor":cmd.add_argument("--fix-permissions",action="store_true",help="Repair private file modes; never delete or rebuild data")
    return parser


def cli_output(result, as_json=False):
    if as_json:
        print(json.dumps({"ok":True,**result},ensure_ascii=False),flush=True)
    elif "accounts" in result:
        if "enabled" in result:print("Auto Ping: "+("enabled" if result["enabled"] else "disabled"))
        for account in result["accounts"]:
            label=account.get("alias") or account.get("email") or account.get("account_name") or account["account_key"]
            print(f"{'*' if account['active'] else ' '} {account['number']:>2}  {label}  [{account.get('plan') or 'unknown'}]")
            for title, window in (("5h",five_hour_usage(account)),("Weekly",weekly_usage(account))):
                if not isinstance(window,dict):continue
                remaining=remaining_usage_percent(window)
                reset=parse_reset_time(window.get("resets_at"))
                print(f"      {title}: {remaining if remaining is not None else '?'}% remaining; reset {reset.isoformat() if reset else 'unknown'}")
            credits=account.get("usage",{}).get("reset_credits")
            if credits is not None:print(f"      Reset credits: {credits}")
            if "auto_ping_configured" in account:
                print("      Auto Ping: "+("selected" if account["auto_ping_configured"] else "off")+(" (active)" if account.get("auto_ping_effective") else " (paused)"))
                if account.get("auto_ping_last_ping_at"):print("      Last ping: "+str(account["auto_ping_last_ping_at"]))
                if account.get("auto_ping_last_error"):print("      Ping error: "+str(account["auto_ping_last_error"]))
            credit_balance=account.get("usage",{}).get("credits")
            if isinstance(credit_balance,dict):print("      Credits: "+("Unlimited" if credit_balance.get("unlimited") else str(credit_balance.get("balance") if credit_balance.get("balance") is not None else "unknown")))
            print("      Key: "+account["account_key"])
            if account.get("email") and account.get("alias"):print("      Email: "+account["email"])
            refresh=account.get("usage",{}).get("refresh",{})
            if refresh.get("requested") and refresh.get("status")!="ok":
                print(f"      Usage: {refresh.get('error_code') or refresh['status']} (showing saved values)")
        if not result["accounts"]:print("No accounts yet. Run: python3 app.py login")
    else:
        print(result.get("message") or json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        if result.get("output"):print(result["output"],flush=True)


def read_cli_file(path: str,limit=10*1024*1024) -> str:
    if path=="-":
        text=sys.stdin.read(limit+1)
    else:
        with Path(path).expanduser().open() as handle:text=handle.read(limit+1)
    if len(text.encode())>limit:raise RuntimeError("Input file is too large")
    return text


def profile_cli_fields(args):
    fields={}
    if args.toml:fields.update(parse_profile_text(read_cli_file(args.toml,60000)))
    if args.file:
        document=json.loads(read_cli_file(args.file))
        if not isinstance(document,dict):raise RuntimeError("Profile fields must be a JSON object")
        fields.update(document)
    for key in ("name","model","reasoning_effort","usage_url","approval_policy","sandbox_mode","model_catalog_json"):
        if getattr(args,key) is not None:fields[key]=getattr(args,key)
    if args.headers_file:
        fields["http_headers"]=json.loads(read_cli_file(args.headers_file,20000))
    if args.api_key_file:fields["api_key"]=read_cli_file(args.api_key_file,2048).strip()
    if args.prompt_key:fields["api_key"]=getpass.getpass("API key: ").strip()
    if args.action=="create":
        fields.setdefault("base_url",API_BASE_URL)
        if not fields.get("api_key") and sys.stdin.isatty():fields["api_key"]=getpass.getpass("API key: ").strip()
    return fields


def run_cli(args):
    if args.command=="login":
        method="device" if args.device else "manual" if args.manual else "browser"
        session=LOGIN_MANAGER.start(method,args.alias)
        if args.json:cli_output({"login":session},True)
        else:
            print("Open: "+session["url"],flush=True)
            if session.get("user_code"):print("Device code: "+session["user_code"],flush=True)
            if session.get("warning"):print(session["warning"],flush=True)
        try:
            if method=="manual" or session.get("warning"):
                print("Paste the full callback URL: ",file=sys.stderr,flush=True)
                callback=sys.stdin.readline(16001).strip()
                if not callback:raise EOFError()
                session=LOGIN_MANAGER.complete(session["id"],callback)
            elif not args.no_browser and method=="browser":
                webbrowser.open(session["url"])
            while session["status"] in ("pending","exchanging"):
                time.sleep(.5)
                session=LOGIN_MANAGER.status(session["id"])
            if session["status"]!="success":raise RuntimeError(session.get("message","Login failed"))
            return {"message":session["message"],"login":session}
        finally:
            LOGIN_MANAGER.close()
    if args.command in ("list","limits"):
        result=list_accounts(args.refresh and not args.skip_api)
        decorate_accounts_for_auto_ping(result["accounts"])
        return result
    if args.command=="web":
        if args.action in ("enable","disable"):
            return {"message":set_webui_enabled(args.action=="enable"),**web_status()}
        return web_status()
    if args.command=="config":
        if args.action=="restart-commands":
            if args.defaults:return set_restart_commands(None)
            if not args.file:raise RuntimeError("config restart-commands requires --file or --defaults")
            return set_restart_commands(json.loads(Path(args.file).expanduser().read_text(encoding="utf-8")))
        if args.action=="show":return settings_status()
        if not args.name or args.value is None:raise RuntimeError("config set requires a setting name and boolean value")
        return {"message":set_setting(args.name,bool_value(args.value))}
    if args.command=="auto-ping":
        if args.action in ("enable","disable"):
            message=set_account_auto_ping(args.account,args.action=="enable") if args.account else set_auto_ping(args.action=="enable")
            return {"message":message}
        if args.account:raise RuntimeError("--account is supported with enable/disable")
        if args.action=="once":return auto_ping_worker_once()
        if args.action=="run":
            AUTO_PING_STOP.clear()
            try:
                with termination_signals():auto_ping_worker_loop()
            finally:AUTO_PING_STOP.set();AUTO_PING_WAKE.set()
            return {"message":"Auto Ping stopped."}
        result=list_accounts(False);decorate_accounts_for_auto_ping(result["accounts"])
        return {"enabled":auto_ping_enabled(),"accounts":result["accounts"],"state":auto_ping_snapshot()["state"]}
    if args.command=="restart":
        return restart_services({"all":(CODEX_APP_SERVICE,),"app":(CODEX_APP_SERVICE,)}[args.service])
    if args.command=="doctor":return doctor_report(args.fix_permissions)
    if args.command=="export":
        result=account_export(args.query,args.format)
        save_private_export(Path(args.file),result["documents"])
        return {"message":f"Exported {result['count']} account(s) to a private file.","count":result["count"],"file":str(Path(args.file).expanduser().absolute())}
    if args.command=="activity":return activity_status()
    if args.command=="status":return get_status()
    if args.command=="switch":
        if not args.query:
            if args.json or not sys.stdin.isatty():raise RuntimeError("An account selector is required in non-interactive mode")
            result=list_accounts(False);cli_output(result)
            args.query=input("Account number, key, alias, or - for previous: ").strip()
        return do_switch(args.query)
    if args.command=="alias":return {"message":set_account_alias(args.query,args.value)}
    if args.command=="remove":return {"message":account_store().remove(args.query)}
    if args.command=="ping":return {"message":run_account_ping(args.query)}
    if args.command=="import":
        return import_accounts_text(read_cli_file(args.file),args.format,args.alias)
    if args.command=="profiles":
        if args.action=="list":return profiles_status()
        if args.action=="export":
            if not args.output:raise RuntimeError("profiles export requires --output for a private file")
            if args.profile_id and args.query:raise RuntimeError("Use a profile ID or --query, not both")
            result=api_profile_export([args.profile_id] if args.profile_id else args.query)
            save_private_export(Path(args.output),result["documents"])
            return {"message":f"Exported {result['count']} API account(s) to a private file.","count":result["count"],"file":str(Path(args.output).expanduser().absolute())}
        if args.action=="create":return {"profile":create_api_profile(profile_cli_fields(args)),"message":"API account created."}
        if args.action=="parse":
            if not args.profile_id:raise RuntimeError("A TOML filename is required")
            fields=parse_profile_text(read_cli_file(args.profile_id,60000))
            if args.output:
                save_private_export(Path(args.output),fields)
                return {"message":"Parsed fields saved to a private file.","file":str(Path(args.output).absolute())}
            has_key=bool(fields.pop("api_key",None))
            return {"fields":fields,"has_api_key":has_key}
        if not args.profile_id:raise RuntimeError("A profile ID is required")
        if args.action=="update":return update_api_profile_partial(args.profile_id,profile_cli_fields(args))
        if args.action=="detail":return {"profile":profile_details(args.profile_id)}
        if args.action=="apply":return apply_account_profile(args.profile_id)
        if args.action=="usage":return {"usage":fetch_api_profile_usage(args.profile_id)}
        return {"message":delete_profile(args.profile_id)}
    raise RuntimeError("Unknown command")


def main():
    parser=cli_parser();args=parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if sys.version_info<(3,11):raise SystemExit("Python 3.11 or newer is required")
    try:
        if args.command=="web" and args.action=="check-enabled":
            raise SystemExit(0 if webui_enabled() else 1)
        if args.command=="web" and args.action in ("start","serve"):
            if args.action=="start":set_webui_enabled(True)
            run_web(args)
            return
        result=run_cli(args)
        cli_output(result,args.json)
        if args.command=="doctor" and not result["healthy"]:raise SystemExit(1)
    except (RuntimeError,OSError,ValueError) as error:
        failure={"ok":False,"error":str(error)}
        if isinstance(error,ServiceCommandError):failure["output"]=error.output
        if args.json:print(json.dumps(failure))
        else:
            print(str(error),file=sys.stderr)
            if failure.get("output"):print(failure["output"],file=sys.stderr)
        raise SystemExit(1)
    except (KeyboardInterrupt,EOFError):
        LOGIN_MANAGER.close()
        raise SystemExit(130)

if __name__=="__main__":
    main()
