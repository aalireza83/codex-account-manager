const grid=document.getElementById('grid');
const banner=document.getElementById('banner');
const meta=document.getElementById('meta');
const serviceOutputBox=document.getElementById('serviceOutputBox');
const serviceOutput=document.getElementById('serviceOutput');
const authImportOutputBox=document.getElementById('authImportOutputBox');
const authImportOutput=document.getElementById('authImportOutput');
const aliasModal=document.getElementById('aliasModal');
const aliasInput=document.getElementById('aliasInput');
const aliasError=document.getElementById('aliasError');
const clearAliasButton=document.getElementById('clearAliasButton');
const saveAliasButton=document.getElementById('saveAliasButton');
let restartAfterSwitch=false;
let autoPingGlobal=false;
let profilesState={profiles:[{id:'default',name:'Default Codex config',kind:'default'}],active_profile:'default'};
let editingProfileId=null;
let profileHeaders={};
let profileCockpitChanged=false;
let profileHiddenFields={approval_policy:'',sandbox_mode:'',model_catalog_json:''};
let aliasEditor={selector:'',previousKey:'',currentAlias:''};

function show(msg,ok=true){
  banner.className='banner '+(ok?'ok':'err');banner.textContent=msg;
  for(const [modalId,bannerId] of [['accountDialog','dialogBanner'],['exportDialog','exportBanner']]){
    const target=document.getElementById(bannerId),open=document.getElementById(modalId).classList.contains('open');
    target.className=open?banner.className:'banner';target.textContent=open?msg:'';
  }
}
function clearBanner(){for(const target of [banner,document.getElementById('dialogBanner'),document.getElementById('exportBanner')]){target.className='banner';target.textContent=''}}
function showServiceOutput(output){
  if(!output){serviceOutputBox.style.display='none';serviceOutput.textContent='';return}
  serviceOutput.textContent=output;serviceOutputBox.style.display='block';serviceOutputBox.open=false
}
function clearServiceOutput(){showServiceOutput('')}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function jsArg(s){return JSON.stringify(String(s)).replace(/'/g,'\\u0027').replace(/</g,'\\u003c')}
const viewDetails={dashboard:'Dashboard',accounts:'Accounts','api-accounts':'API accounts',diagnostics:'Diagnostics',activity:'Activity',settings:'Settings'};
let lastAccountData=null;
function updateOverview(){
  const list=lastAccountData?.accounts||[];
  document.getElementById('statAccounts').textContent=lastAccountData?list.length:'—';
  const providers=(profilesState.profiles||[]).filter(p=>p.kind==='api');
  document.getElementById('statProfiles').textContent=providers.length;
  const api=providers.find(p=>p.id===profilesState.active_profile);
  const native=list.find(a=>a.active);
  document.getElementById('statActive').textContent=api?api.name:(native?(native.alias||native.account_name||native.email||'ChatGPT account'):'No active account');
  document.getElementById('statActiveType').textContent=api?'API provider':native?'ChatGPT':'Select an account to get started';
}
function setSidebar(open){
  const wasOpen=document.getElementById('sidebar').classList.contains('open');
  document.getElementById('sidebar').classList.toggle('open',open);
  document.getElementById('sidebarBackdrop').hidden=!open;
  document.getElementById('sidebarToggle').setAttribute('aria-expanded',String(open));
  const mobile=!matchMedia('(min-width: 1280px)').matches;
  document.getElementById('sidebar').inert=mobile&&!open;
  document.querySelector('.workspace').inert=mobile&&open;
  document.body.style.overflow=mobile&&open||document.querySelector('.modal.open')?'hidden':'';
  if(open)document.getElementById('sidebarClose').focus();
  else if(wasOpen&&mobile)document.getElementById('sidebarToggle').focus();
}
function syncThemeButton(){
  const dark=document.documentElement.classList.contains('dark');
  const button=document.getElementById('themeToggle');
  button.setAttribute('aria-label',dark?'Switch to light mode':'Switch to dark mode');
  button.setAttribute('aria-pressed',String(dark));
}
function toggleTheme(){
  const dark=document.documentElement.classList.toggle('dark');
  try{localStorage.setItem('codex-ui-theme',dark?'dark':'light')}catch(_){}
  syncThemeButton();
}
const accountDialog=document.getElementById('accountDialog');
let dialogKind=null,dialogReturnFocus=null,loginCompleted=false;
const draftFields={
  login:['loginAlias','loginMethod'],
  import:['authImportFormat','authImportAlias','authImportText'],
  'add-api':['profileName','profileBaseUrl','profileApiKey','profileUsageUrl','profileModel','profileReasoning','profileCockpitHeaders','profileAdvanced','profileImportText']
};
function dialogDraftKey(kind=dialogKind,id=editingProfileId){return 'codex-account-draft:'+kind+(kind==='add-api'?':'+(id||'new'):'')}
function saveDialogDraft(){
  if(!dialogKind||dialogKind==='login'&&loginCompleted)return;
  const fields=Object.fromEntries(draftFields[dialogKind].map(id=>{const input=document.getElementById(id);return [id,input.type==='checkbox'?input.checked:input.value]}));
  const draft={fields};
  if(dialogKind==='add-api')Object.assign(draft,{profileHeaders,profileHiddenFields,profileCockpitChanged});
  try{sessionStorage.setItem(dialogDraftKey(),JSON.stringify(draft));document.getElementById('draftState').textContent='Draft saved in this tab.'}
  catch(_){document.getElementById('draftState').textContent='Draft kept while this page stays open.'}
}
function restoreDialogDraft(kind,id=null){
  try{
    const raw=sessionStorage.getItem(dialogDraftKey(kind,id));if(!raw)return;
    const draft=JSON.parse(raw);
    for(const field of draftFields[kind])if(Object.hasOwn(draft.fields||{},field)){
      const input=document.getElementById(field);if(input.type==='checkbox')input.checked=draft.fields[field]===true;else input.value=String(draft.fields[field]);
    }
    if(kind==='add-api'){
      profileHeaders=draft.profileHeaders||{};profileHiddenFields=draft.profileHiddenFields||{approval_policy:'',sandbox_mode:'',model_catalog_json:''};profileCockpitChanged=draft.profileCockpitChanged===true;
    }
  }catch(_){}
}
function removeDialogDraft(kind,id=null){try{sessionStorage.removeItem(dialogDraftKey(kind,id))}catch(_){}}
function openAccountDialog(kind='login',editId=null,preserveForm=false){
  if(!Object.hasOwn(draftFields,kind))return;
  const opening=!accountDialog.classList.contains('open');
  if(!opening)saveDialogDraft();else dialogReturnFocus=document.activeElement;
  showView(kind==='add-api'?'api-accounts':'accounts');
  dialogKind=kind;
  if(kind==='add-api'&&!preserveForm)cancelProfileEdit();
  if(kind==='login'&&loginCompleted){document.getElementById('loginAlias').value='';document.getElementById('loginMethod').value='browser';document.getElementById('loginSession').hidden=true;loginCompleted=false}
  restoreDialogDraft(kind,editId);
  document.querySelectorAll('.dialog-pane').forEach(pane=>pane.hidden=pane.id!=='view-'+kind);
  document.getElementById('nativeDialogTabs').hidden=kind==='add-api';
  document.getElementById('dialogLoginTab').classList.toggle('active',kind==='login');
  document.getElementById('dialogImportTab').classList.toggle('active',kind==='import');
  document.getElementById('accountDialogTitle').textContent=kind==='add-api'?(editingProfileId?'Edit API account':'Add API account'):'Add account';
  document.getElementById('dialogBanner').className='banner';
  accountDialog.classList.add('open');
  document.querySelector('.app-shell').inert=true;document.querySelector('.app-shell').classList.add('obscured');
  document.body.style.overflow='hidden';
  try{sessionStorage.setItem('codex-account-dialog',JSON.stringify({kind,id:editingProfileId}))}catch(_){}
  document.getElementById('draftState').textContent='';
  saveDialogDraft();
  const first=[...accountDialog.querySelectorAll('.dialog-pane:not([hidden]) input:not(:disabled),.dialog-pane:not([hidden]) select:not(:disabled)')].find(input=>input.offsetParent!==null);
  (first||accountDialog.querySelector('button')).focus({preventScroll:true});
}
function closeAccountDialog(save=true){
  if(save)saveDialogDraft();
  accountDialog.classList.remove('open');document.querySelector('.app-shell').inert=false;document.querySelector('.app-shell').classList.remove('obscured');
  document.body.style.overflow='';
  try{sessionStorage.removeItem('codex-account-dialog')}catch(_){}
  if(dialogReturnFocus?.isConnected&&!dialogReturnFocus.closest('[hidden]'))dialogReturnFocus.focus({preventScroll:true});
}
function discardDialogDraft(){
  if(dialogKind==='login'&&document.getElementById('loginStart').disabled){show('Cancel the current login before discarding its draft.',false);return}
  removeDialogDraft(dialogKind,editingProfileId);
  if(dialogKind==='add-api'&&editingProfileId){const id=editingProfileId;closeAccountDialog(false);openProfileEditor(id);return}
  if(dialogKind==='add-api')cancelProfileEdit();
  else for(const id of draftFields[dialogKind]){const field=document.getElementById(id);field.value=field.tagName==='SELECT'?field.options[0].value:''}
  document.getElementById('draftState').textContent='Draft discarded.';
}
accountDialog.addEventListener('input',saveDialogDraft);
accountDialog.addEventListener('change',saveDialogDraft);
window.addEventListener('pagehide',()=>{if(accountDialog.classList.contains('open'))saveDialogDraft()});
const exportDialog=document.getElementById('exportDialog');
let exportReturnFocus=null;
function toggleExport(kind){
  if(exportDialog.classList.contains('open')){closeExportDialog();return}
  if(accountDialog.classList.contains('open'))closeAccountDialog();
  exportReturnFocus=document.activeElement;
  const api=kind==='api';
  document.getElementById('view-export').hidden=api;
  document.getElementById('apiExportPanel').hidden=!api;
  document.getElementById('exportDialogTitle').textContent=api?'Export API accounts':'Export accounts';
  document.getElementById('exportBanner').className='banner';document.getElementById('exportBanner').textContent='';
  document.getElementById(api?'toggleApiExport':'toggleNativeExport').setAttribute('aria-expanded','true');
  exportDialog.classList.add('open');
  document.querySelector('.app-shell').inert=true;document.querySelector('.app-shell').classList.add('obscured');
  document.body.style.overflow='hidden';
  document.getElementById(api?'apiExportScope':'exportFormat').focus({preventScroll:true});
}
function closeExportDialog(){
  exportDialog.classList.remove('open');
  document.getElementById('view-export').hidden=true;document.getElementById('apiExportPanel').hidden=true;
  for(const id of ['toggleNativeExport','toggleApiExport'])document.getElementById(id).setAttribute('aria-expanded','false');
  document.querySelector('.app-shell').inert=false;document.querySelector('.app-shell').classList.remove('obscured');
  document.body.style.overflow='';
  if(exportReturnFocus?.isConnected)exportReturnFocus.focus({preventScroll:true});
}
function showView(name,button){
  if(['login','import','add-api'].includes(name)){openAccountDialog(name);return}
  if(name==='export'){showView('accounts');if(document.getElementById('view-export').hidden)toggleExport('accounts');return}
  if(exportDialog.classList.contains('open'))closeExportDialog();
  if(!viewDetails[name])name='dashboard';
  button=document.querySelector(`.tabs button[data-view="${name}"]`);
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+name));
  document.querySelectorAll('.tabs button').forEach(b=>{
    b.classList.toggle('active',b===button);
    if(b===button)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
  });
  document.getElementById('pageTitle').textContent=viewDetails[name];
  document.title=viewDetails[name]+' · Codex Account Manager';
  const wasOpen=document.getElementById('sidebar').classList.contains('open');
  setSidebar(false);
  if(wasOpen)document.getElementById('mainContent').focus({preventScroll:true});
  window.scrollTo(0,0);
  history.replaceState(null,'','#'+name);
  if(name==='dashboard'||name==='activity')loadActivity();
}
window.addEventListener('hashchange',()=>showView(location.hash.slice(1)));
matchMedia('(min-width: 1280px)').addEventListener('change',()=>setSidebar(false));
matchMedia('(prefers-color-scheme: dark)').addEventListener('change',event=>{
  let saved=null;try{saved=localStorage.getItem('codex-ui-theme')}catch(_){}
  if(!saved){document.documentElement.classList.toggle('dark',event.matches);syncThemeButton()}
});
let aliasReturnFocus=null;
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){
    if(aliasModal.classList.contains('open'))closeAliasEditor();
    else if(exportDialog.classList.contains('open'))closeExportDialog();
    else if(accountDialog.classList.contains('open'))closeAccountDialog();
    else if(document.getElementById('sidebar').classList.contains('open')){setSidebar(false);document.getElementById('sidebarToggle').focus()}
  }
  const activeDialog=aliasModal.classList.contains('open')?aliasModal:exportDialog.classList.contains('open')?exportDialog:accountDialog.classList.contains('open')?accountDialog:null;
  if(event.key==='Tab'&&activeDialog){
    const elements=[...activeDialog.querySelectorAll('button,input,select,textarea,a[href],summary')].filter(e=>!e.disabled&&e.offsetParent!==null);
    const first=elements[0],last=elements[elements.length-1];
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus()}
  }
});
syncThemeButton();
function pct(v){
  if(v===null||v===undefined||v==='')return null;
  if(typeof v==='number')return Math.max(0,Math.min(100,v));
  const m=String(v).match(/(-?\d+(?:\.\d+)?)\s*%/);
  return m?Math.max(0,Math.min(100,Number(m[1]))):null
}
function utcResetDate(value){
  if(value===null||value===undefined||value==='')return null;
  if(typeof value==='number')return new Date(value>(1e12)?value:value*1000);
  const raw=String(value).trim();
  if(/^\d{4}-\d\d-\d\d[T ]/.test(raw)){const d=new Date(raw.endsWith('Z')||/[+-]\d\d:?\d\d$/.test(raw)?raw:raw+'Z');return isNaN(d)?null:d}
  const m=raw.match(/^(\d{1,2}):(\d{2})(?:\s+on\s+(\d{1,2})\s+([A-Za-z]{3}))?$/i);
  if(!m)return null;
  const months={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};
  const now=new Date();let d;
  if(m[3]){const month=months[m[4].toLowerCase()];if(month===undefined)return null;d=new Date(Date.UTC(now.getUTCFullYear(),month,Number(m[3]),Number(m[1]),Number(m[2])));if(d.getTime()<now.getTime()-183*86400000)d.setUTCFullYear(d.getUTCFullYear()+1)}
  else{d=new Date(Date.UTC(now.getUTCFullYear(),now.getUTCMonth(),now.getUTCDate(),Number(m[1]),Number(m[2])));if(d.getTime()<now.getTime()-300000)d.setUTCDate(d.getUTCDate()+1)}
  return d
}
function localTime(value,withDate=false){
  const d=utcResetDate(value);if(!d||isNaN(d))return String(value??'—');
  return new Intl.DateTimeFormat(undefined,withDate?{dateStyle:'medium',timeStyle:'short'}:{hour:'2-digit',minute:'2-digit'}).format(d)
}
function usageResetTime(value){
  const d=utcResetDate(value);if(!d||isNaN(d))return String(value??'—');
  const time=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit'}).format(d);
  const day=new Intl.DateTimeFormat(undefined,{day:'numeric'}).format(d);
  const month=new Intl.DateTimeFormat(undefined,{month:'short'}).format(d);
  return `${time} on ${day} ${month}`
}
function localizeUsageText(value){
  const raw=String(value??'—');
  return raw.replace(/\((\d{1,2}:\d{2}(?:\s+on\s+\d{1,2}\s+[A-Za-z]{3})?)\)/i,(_,reset)=>`(${usageResetTime(reset)})`)
}
function metric(label,obj){
  let text='—',p=null;
  if(obj&&typeof obj==='object'){
    for(const x of [obj.remaining_percent,obj.remaining_pct,obj.percent_remaining,obj.remaining,obj.value]){
      if(x!==undefined&&x!==null){p=pct(x);if(p!==null)break}
    }
    if(p===null&&obj.used_percent!==undefined&&obj.used_percent!==null){const used=pct(obj.used_percent);if(used!==null)p=100-used}
    text=p!==null?`${p}%`:(obj.display||obj.status||'—');
    if(obj.resets_at||obj.reset_at)text+=` (${usageResetTime(obj.resets_at||obj.reset_at)})`
  }else if(obj!==undefined&&obj!==null){text=localizeUsageText(obj);p=pct(obj)}
  return `<div class="metric"><div class="label">${esc(label)}</div><div class="value">${esc(text)}</div>${p!==null?`<div class="bar ${p<20?'low':p<50?'medium':''}"><i style="width:${p}%"></i></div>`:''}</div>`
}
function usageField(a,windowMinutes,names){
  const u=a.usage||{};
  for(const value of Object.values(u))if(value&&typeof value==='object'&&Number(value.window_minutes)===windowMinutes)return value;
  for(const n of names){
    if(u[n]===undefined)continue;
    const value=u[n];
    if(value&&typeof value==='object'&&value.window_minutes!==undefined&&Number(value.window_minutes)!==windowMinutes)continue;
    return value
  }
  return null
}
function usageRefreshStatus(a){
  const u=a.usage||{},r=u.refresh||{};
  const failed=r.status&&r.status!=='ok'&&r.requested===true;
  const cached=u.source==='cache';
  const details=[];
  if(r.status)details.push(String(r.status));
  if(r.http_status!==null&&r.http_status!==undefined)details.push(`HTTP ${r.http_status}`);
  if(r.error_code)details.push(String(r.error_code));
  let message=failed?`Usage refresh failed: ${details.join(' · ')}`:(cached?'Cached usage':'');
  if(!message)return '';
  if(failed&&cached)message+=' — showing cached usage';
  if(r.error_code==='token_revoked')message+=' — please sign in again';
  if(u.updated_at!==null&&u.updated_at!==undefined)message+=` · Last updated: ${localTime(u.updated_at,true)}`;
  return `<div class="usage-refresh-status ${failed?'failed':''}" role="status">${esc(message)}</div>`
}
function render(data){
  lastAccountData=data;updateOverview();renderExportAccounts();
  const accounts=data.accounts||[];
  meta.textContent='Updated '+new Date().toLocaleTimeString();
  if(!accounts.length){grid.innerHTML='<div class="empty">No accounts yet. Open “Add account / Login” to sign in.</div>';return}
  grid.innerHTML=accounts.map(a=>{
    const selector=a.account_key||a.email||a.alias||a.number;
    const stableSelector=selector;
    const fullyActive=a.active&&profilesState.active_profile==='default';
    const five=usageField(a,300,['five_hour','5h','primary','session','session_5h']);
    const weekly=usageField(a,10080,['weekly','7d','secondary','week']);
    const pingState=a.auto_ping_effective?(a.auto_ping_next_reset_at?`Next: ${localTime(a.auto_ping_next_reset_at,true)}`:'Waiting for reset time'):(autoPingGlobal?'Disabled for this account':'Disabled globally');
    const lastPing=a.auto_ping_last_ping_at?`Last ping: ${localTime(a.auto_ping_last_ping_at,true)}`:'Last ping: Never';
    return `<div class="card ${fullyActive?'active':''}">
      <div class="identity">
        <div class="name">${esc(a.alias||a.account_name||a.email||('Account '+(a.number||'')))}</div>
        <div class="email">${esc(a.email||a.account_key||'')}</div>
        <div class="ping-control"><span>Auto Ping<div class="auto-ping-state"><div>${esc(pingState)}</div><div>${esc(lastPing)}</div>${a.auto_ping_next_retry_at?`<div>Retry: ${esc(localTime(a.auto_ping_next_retry_at,true))}</div>`:''}${a.auto_ping_last_error?`<div class="ping-error">Last error: ${esc(a.auto_ping_last_error)}</div>`:''}</div></span><label class="switch" title="Auto Ping"><input type="checkbox" aria-label="Auto Ping" ${a.auto_ping_configured?'checked':''} onchange='setAccountAutoPing(${jsArg(selector)},this)'><span class="switch-track"></span></label></div>
        ${fullyActive?'<span class="badge active">ACTIVE ACCOUNT</span>':''}
      </div>
      <div class="account-details"><div class="plan">${esc(a.plan||'—')}</div>${a.usage?.reset_credits!=null?`<div class="metric account-resets"><span class="label">Available resets</span><span class="value">${esc(a.usage.reset_credits)}</span></div>`:''}</div>
      <div class="usage-stack">${metric('5-hour remaining',five)}${metric('Weekly remaining',weekly)}${usageRefreshStatus(a)}</div>
      <div class="account-actions">
        <button class="test-button" onclick='pingAccount(${jsArg(selector)},this)'>Ping</button>
        <button class="alias-button" onclick='openAliasEditor(${jsArg(stableSelector)},${jsArg(selector)},${jsArg(a.alias||'')})'>${a.alias?'Edit alias':'Set alias'}</button>
        <button ${fullyActive?'disabled':''} onclick='useCodexAuth(${jsArg(selector)},${a.active?'true':'false'},this)'>${fullyActive?'Active':'Switch account'}</button>
        <button class="danger" ${a.active?'disabled':''} onclick='removeAccount(${jsArg(selector)},this)'>Remove</button>
      </div>
    </div>`
  }).join('')
}

