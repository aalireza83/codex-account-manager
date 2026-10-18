"""Isolated browser fixture: synthetic credentials and OAuth/usage responses only."""
import os, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app, accounts, login
from test_accounts import auth, quota
root=tempfile.TemporaryDirectory(prefix='caw-preview-')
os.environ['CODEX_HOME']=root.name+'/codex'
os.environ['CODEX_ACCOUNT_MANAGER_CONFIG']=root.name+'/config.json'
os.environ['CODEX_ACCOUNT_MANAGER_DATA_DIR']=root.name+'/data'
store=app.account_store()
store.import_documents(auth(), 'Personal')
store.import_documents(auth('user-2'), 'Work')
with store.switching('Personal'):pass
accounts.request_json=lambda *a, **kw: quota()
store.list(True)
app.write_webui_config({'restart_after_switch':False})
app.update_auto_ping_state(app.account_selector(store.find('Work')),{'last_result':'error','last_error':'Synthetic prior Ping failed'})
def oauth(url, data=None, **kwargs):
    if url.endswith('/usercode'):
        return {'device_auth_id':'synthetic-device','user_code':'ABCD-EFGH','interval':1}
    if url.endswith('/deviceauth/token'):
        raise accounts.RemoteError(403)
    return {k:v for k,v in auth('new-user')['tokens'].items() if k!='account_id'}
login.request_json=oauth
app.restart_services=lambda services=(app.CODEX_APP_SERVICE,): {'message':'Restarted '+', '.join(services),'output':'Synthetic systemctl output: '+', '.join(services)}
app._run_account_ping=lambda query: 'Synthetic Ping completed.'
app.fetch_api_profile_usage=lambda profile_id: {'quota':{'remaining':42,'used':8,'limit':50},'status':'active','usage':{'today':{'requests':3,'total_tokens':150}},'daily_usage':[]}
sys.argv=['app.py','web','start','--port',os.environ['CODEX_UI_TEST_PORT'],'--quiet']
app.main()
