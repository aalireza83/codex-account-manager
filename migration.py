"""Explicit, offline migration into the independent manager vault.

Only this module reads the former account layout. Normal CLI/WebUI startup never
imports or falls back to another manager's storage.
"""
import base64
import json
import os
import shutil
import tempfile
from pathlib import Path

from accounts import AccountStore, identity, matches_identity, json_bytes, private_write

OLD_BEGIN = '# BEGIN codex-auth-webui managed providers'
OLD_END = '# END codex-auth-webui managed providers'
NEW_BEGIN = '# BEGIN codex-account-manager managed providers'
NEW_END = '# END codex-account-manager managed providers'


def migrate_storage(source_config, source_accounts, target_config, target_data, codex_home):
    source_config,source_accounts,target_config,target_data,codex_home=map(
        lambda value:Path(value).expanduser().resolve(),
        (source_config,source_accounts,target_config,target_data,codex_home))
    if target_config.exists() or target_data.exists():
        raise RuntimeError('Migration target already exists; refusing to merge or overwrite it')
    if source_config==target_config or target_data.is_relative_to(source_accounts) or target_data.is_relative_to(source_config.parent):
        raise RuntimeError('Migration destination must be separate from the source')
    # Old account directories can also contain unrelated Codex login sandboxes.
    # Only consume the registry and top-level credential files, never those trees.
    consumed=[source_config,source_accounts/'registry.json',*source_accounts.glob('*.auth.json')]
    profiles_source=source_config.parent/'profiles'
    if profiles_source.exists():consumed.extend(profiles_source.rglob('*'))
    if any(path.is_symlink() for path in consumed):
        raise RuntimeError('Migration input files must not be symlinks')
    settings=json.loads(source_config.read_text())
    registry=json.loads((source_accounts/'registry.json').read_text())
    if not isinstance(settings,dict) or not isinstance(registry,dict) or not isinstance(registry.get('accounts'),list):
        raise RuntimeError('Invalid source settings or account registry')
    replacement=(str(source_config.parent/'profiles'),str(target_data/'profiles'))
    def rewrite_text(text):
        return text.replace(*replacement).replace(OLD_BEGIN,NEW_BEGIN).replace(OLD_END,NEW_END)
    def rewrite_json(value):
        if isinstance(value,str):return rewrite_text(value)
        if isinstance(value,list):return [rewrite_json(item) for item in value]
        if isinstance(value,dict):return {key:rewrite_json(item) for key,item in value.items()}
        return value
    target_config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    target_data.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    stage=Path(tempfile.mkdtemp(prefix='.manager-migration-',dir=target_data.parent))
    active_path=codex_home/'config.toml'
    active_before=active_path.read_bytes() if active_path.exists() else None
    active_after=rewrite_text(active_before.decode()).encode() if active_before is not None else None
    published=False
    try:
        vault=AccountStore(codex_home,stage/'vault')
        seen=set()
        for record in registry['accounts']:
            key=record.get('account_key')
            if not isinstance(key,str) or key in seen:raise RuntimeError('Invalid or duplicate source account identity')
            seen.add(key)
            encoded=base64.urlsafe_b64encode(key.encode()).decode().rstrip('=')
            source=source_accounts/(encoded+'.auth.json')
            document=json.loads(source.read_text())
            if not matches_identity(record,identity(document)):
                raise RuntimeError('A source credential does not match its account identity')
            destination=vault.snapshot(key)
            private_write(destination,source.read_bytes())
            stat=source.stat();os.utime(destination,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        private_write(vault.registry_path,json_bytes(registry))
        # Preserve unregistered credentials separately without enrolling them.
        registered={base64.urlsafe_b64encode(key.encode()).decode().rstrip('=')+'.auth.json' for key in seen}
        for source in source_accounts.glob('*.auth.json'):
            if source.name not in registered:private_write(stage/'migration'/'unregistered'/source.name,source.read_bytes())
        profiles=source_config.parent/'profiles'
        if profiles.exists():
            for source in profiles.rglob('*'):
                if not source.is_file():continue
                content=source.read_bytes()
                if source.name=='manifest.json':content=json_bytes(rewrite_json(json.loads(content)))
                elif source.name=='config.toml':content=rewrite_text(content.decode()).encode()
                private_write(stage/'profiles'/source.relative_to(profiles),content)
        activity=source_config.parent/'activity.json'
        if activity.exists():private_write(stage/'activity.json',activity.read_bytes())
        if active_before is not None:private_write(stage/'migration'/'active-config-before.toml',active_before)
        private_write(stage/'migration'/'source-settings.json',source_config.read_bytes())
        # No old service/runtime leases or locks are copied.
        os.replace(stage,target_data);published=True
        private_write(target_config,json_bytes(rewrite_json(settings)))
        if active_after!=active_before:private_write(active_path,active_after)
    except BaseException:
        if published:
            if active_before is not None and active_path.exists() and active_path.read_bytes()!=active_before:
                private_write(active_path,active_before)
            target_config.unlink(missing_ok=True)
            shutil.rmtree(target_data)
        raise
    finally:
        if stage.exists():shutil.rmtree(stage)
    return {'accounts':len(seen),'config':str(target_config),'data':str(target_data),
            'active_config_updated':active_after!=active_before}