let loginSessionId=null,loginTimer=null;
try{loginSessionId=sessionStorage.getItem('codex-login-session')}catch(e){}
function rememberLogin(id){try{if(id)sessionStorage.setItem('codex-login-session',id);else sessionStorage.removeItem('codex-login-session')}catch(e){}}
function renderLogin(session){
  document.getElementById('loginSession').hidden=false;
  const pending=['pending','exchanging'].includes(session.status);
  document.getElementById('loginStart').disabled=pending;
  document.getElementById('loginMethod').disabled=pending;
  document.getElementById('loginAlias').disabled=pending;
  if(session.method)document.getElementById('loginMethod').value=session.method;
  if(session.alias!==undefined)document.getElementById('loginAlias').value=session.alias;
  document.getElementById('loginCancel').hidden=!pending;
  const link=document.getElementById('loginLink');
  const url=pending&&session.url?session.url:'';
  if(url)link.href=url;else link.removeAttribute('href');
  document.getElementById('loginUrl').value=url;
  document.getElementById('loginLinkBox').hidden=!url;
  document.getElementById('loginCopyStatus').textContent='';
  document.getElementById('loginMessage').textContent=session.message||(session.status==='exchanging'?'Completing login…':'Waiting for sign-in. Expires '+new Date(session.expires_at*1000).toLocaleTimeString());
  document.getElementById('loginWarning').textContent=session.warning||'';
  document.getElementById('loginDeviceCode').textContent=pending&&session.user_code?'Code: '+session.user_code:'';
  document.getElementById('loginDeviceCode').hidden=!pending||!session.user_code;
  const callbackHidden=session.status!=='pending'||session.method==='device';
  document.getElementById('loginCallbackForm').hidden=callbackHidden;
  document.getElementById('loginComplete').hidden=callbackHidden;
  if(!pending){clearTimeout(loginTimer);rememberLogin(null);document.getElementById('loginCallback').value='';if(session.status==='success'){loginCompleted=true;removeDialogDraft('login')}}
  return pending
}
async function copyLoginLink(button){
  const input=document.getElementById('loginUrl'),value=input.value;
  if(!value)return;
  button.disabled=true;
  let copied=false;
  try{
    if(!navigator.clipboard?.writeText)throw new Error('Clipboard unavailable');
    await navigator.clipboard.writeText(value);copied=true;
  }catch(error){
    input.focus();input.select();
    try{copied=document.execCommand('copy')}catch(error){}
  }finally{
    button.disabled=false;
    document.getElementById('loginCopyStatus').textContent=copied?'Link copied.':'Select the link and copy it manually.';
  }
}
async function startLogin(){
  loginCompleted=false;saveDialogDraft();
  clearBanner();document.getElementById('loginStart').disabled=true;
  try{
    const d=await post('/api/login/start',{method:document.getElementById('loginMethod').value,alias:document.getElementById('loginAlias').value});
    loginSessionId=d.login.id;rememberLogin(loginSessionId);renderLogin(d.login);pollLogin()
  }catch(e){document.getElementById('loginStart').disabled=false;show(e.message,false)}
}
async function pollLogin(){
  clearTimeout(loginTimer);
  const id=loginSessionId;
  try{
    const r=await fetch('/api/login/status?id='+encodeURIComponent(id),{cache:'no-store'}),d=await r.json();
    if(r.status===400){renderLogin({status:'error',message:d.error||'Login session expired.'});return}
    if(!r.ok||!d.ok)throw new Error(d.error||'Could not check login');
    if(id!==loginSessionId)return;
    if(renderLogin(d.login))loginTimer=setTimeout(pollLogin,1500);
    else if(d.login.status==='success'){await loadAccounts(false);show(d.login.message)}
  }catch(e){show(e.message,false);if(id===loginSessionId)loginTimer=setTimeout(pollLogin,5000)}
}
async function completeLogin(button){
  button.disabled=true;
  try{
    const callback=document.getElementById('loginCallback').value;
    document.getElementById('loginCallback').value='';
    const d=await post('/api/login/complete',{id:loginSessionId,callback});
    renderLogin(d.login);await pollLogin()
  }catch(e){show(e.message,false)}finally{button.disabled=false}
}
async function cancelLogin(){
  try{const d=await post('/api/login/cancel',{id:loginSessionId});clearTimeout(loginTimer);renderLogin(d.login)}catch(e){show(e.message,false)}
}
async function removeAccount(query,button){
  if(!confirm('Remove this saved account?'))return;
  button.disabled=true;
  try{const d=await post('/api/account-remove',{query});await loadAccounts(false);show(d.message)}catch(e){show(e.message,false)}finally{button.disabled=false}
}

