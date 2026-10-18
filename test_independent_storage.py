"""Isolation and explicit migration; no access to real accounts or services."""
import base64
import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from accounts import AccountStore,json_bytes,private_write
from migration import migrate_storage,OLD_BEGIN,OLD_END,NEW_BEGIN,NEW_END
from test_accounts import auth


class IndependentStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.home=self.root/'codex'
        self.source=self.root/'old-manager';self.source_config=self.source/'config.json'
        self.legacy=self.home/'accounts'
        self.target_config=self.root/'manager'/'config.json';self.target_data=self.root/'manager'/'data'
        env=patch.dict(os.environ,{'CODEX_HOME':str(self.home),
            'CODEX_ACCOUNT_MANAGER_CONFIG':str(self.target_config)})
        env.start();self.addCleanup(env.stop)
        unset=patch.dict(os.environ);unset.start();self.addCleanup(unset.stop)
        os.environ.pop('CODEX_ACCOUNT_MANAGER_DATA_DIR',None)
        self.document=auth();self.key='user-1::account-1'
        self.settings={'webui_enabled':True,'restart_after_switch':False,'active_profile':'default',
                       'auto_ping_accounts':{self.key:True},'custom_future_setting':{'keep':True},
                       'restart_commands':app.default_restart_commands()}
        self.registry={'schema_version':4,'accounts':[{'account_key':self.key,'alias':'Work','email':'user-1@example.test','future':42}],
                       'active_account_key':self.key,'previous_active_account_key':None,'custom':True}
        private_write(self.source_config,json_bytes(self.settings))
        private_write(self.legacy/'registry.json',json_bytes(self.registry))
        encoded=base64.urlsafe_b64encode(self.key.encode()).decode().rstrip('=')
        self.old_snapshot=self.legacy/(encoded+'.auth.json');private_write(self.old_snapshot,json_bytes(self.document))
        private_write(self.home/'auth.json',json_bytes(self.document))

    def migrate(self):
        return migrate_storage(self.source_config,self.legacy,self.target_config,self.target_data,self.home)

    def source_bytes(self):
        return {str(p):p.read_bytes() for root in (self.source,self.legacy) for p in root.rglob('*') if p.is_file()}

    def test_defaults_use_private_names_and_ignore_old_environment_and_registry(self):
        with patch.dict(os.environ,{'CODEX_AUTH_WEBUI_CONFIG':str(self.source_config)}):
            self.assertEqual(app.webui_config_path(),self.target_config)
            self.assertEqual(app.account_store().registry_path,self.target_data/'vault'/'accounts.json')
            self.assertEqual(app.account_store().read()['accounts'],[])
            path=app.account_store().snapshot(self.key)
            self.assertEqual(path,self.target_data/'vault'/'credentials'/(hashlib.sha256(self.key.encode()).hexdigest()+'.json'))
            self.assertEqual(app.read_webui_config(),{})

    def test_migration_preserves_originals_settings_tokens_aliases_and_usage_metadata(self):
        before=self.source_bytes();active=(self.home/'auth.json').read_bytes()
        result=self.migrate();self.assertEqual(result['accounts'],1)
        self.assertEqual(self.source_bytes(),before)
        self.assertEqual(app.read_webui_config(),self.settings)
        self.assertEqual(app.account_store().read(),self.registry)
        self.assertEqual(app.account_store().snapshot(self.key).read_bytes(),self.old_snapshot.read_bytes())
        self.assertEqual((self.home/'auth.json').read_bytes(),active)
        self.assertEqual(app.account_store().snapshot(self.key).stat().st_mtime_ns,self.old_snapshot.stat().st_mtime_ns)
        self.assertEqual(self.target_config.stat().st_mode&0o777,0o600)
        app.account_store().set_alias('Work','Independent')
        self.assertEqual(self.source_bytes(),before)
        self.assertEqual(json.loads((self.legacy/'registry.json').read_text()),self.registry)

    def test_profiles_and_active_references_move_without_changing_unowned_configuration(self):
        pid='synthetic';old=self.source/'profiles'/pid;new=self.target_data/'profiles'/pid
        private_write(old/'token',b'synthetic-secret\n')
        private_write(old/'manifest.json',json_bytes({'id':pid,'token_path':str(old/'token'),'future':7}))
        private_write(old/'config.toml',f'args = ["{old}/token"]\n'.encode())
        private_write(self.source/'activity.json',b'{"tracking_since":"synthetic","records":{}}')
        config=f'model = "unchanged"\n{OLD_BEGIN}\n[model_providers.webui_synthetic]\nargs = ["{old}/token"]\n{OLD_END}\n'
        private_write(self.home/'config.toml',config.encode())
        self.settings['managed_config_overlay']={'applied':str(old/'token')}
        private_write(self.source_config,json_bytes(self.settings))
        before=self.source_bytes();result=self.migrate()
        self.assertTrue(result['active_config_updated']);self.assertEqual(self.source_bytes(),before)
        self.assertEqual((new/'token').read_bytes(),b'synthetic-secret\n')
        self.assertIn(str(new/'token'),(new/'config.toml').read_text())
        self.assertEqual(json.loads((new/'manifest.json').read_text())['future'],7)
        self.assertEqual(app.read_webui_config()['managed_config_overlay']['applied'],str(new/'token'))
        migrated=(self.home/'config.toml').read_text()
        self.assertIn('model = "unchanged"',migrated);self.assertIn(NEW_BEGIN,migrated);self.assertIn(NEW_END,migrated)
        self.assertIn(str(new/'token'),migrated)
        self.assertEqual((self.target_data/'migration'/'active-config-before.toml').read_bytes(),config.encode())
        self.assertEqual((self.target_data/'activity.json').read_bytes(),(self.source/'activity.json').read_bytes())

    def test_missing_credentials_or_existing_target_abort_without_merging(self):
        before=self.source_bytes();self.old_snapshot.unlink()
        with self.assertRaises(FileNotFoundError):self.migrate()
        self.assertFalse(self.target_config.exists());self.assertFalse(self.target_data.exists())
        private_write(self.old_snapshot,before[str(self.old_snapshot)])
        private_write(self.target_config,b'{}')
        with self.assertRaisesRegex(RuntimeError,'already exists'):self.migrate()
        self.assertEqual(self.target_config.read_bytes(),b'{}');self.assertEqual(self.source_bytes(),before)

    def test_failed_publication_restores_active_file_and_removes_only_new_destination(self):
        before=self.source_bytes();original=private_write
        def fail(path,data):
            if Path(path)==self.target_config:raise OSError('Synthetic publication failure')
            return original(path,data)
        with patch('migration.private_write',side_effect=fail),self.assertRaises(OSError):self.migrate()
        self.assertEqual(self.source_bytes(),before);self.assertFalse(self.target_data.exists())
        self.assertFalse(self.target_config.exists())

    def test_unrelated_login_sandbox_is_not_followed_or_imported(self):
        temporary=self.legacy/'login-old'/'tmp'
        temporary.mkdir(parents=True)
        (temporary/'codex-wrapper').symlink_to('/bin/true')
        self.migrate()
        self.assertEqual(len(app.account_store().read()['accounts']),1)
        self.assertFalse((self.target_data/'login-old').exists())

    def test_foreign_active_account_is_never_automatically_enrolled(self):
        self.migrate()
        private_write(self.home/'auth.json',json_bytes(auth('foreign-account')))
        before=app.account_store().snapshot(self.key).read_bytes()
        result=app.list_accounts(False)
        self.assertEqual(len(result['accounts']),1)
        self.assertFalse(result['accounts'][0]['active'])
        self.assertEqual(app.account_store().snapshot(self.key).read_bytes(),before)
        self.assertEqual(len(app.account_store().read()['accounts']),1)