let exporting=false;
function renderExportAccounts(){
  const list=document.getElementById('exportAccountsList');
  const selected=new Set([...list.querySelectorAll('.export-account:checked')].map(input=>input.value));
  const accounts=lastAccountData?.accounts||[];
  list.innerHTML=accounts.map(a=>{
    const selector=String(a.account_key||a.email||a.alias||a.number);
    const name=a.alias||a.account_name||a.email||('Account '+(a.number||''));
    return `<label class="export-row"><input class="export-account" type="checkbox" value="${esc(selector)}" ${selected.has(selector)?'checked':''} onchange="document.getElementById('exportScope').value='selected';updateExportControls()"><span><span class="list-title">${esc(name)}</span><span class="email">${esc(a.email||a.account_key||'')}</span></span></label>`;
  }).join('')||'<div class="empty">No accounts to export.</div>';
  updateExportControls();
}
function updateExportControls(){
  const count=document.querySelectorAll('#exportAccountsList .export-account:checked').length;
  const total=lastAccountData?.accounts?.length||0;
  const all=document.getElementById('exportScope').value==='all';
  document.getElementById('exportSelectionState').textContent=all?`${total} account(s) will be exported.`:`${count} of ${total} account(s) selected.`;
  document.getElementById('exportButton').disabled=exporting||!total||(!all&&!count);
}
async function exportAccounts(button){
  const all=document.getElementById('exportScope').value==='all';
  const queries=[...document.querySelectorAll('#exportAccountsList .export-account:checked')].map(input=>input.value);
  if(!lastAccountData?.accounts?.length||(!all&&!queries.length)){show('Select at least one account to export.',false);return}
  if(!confirm('Export includes authentication secrets. Save the downloaded file privately.'))return;
  exporting=true;button.disabled=true;
  try{
    const format=document.getElementById('exportFormat').value;
    const d=await post('/api/export-accounts',{queries:all?null:queries,format});
    const url=URL.createObjectURL(new Blob([JSON.stringify(d.documents,null,2)+'\n'],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='codex-accounts-'+format+'.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    show('Exported '+d.count+' account(s). The file contains credentials.');
  }catch(e){show(e.message,false)}finally{exporting=false;updateExportControls()}
}
let apiExporting=false;
function renderApiExportAccounts(){
  const list=document.getElementById('apiExportList');
  const selected=new Set([...list.querySelectorAll('.api-export-account:checked')].map(input=>input.value));
  list.innerHTML=(profilesState.profiles||[]).filter(p=>p.kind==='api').map(p=>`<label class="export-row"><input class="api-export-account" type="checkbox" value="${esc(p.id)}" ${selected.has(p.id)?'checked':''} onchange="document.getElementById('apiExportScope').value='selected';updateApiExportControls()"><span><span class="list-title">${esc(p.name)}</span><span class="email">${esc(p.base_url||'')}</span></span></label>`).join('')||'<div class="empty">No API accounts to export.</div>';
  updateApiExportControls();
}
function updateApiExportControls(){
  const total=(profilesState.profiles||[]).filter(p=>p.kind==='api').length;
  const selected=document.querySelectorAll('.api-export-account:checked').length;
  const all=document.getElementById('apiExportScope').value==='all';
  document.getElementById('apiExportSelectionState').textContent=all?`${total} API account(s) will be exported.`:`${selected} of ${total} API account(s) selected.`;
  document.getElementById('apiExportButton').disabled=apiExporting||!total||(!all&&!selected);
}
async function exportApiAccounts(button){
  const all=document.getElementById('apiExportScope').value==='all';
  const profile_ids=[...document.querySelectorAll('.api-export-account:checked')].map(input=>input.value);
  if(!(profilesState.profiles||[]).some(p=>p.kind==='api')||!all&&!profile_ids.length){show('Select at least one API account.',false);return}
  if(!confirm('API export includes API keys. Save the downloaded file privately.'))return;
  apiExporting=true;button.disabled=true;
  try{
    const data=await post('/api/profiles/export',{profile_ids:all?null:profile_ids});
    const url=URL.createObjectURL(new Blob([JSON.stringify(data.documents,null,2)+'\n'],{type:'application/json'}));
    const anchor=document.createElement('a');anchor.href=url;anchor.download='codex-api-accounts.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    show('Exported '+data.count+' API account(s). The file contains API keys.');
  }catch(error){show(error.message,false)}finally{apiExporting=false;updateApiExportControls()}
}

async function runDoctor(fix,button){
  button.disabled=true;
  try{const d=await post('/api/doctor',{fix_permissions:fix});document.getElementById('doctorReport').textContent=JSON.stringify(d,null,2)}catch(e){show(e.message,false)}finally{button.disabled=false}
}
async function runAutoPingOnce(button){
  button.disabled=true;
  try{const d=await post('/api/auto-ping/once');document.getElementById('autoPingReport').textContent=JSON.stringify(d,null,2);await loadAccounts(false)}catch(e){show(e.message,false)}finally{button.disabled=false}
}
async function restartSelectedServices(button){
  if(!confirm('Run the saved restart commands for Codex app server?'))return;
  button.disabled=true;
  try{const d=await post('/api/restart-services',{service:document.getElementById('restartService').value});show(d.message);showServiceOutput(d.output)}catch(e){show(e.message,false);showServiceOutput(e.output)}finally{button.disabled=false}
}
async function setWebUI(input){
  if(input.checked)return;
  if(!confirm('Stop and disable WebUI? Re-enable with: python3 app.py web start')){input.checked=true;return}
  try{
    await post('/api/config',{name:'webui_enabled',value:false});clearTimeout(loginTimer);
    document.querySelectorAll('button,input,select,textarea').forEach(el=>el.disabled=true);
    show('WebUI disabled and stopping. To return, run: python3 app.py web start');
  }catch(e){input.checked=true;show(e.message,false)}
}

const dashboardPending=new Set();
let dashboardSwitching=false;
let dashboardAccountsState=[];
function renderDashboardAccounts(accounts){
  document.getElementById('dashboardAccounts').innerHTML=accounts.map(account=>{
    const ping=account.ping||{};
    const duration=ping.last_duration_ms===undefined?'':` · ${(ping.last_duration_ms/1000).toLocaleString(undefined,{maximumFractionDigits:2})} s`;
    const status=ping.last_result?(ping.last_result==='ok'?'Ping OK':'Ping failed')+duration:(account.kind==='api'?(account.switch?.last_at?'Switched '+localTime(account.switch.last_at):'No switch yet'):'No Ping yet');
    const pending=action=>dashboardPending.has(account.kind+':'+account.id+':'+action);
    const handler=action=>`dashboardAction(${jsArg(account.kind)},${jsArg(account.id)},${jsArg(action)},${account.auth_active?'true':'false'},this)`;
    return `<div class="dashboard-account ${account.active?'active':''}" role="group" aria-label="${esc(account.name)}" data-kind="${esc(account.kind)}" data-account-id="${esc(account.id)}">
      <div class="dashboard-identity"><span class="dashboard-type">${account.kind==='api'?'API':'C'}</span><div><div class="name">${esc(account.name)}</div><span class="sub">${account.kind==='api'?'API':'ChatGPT'}</span>${account.active?'<span class="dashboard-active">Active</span>':''}</div></div>
      ${account.kind==='account'?`<div class="dashboard-usage">${metric('5-hour remaining',usageField(account,300,['five_hour','5h','primary','session','session_5h']))}${metric('Weekly remaining',usageField(account,10080,['weekly','7d','secondary','week']))}${usageRefreshStatus(account)}</div>`:''}
      <div class="dashboard-ping-status ${ping.last_result==='error'?'failed':''}">${esc(status)}</div>
      <div class="dashboard-account-actions"><button ${!account.ping_available||pending('ping')?'disabled':''} ${!account.ping_available?'title="Ping is currently available for native accounts only"':''} onclick='${handler('ping')}'>${pending('ping')?'Pinging…':'Ping'}</button><button class="primary" data-dashboard-switch ${account.active||dashboardSwitching?'disabled':''} onclick='${handler('switch')}'>${account.active?'Active':pending('switch')?'Switching…':'Switch'}</button></div>
    </div>`;
  }).join('')||'<div class="empty">No accounts yet. Add an account from Accounts or API accounts.</div>';
}
async function dashboardAction(kind,id,action,authActive,button){
  if(!['account','api'].includes(kind)||!['ping','switch'].includes(action)||kind==='api'&&action==='ping')return;
  const key=kind+':'+id+':'+action;
  if(dashboardPending.has(key)||action==='switch'&&dashboardSwitching)return;
  dashboardPending.add(key);
  button.disabled=true;button.textContent=action==='ping'?'Pinging…':'Switching…';
  if(action==='switch'){dashboardSwitching=true;document.querySelectorAll('[data-dashboard-switch]').forEach(input=>input.disabled=true)}
  try{
    if(action==='ping')await pingAccount(id,button);
    else if(kind==='api')await applyProfile(id,button);
    else await useCodexAuth(id,authActive,button);
  }finally{
    dashboardPending.delete(key);if(action==='switch')dashboardSwitching=false;
    renderDashboardAccounts(dashboardAccountsState);
    await loadActivity();
  }
}

function renderActivity(data){
  dashboardAccountsState=data.accounts||[];renderDashboardAccounts(dashboardAccountsState);
  let pings=0,switches=0,failed=0;
  const rows=(data.accounts||[]).map(account=>{
    const ping=account.ping||{},sw=account.switch||{};
    pings+=ping.success||0;switches+=sw.success||0;failed+=(ping.failed||0)+(sw.failed||0);
    const counts=metric=>`${metric.success||0} / ${metric.failed||0}`;
    const at=metric=>metric.last_at?esc(localTime(metric.last_at,true)):'—';
    const duration=ping.last_duration_ms===undefined?'—':`${(ping.last_duration_ms/1000).toLocaleString(undefined,{maximumFractionDigits:2})} s`;
    return `<tr><td><span class="list-title">${esc(account.name)}</span><span class="email">${account.kind==='api'?'API':'ChatGPT'}</span></td><td>${account.ping_available?counts(ping):'<span title="Ping is available for native accounts; API profiles currently support switching.">—</span>'}</td><td>${account.ping_available?at(ping):'—'}</td><td>${account.ping_available?esc(duration):'—'}</td><td>${counts(sw)}</td><td>${at(sw)}</td></tr>`;
  }).join('');
  document.getElementById('statPings').textContent=pings;
  document.getElementById('statSwitches').textContent=switches;
  document.getElementById('statFailures').textContent=failed;
  document.getElementById('activitySince').textContent='Tracking since '+localTime(data.tracking_since,true);
  document.getElementById('activityTable').innerHTML=rows?`<table><thead><tr><th>Account</th><th>Ping OK / failed</th><th>Last Ping</th><th>Last Ping duration</th><th>Switch OK / failed</th><th>Last switch</th></tr></thead><tbody>${rows}</tbody></table>`:'<div class="empty">No accounts yet.</div>';
}
async function loadActivity(){
  try{
    const response=await fetch('/api/activity',{cache:'no-store'}),data=await response.json();
    if(!response.ok||!data.ok)throw new Error(data.error||'Could not load activity statistics');
    renderActivity(data);
  }catch(error){document.getElementById('activityTable').textContent=error.message;show(error.message,false)}
}

async function refreshWorkspace(refresh){
  clearBanner();
  const buttons=[document.getElementById('refreshWorkspace')];
  buttons.forEach(button=>button.disabled=true);
  try{
    // Read profile/config changes made by CLI before deciding which account is active.
    const results=await Promise.allSettled([loadStatus(),loadProfiles()]);
    await loadAccounts(refresh);
    await loadActivity();
    for(const result of results)if(result.status==='rejected')show(result.reason.message,false);
  }finally{buttons.forEach(button=>button.disabled=false)}
}

async function refreshPageUsage(kind,button){
  if(button.disabled)return;
  const old=button.textContent;button.disabled=true;button.textContent='Refreshing…';
  clearBanner();
  try{
    if(kind==='api'){
      for(const card of document.querySelectorAll('#apiAccountGrid .api-card')){
        const usageButton=card.querySelector('[data-api-usage]');
        const box=card.querySelector('.api-usage');
        if(usageButton&&box)await loadApiUsage(box.id.slice('apiUsage-'.length),usageButton,true);
      }
    }else{
      await loadAccounts(true);await loadActivity();
    }
  }catch(error){show(error.message,false)}
  finally{button.disabled=false;button.textContent=old}
}

async function loadAccounts(refresh){
  grid.style.opacity=.55;
  try{
    const r=await fetch('/api/accounts?refresh='+(refresh?'1':'0'),{cache:'no-store'});
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'Request failed');
    autoPingGlobal=d.auto_ping_enabled===true;
    render(d);
    if(d.warning)show(d.warning,false)
  }catch(e){show(e.message,false)}
  finally{grid.style.opacity=1}
}

let restartCommandsDirty=false;
let restartCommandDefaults=null;
function fillRestartCommands(commands){
  document.getElementById('restartAppCommands').value=(commands.app||[]).join('\n');
}
function restoreRestartCommandDefaults(){
  if(!restartCommandDefaults)return;
  fillRestartCommands(restartCommandDefaults);restartCommandsDirty=true;
  document.getElementById('restartCommandsState').textContent='Default commands loaded. Save to apply.';
}
async function saveRestartCommands(button){
  clearBanner();
  const lines=id=>document.getElementById(id).value.split('\n').map(line=>line.trim()).filter(Boolean);
  const commands={app:lines('restartAppCommands')};
  const inputs=['restartAppCommands','defaultRestartCommands'].map(id=>document.getElementById(id));
  button.disabled=true;inputs.forEach(input=>input.disabled=true);
  document.getElementById('restartCommandsState').textContent='Saving…';
  try{
    const data=await post('/api/config/restart-commands',{commands});
    fillRestartCommands(data.restart_commands);restartCommandsDirty=false;
    document.getElementById('restartCommandsState').textContent=data.message;
  }catch(error){document.getElementById('restartCommandsState').textContent=error.message;show(error.message,false)}
  finally{button.disabled=false;inputs.forEach(input=>input.disabled=false)}
}
async function loadStatus(){
  try{
    const r=await fetch('/api/status',{cache:'no-store'});
    const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'Status failed');
    const syncToggle=(toggleId,stateId,value,description='')=>{const input=document.getElementById(toggleId);input.checked=value===true;document.getElementById(stateId).textContent=description||(value?'Enabled':'Disabled')};
    restartAfterSwitch=d.restart_after_switch_enabled!==false;
    syncToggle('webuiToggle','webuiState',d.webui?.enabled===true,'Disabling stops this server. CLI stays available.');
    syncToggle('restartAfterSwitchToggle','restartAfterSwitchState',restartAfterSwitch);
    autoPingGlobal=d.auto_ping_enabled===true;
    syncToggle('autoPingToggle','autoPingState',autoPingGlobal,'Ping selected accounts after their 5-hour reset.');
    document.getElementById('statusRaw').textContent=JSON.stringify(d.webui,null,2);
    const configResponse=await fetch('/api/config',{cache:'no-store'});const config=await configResponse.json();
    if(!configResponse.ok||!config.ok)throw new Error(config.error||'Could not load Auto Ping status');
    document.getElementById('autoPingReport').textContent=JSON.stringify(config.auto_ping,null,2);
    restartCommandDefaults=config.restart_command_defaults;
    if(!restartCommandsDirty)fillRestartCommands(config.restart_commands);
    for(const id of ['restartAppCommands','saveRestartCommands','defaultRestartCommands'])document.getElementById(id).disabled=false;
  }catch(e){show(e.message,false)}
}

function renderProfiles(){
  renderApiExportAccounts();
  updateOverview();
  const profiles=profilesState.profiles||[];
  const apiProfiles=profiles.filter(p=>p.kind==='api');
  const active=profiles.find(p=>p.id===profilesState.active_profile);
  document.getElementById('activeProfileState').textContent='Active config: '+(active?active.name:profilesState.active_profile||'default');
  document.getElementById('apiAccountGrid').innerHTML=apiProfiles.map(p=>`<div class="api-card ${p.id===profilesState.active_profile?'active':''}"><div class="identity"><div class="name">${esc(p.name)}</div><div class="email">${esc(p.base_url||'')}</div>${p.id===profilesState.active_profile?'<span class="badge active">ACTIVE API ACCOUNT</span>':''}</div><div class="list-meta">${esc(p.model||'Codex default')} · ${esc(p.reasoning_effort||'Codex default')} · ${esc(p.sandbox_mode||'Codex default')}</div><div class="account-actions"><button class="primary" ${p.id===profilesState.active_profile?'disabled':''} onclick='applyProfile(${jsArg(p.id)},this)'>Select API account</button><button data-api-usage onclick='loadApiUsage(${jsArg(p.id)},this)'>Usage</button><button onclick='openProfileEditor(${jsArg(p.id)})'>Edit</button><button class="danger" onclick='deleteProfile(${jsArg(p.id)},this)'>Delete</button></div><div id="apiUsage-${esc(p.id)}" class="api-usage" hidden></div></div>`).join('')||'<div class="empty">No API accounts yet. Add one from the “Add API account” menu.</div>';
  document.getElementById('profileList').innerHTML=apiProfiles.map(p=>`<div class="list-row"><div><div class="list-title">${esc(p.name)}</div><div class="list-meta">${esc(p.model||'Codex default')} · ${esc(p.base_url||'—')}</div></div><div class="list-actions"><button ${p.id===profilesState.active_profile?'disabled':''} onclick='applyProfile(${JSON.stringify(p.id)},this)'>Select</button><button onclick='openProfileEditor(${JSON.stringify(p.id)})'>Edit</button><button class="danger" onclick='deleteProfile(${JSON.stringify(p.id)},this)'>Delete</button></div></div>`).join('')||'<div class="sub">No API accounts created yet.</div>';
}

function renderApiUsage(data){
  const quota=data.quota||{},today=(data.usage||{}).today||{};
  const money=value=>value===undefined||value===null?'—':`${Number(value).toLocaleString(undefined,{maximumFractionDigits:4})} ${data.unit||quota.unit||'USD'}`;
  const item=(label,value)=>`<div class="api-usage-item"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`;
  const expiry=data.expires_at?new Date(data.expires_at):null;
  const expires=expiry&&!isNaN(expiry)?new Intl.DateTimeFormat(undefined,{dateStyle:'medium',timeStyle:'short'}).format(expiry):'—';
  const models=Array.isArray(data.model_stats)?data.model_stats:[];
  const daily=Array.isArray(data.daily_usage)?[...data.daily_usage].sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))):[];
  const last=daily[0];
  const todayEmpty=Number(today.requests||0)===0&&Number(today.total_tokens||0)===0&&Number(today.actual_cost??today.cost??0)===0;
  const note=todayEmpty&&last&&Number(last.requests||0)>0?`The provider reports no usage for today. Latest recorded activity: ${esc(last.date||'unknown date')}.`:'';
  const dailyRows=daily.slice(0,7).map(day=>`<tr><td>${esc(day.date||'—')}</td><td>${esc(day.requests??'—')}</td><td>${esc(day.total_tokens??'—')}</td><td>${esc(money(day.actual_cost??day.cost))}</td></tr>`).join('');
  return `<div class="api-usage-grid">${item('Quota remaining',money(quota.remaining??data.remaining))}${item('Quota used',money(quota.used))}${item('Quota limit',money(quota.limit))}${item('Status',data.status||'—')}${item('Expires',expires)}${item('Today requests (provider)',today.requests??'—')}${item('Today tokens (provider)',today.total_tokens??'—')}${item('Today cost (provider)',money(today.actual_cost??today.cost))}</div>${note?`<div class="api-usage-note">${note}</div>`:''}<div class="api-usage-models">${models.length?models.map(m=>`${esc(m.model||'Model')}: ${esc(m.requests??0)} requests · ${esc(m.total_tokens??0)} tokens · ${esc(money(m.actual_cost??m.cost))}`).join('<br>'):'No model usage yet.'}</div>${dailyRows?`<div class="api-usage-daily"><div>Recent daily usage (dates as reported by provider)</div><table><thead><tr><th>Date</th><th>Requests</th><th>Tokens</th><th>Cost</th></tr></thead><tbody>${dailyRows}</tbody></table></div>`:''}<details class="api-usage-raw"><summary>Raw output</summary><pre>${esc(JSON.stringify(data,null,2))}</pre></details>`;
}
async function loadApiUsage(id,button,refresh=false){
  if(button.disabled)return;
  const box=document.getElementById('apiUsage-'+id);
  if(!refresh&&!box.hidden){box.hidden=true;return}
  button.disabled=true;const old=button.textContent;button.textContent='Loading…';box.hidden=false;box.textContent='Loading usage…';
  try{
    const r=await fetch('/api/profiles/usage?profile_id='+encodeURIComponent(id),{cache:'no-store'});const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'Could not load usage');
    box.innerHTML=renderApiUsage(d.usage);
  }catch(e){box.textContent=e.message}
  finally{button.disabled=false;button.textContent=old}
}

async function loadProfiles(){
  const r=await fetch('/api/profiles',{cache:'no-store'});const d=await r.json();
  if(!r.ok||!d.ok)throw new Error(d.error||'Could not load profiles');
  profilesState=d;renderProfiles();return d
}

function profileFormBody(){
  const advanced=JSON.parse(document.getElementById('profileAdvanced').value||'{}');
  if(!advanced||Array.isArray(advanced)||typeof advanced!=='object')throw new Error('Advanced fields must be a JSON object');
  for(const key of Object.keys(advanced))if(!['http_headers','approval_policy','sandbox_mode','model_catalog_json'].includes(key))throw new Error('Unknown advanced field: '+key);
  for(const key of ['approval_policy','sandbox_mode','model_catalog_json'])if(key in advanced)profileHiddenFields[key]=advanced[key];
  if('http_headers' in advanced){if(!advanced.http_headers||Array.isArray(advanced.http_headers)||typeof advanced.http_headers!=='object')throw new Error('HTTP headers must be a JSON object');profileHeaders=advanced.http_headers}
  for(const key of ['approval_policy','sandbox_mode','model_catalog_json'])if(key in advanced&&typeof advanced[key]!=='string')throw new Error(key+' must be a string');
  const cockpit=document.getElementById('profileCockpitHeaders').checked;
  const headers={...profileHeaders};
  const defaults={'x-openai-actor-authorization':'cockpit-tools','x-agtools-disable-image-generation':'chat','x-cockpit-instance-id':'.codex'};
  if(cockpit){
    for(const [key,value] of Object.entries(defaults))if(!Object.keys(headers).some(name=>name.toLowerCase()===key))headers[key]=value;
  }else if(profileCockpitChanged){
    for(const key of Object.keys(headers))if(key.toLowerCase() in defaults)delete headers[key];
  }
  return {name:document.getElementById('profileName').value,base_url:document.getElementById('profileBaseUrl').value,usage_url:document.getElementById('profileUsageUrl').value,model:document.getElementById('profileModel').value,api_key:document.getElementById('profileApiKey').value,reasoning_effort:document.getElementById('profileReasoning').value,...profileHiddenFields,http_headers:headers};
}
function cancelProfileEdit(){
  editingProfileId=null;
  profileCockpitChanged=false;
  profileHeaders={};
  profileHiddenFields={approval_policy:'',sandbox_mode:'',model_catalog_json:''};
  for(const id of ['profileName','profileModel','profileApiKey','profileReasoning','profileImportText','profileAdvanced'])document.getElementById(id).value='';
  document.getElementById('profileBaseUrl').value='https://ttmapi.site/v1';
  document.getElementById('profileUsageUrl').value='https://ttmapi.site/v1/usage';
  document.getElementById('profileCockpitHeaders').checked=true;
  document.getElementById('profileApiKey').placeholder='sk-…';
  document.getElementById('profileFormTitle').textContent='Add API account';
  document.getElementById('profileFormNotice').textContent='Blank optional fields keep the current Codex setting.';
  document.getElementById('saveProfileButton').textContent='Create API account';
  document.getElementById('cancelProfileEditButton').style.display='none';
}
async function openProfileEditor(id){
  clearBanner();
  try{
    const r=await fetch('/api/profiles/detail?profile_id='+encodeURIComponent(id),{cache:'no-store'});const d=await r.json();
    if(!r.ok||!d.ok)throw new Error(d.error||'Could not load API account');
    profileCockpitChanged=false;
    const p=d.profile;editingProfileId=id;profileHeaders={...(p.http_headers||{})};
    profileHiddenFields={approval_policy:p.approval_policy||'',sandbox_mode:p.sandbox_mode||'',model_catalog_json:p.model_catalog_json||''};
    document.getElementById('profileAdvanced').value=JSON.stringify({...profileHiddenFields,http_headers:profileHeaders},null,2);
    for(const [field,value] of Object.entries({profileName:p.name,profileBaseUrl:p.base_url,profileUsageUrl:p.usage_url,profileModel:p.model,profileReasoning:p.reasoning_effort}))document.getElementById(field).value=value||'';
    document.getElementById('profileApiKey').value='';
    document.getElementById('profileApiKey').placeholder='Leave blank to keep existing key';
    document.getElementById('profileCockpitHeaders').checked=Object.keys(p.http_headers||{}).some(k=>k.toLowerCase()==='x-cockpit-instance-id');
    document.getElementById('profileFormTitle').textContent='Edit API account';
    document.getElementById('profileFormNotice').textContent='Leave API key blank to keep the existing key. Blank optional fields inherit Codex settings.';
    document.getElementById('saveProfileButton').textContent='Save changes';
    document.getElementById('cancelProfileEditButton').style.display='block';
    openAccountDialog('add-api',id,true);
    document.getElementById('profileName').focus();
  }catch(e){show(e.message,false)}
}
async function saveProfile(button){
  let body;try{body=profileFormBody()}catch(e){show(e.message,false);return}
  if(editingProfileId)body.profile_id=editingProfileId;
  const savedId=editingProfileId;saveDialogDraft();
  clearBanner();const old=button.textContent;button.disabled=true;button.textContent=editingProfileId?'Saving…':'Creating…';
  try{
    const d=await post(editingProfileId?'/api/profiles/update':'/api/profiles',body);
    removeDialogDraft('add-api',savedId);cancelProfileEdit();closeAccountDialog(false);show(d.message);await loadProfiles();await loadAccounts(false)
  }catch(e){show(e.message,false)}finally{button.disabled=false;button.textContent=editingProfileId?old:'Create API account'}
}

async function parseProfileText(button){
  const text=document.getElementById('profileImportText').value;
  clearBanner();const old=button.textContent;button.disabled=true;button.textContent='Parsing…';
  try{
    profileCockpitChanged=false;
    const d=await post('/api/profiles/parse',{text});const f=d.fields||{};document.getElementById('profileAdvanced').value='';
    document.getElementById('profileName').value=f.name||'';
    document.getElementById('profileBaseUrl').value=f.base_url||'';
    document.getElementById('profileUsageUrl').value=f.base_url==='https://ttmapi.site/v1'?'https://ttmapi.site/v1/usage':'';
    document.getElementById('profileModel').value=f.model||'';
    document.getElementById('profileApiKey').value=f.api_key||'';
    document.getElementById('profileReasoning').value=f.reasoning_effort||'';
    profileHiddenFields={approval_policy:f.approval_policy||'',sandbox_mode:f.sandbox_mode||'',model_catalog_json:f.model_catalog_json||''};
    profileHeaders={...(f.http_headers||{})};
    document.getElementById('profileCockpitHeaders').checked=Object.keys(f.http_headers||{}).some(k=>k.toLowerCase()==='x-cockpit-instance-id');
    saveDialogDraft();show(f.warning||'Fields filled from pasted config.');
  }catch(e){show(e.message,false)}finally{button.disabled=false;button.textContent=old}
}

async function applyProfile(id,button){
  const restartNote=restartAfterSwitch?'Saved restart commands will run for Codex app server.':'Services will not restart because Restart after account switch is disabled.';
  if(!confirm(`Select this API account?\n\n${restartNote}`))return;
  clearBanner();clearServiceOutput();button.disabled=true;
  try{const d=await post('/api/profiles/apply',{profile_id:id});show(d.message);showServiceOutput(d.output);await loadProfiles();await loadAccounts(false)}catch(e){show(e.message,false);showServiceOutput(e.output)}finally{button.disabled=false}
}

async function deleteProfile(id,button){
  if(!confirm('Delete this saved profile? The active Codex files will not be changed.'))return;
  clearBanner();button.disabled=true;
  try{const d=await post('/api/profiles/delete',{profile_id:id});show(d.message);await loadProfiles();await loadAccounts(false)}catch(e){show(e.message,false)}finally{button.disabled=false}
}

async function post(url,body={}){
  const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(!r.ok||!d.ok){const e=new Error(d.error||'Action failed');e.output=d.output||'';throw e}
  return d
}

async function setRestartAfterSwitch(enabled,input){
  if(input)input.disabled=true;
  clearBanner();
  try{
    const d=await post('/api/config/restart-after-switch',{enabled});
    show(d.message||'Updated');
    await loadStatus()
  }catch(e){if(input)input.checked=!enabled;show(e.message,false)}finally{if(input)input.disabled=false}
}

async function setAutoPing(enabled,input){
  if(input)input.disabled=true;
  clearBanner();
  try{
    const d=await post('/api/config/auto-ping',{enabled});
    show(d.message||'Auto Ping updated.');
    await loadStatus();await loadAccounts(false)
  }catch(e){if(input)input.checked=!enabled;show(e.message,false)}finally{if(input)input.disabled=false}
}

async function setAccountAutoPing(q,input){
  const enabled=input.checked;input.disabled=true;clearBanner();
  try{
    const d=await post('/api/config/account-auto-ping',{query:q,enabled});
    show(d.message||'Account Auto Ping updated.');
    await loadAccounts(false)
  }catch(e){input.checked=!enabled;show(e.message,false)}
  finally{input.disabled=false}
}

async function switchTo(q){
  const restartNote=restartAfterSwitch?'Saved Codex app restart commands will run automatically.':'Automatic restart is disabled.';
  if(!confirm(`Switch to account "${q}"?\n\nOnly unchanged settings added by the active API profile will be removed.\n${restartNote}`))return;
  clearBanner();clearServiceOutput();
  try{
    const d=await post('/api/switch',{query:q});
    show(d.message||'Account switched and codex-app-server restarted.');
    showServiceOutput(d.output);
    await loadProfiles();
    await loadAccounts(false)
  }catch(e){show(e.message,false);showServiceOutput(e.output)}
}

async function useCodexAuth(q,authAlreadyActive,button){
  if(!authAlreadyActive){await switchTo(q);return}
  const restartNote=restartAfterSwitch?'Saved restart commands will run for Codex app server.':'Services will not restart because Restart after account switch is disabled.';
  if(!confirm(`Use the currently authenticated ChatGPT account and remove unchanged API-profile settings?\n\n${restartNote}`))return;
  clearBanner();clearServiceOutput();button.disabled=true;
  try{const d=await post('/api/profiles/apply',{profile_id:'default'});show(d.message);showServiceOutput(d.output);await loadProfiles();await loadAccounts(false)}catch(e){show(e.message,false);showServiceOutput(e.output)}finally{button.disabled=false}
}

async function restartServices(button){
  if(!confirm('Run the saved restart commands for Codex app server now?'))return;
  clearBanner();clearServiceOutput();
  const oldText=button.textContent;
  button.disabled=true;button.textContent='Restarting…';
  try{
    const d=await post('/api/restart-services');
    show(d.message||'Services restarted.');
    showServiceOutput(d.output);
  }catch(e){show(e.message,false);showServiceOutput(e.output)}
  finally{button.disabled=false;button.textContent=oldText}
}

async function pingAccount(q,button){
  clearBanner();
  const oldText=button.textContent;
  button.disabled=true;button.textContent='Pinging…';
  try{
    const d=await post('/api/ping-account',{query:q});
    show(d.message||'Ping passed.');
    await loadAccounts(false)
  }catch(e){show(e.message,false)}
  finally{button.disabled=false;button.textContent=oldText}
}

async function importCodexAuth(button){
  const text=document.getElementById('authImportText').value;
  const format=document.getElementById('authImportFormat').value;
  const alias=document.getElementById('authImportAlias').value.trim();
  if(!text.trim()){show('Paste authentication JSON first.',false);return}
  if(!confirm('Import these accounts into local storage?'))return;
  clearBanner();authImportOutputBox.classList.remove('visible');authImportOutput.textContent='';
  const oldText=button.textContent;button.disabled=true;button.textContent='Importing…';
  try{
    const d=await post('/api/import-accounts',{text,format,alias});
    show(d.message||'Import completed.');
    authImportOutput.textContent=d.output||'(no command output)';authImportOutputBox.classList.add('visible');authImportOutputBox.open=false;
    document.getElementById('authImportText').value='';document.getElementById('authImportAlias').value='';document.getElementById('authImportFormat').value='auto';removeDialogDraft('import');closeAccountDialog(false);
    await loadAccounts(false)
  }catch(e){
    show(e.message,false);
    if(e.output){authImportOutput.textContent=e.output;authImportOutputBox.classList.add('visible');authImportOutputBox.open=true}
  }finally{button.disabled=false;button.textContent=oldText}
}

function openAliasEditor(selector,previousKey,currentAlias){
  aliasEditor={selector:String(selector),previousKey:String(previousKey),currentAlias:String(currentAlias||'')};
  aliasInput.value=aliasEditor.currentAlias;aliasError.textContent='';clearAliasButton.disabled=!aliasEditor.currentAlias;
  aliasReturnFocus=document.activeElement;document.querySelector('.app-shell').inert=true;
  aliasModal.classList.add('open');setTimeout(()=>{aliasInput.focus();aliasInput.select()},0)
}
function closeAliasEditor(){aliasModal.classList.remove('open');document.querySelector('.app-shell').inert=false;aliasError.textContent='';if(aliasReturnFocus?.isConnected)aliasReturnFocus.focus()}
async function updateAlias(alias){
  const value=alias===null?null:String(alias).trim();
  if(value!==null&&!value){aliasError.textContent='Alias cannot be empty.';return}
  if(value!==null&&/^\d+$/.test(value)){aliasError.textContent='Alias cannot contain only digits.';return}
  clearAliasButton.disabled=true;saveAliasButton.disabled=true;aliasInput.disabled=true;aliasError.textContent='';
  try{
    const d=await post('/api/account-alias',{selector:aliasEditor.selector,previous_key:aliasEditor.previousKey,alias:value});
    closeAliasEditor();show(d.message||'Account alias updated.');await loadAccounts(false)
  }catch(e){aliasError.textContent=e.message}
  finally{clearAliasButton.disabled=!aliasEditor.currentAlias;saveAliasButton.disabled=false;aliasInput.disabled=false}
}
function saveAlias(){return updateAlias(aliasInput.value)}
function clearAlias(){if(aliasEditor.currentAlias&&confirm(`Clear alias "${aliasEditor.currentAlias}"?`))return updateAlias(null)}

refreshWorkspace(false);
if(loginSessionId){document.getElementById('loginStart').disabled=true;pollLogin()}
showView(location.hash.slice(1)||'dashboard');
try{
  const saved=JSON.parse(sessionStorage.getItem('codex-account-dialog')||'null');
  if(saved&&Object.hasOwn(draftFields,saved.kind)){
    if(saved.kind==='add-api'&&saved.id)openProfileEditor(saved.id);else openAccountDialog(saved.kind);
  }
}catch(_){}
