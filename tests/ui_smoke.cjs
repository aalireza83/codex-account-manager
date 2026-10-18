// Run with npm run test:ui. Every operation uses a disposable synthetic workspace.
const {chromium}=require('playwright');
const {spawn,spawnSync}=require('node:child_process');
const fs=require('node:fs');
const path=require('node:path');
const net=require('node:net');
const os=require('node:os');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const python=process.env.PYTHON||'python3';
const artifacts=fs.mkdtempSync(path.join(os.tmpdir(),'codex-ui-results-'));
async function navigate(page,view){
 if(await page.locator('#exportDialog').evaluate(e=>e.classList.contains('open')))await page.keyboard.press('Escape');
 if(await page.locator('#accountDialog').evaluate(e=>e.classList.contains('open')))await page.keyboard.press('Escape');
 await page.waitForFunction(()=>!document.getElementById('accountDialog').classList.contains('open'));
 await page.waitForFunction(()=>matchMedia('(min-width: 1280px)').matches?!document.getElementById('sidebar').inert:(document.getElementById('sidebar').inert||document.getElementById('sidebar').classList.contains('open')));
 if(await page.locator('#sidebar').evaluate(e=>e.inert))await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.locator(`.tabs button[data-view="${view}"]`).click();
}
async function openNativeForm(page,kind='login'){
 await navigate(page,'accounts');await page.getByRole('button',{name:'Add account',exact:true}).click();
 if(kind==='import')await page.locator('#dialogImportTab').click();
}
async function openApiForm(page){await navigate(page,'api-accounts');await page.getByRole('button',{name:'Add API account',exact:true}).click()}
async function refreshFromDashboard(page,refresh=false){
 const exportOpen=await page.locator('#exportDialog').evaluate(e=>e.classList.contains('open'));
 const view=await page.locator('section.view.active').getAttribute('id');
 await navigate(page,'dashboard');
 if(refresh)await page.locator('#refreshWorkspace').click();else await page.evaluate(()=>refreshWorkspace(false));
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);await navigate(page,view.slice(5));
 if(exportOpen)await page.locator(view==='view-api-accounts'?'#toggleApiExport':'#toggleNativeExport').click();
}
async function restartFromDashboard(page){
 const view=await page.locator('section.view.active').getAttribute('id');await navigate(page,'dashboard');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/restart-services'));
 await page.getByRole('button',{name:'Restart services',exact:true}).click();await response;await navigate(page,view.slice(5));
}
async function loginFlow(browser,base){
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:"Asia/Tehran"});
 try{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await navigate(page,'accounts');
 await page.locator('.card .name').getByText('Personal',{exact:true}).waitFor();
 await page.getByText('Last error: Synthetic prior Ping failed',{exact:true}).waitFor();
 assert(!/Self-hosted|YOUR CODEX WORKSPACE|Manage your accounts, usage limits and active configuration\./.test(await page.locator('body').innerText()));
 if(await page.getByText('Available resets',{exact:true}).count()!==2)throw new Error('Reset credits missing');
 await openNativeForm(page);
 await page.locator('#loginAlias').fill('New account');
 await page.locator('#loginMethod').selectOption('manual');
 await page.locator('#loginStart').click();
 await page.locator('#loginLink').waitFor({state:'visible'});
 const url=new URL(await page.locator('#loginLink').getAttribute('href'));
 await page.locator('#loginCallback').fill('http://localhost:1455/auth/callback?code=synthetic&state='+url.searchParams.get('state'));
 await page.getByRole('button',{name:'Complete login',exact:true}).click();
 await page.getByText('Account saved. Select it from Accounts when you want to switch.',{exact:true}).first().waitFor();
 await navigate(page,'accounts');
 await page.locator('#grid .name').getByText('New account',{exact:true}).waitFor();
 const current=await page.locator('.card.active .name').innerText();
 if(current!=='Personal')throw new Error('Login changed active account');
 page.on('dialog',d=>d.accept());
 const card=page.locator('.card').filter({has:page.getByText('New account',{exact:true})});
 await card.getByRole('button',{name:'Switch account',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.card.active .name')?.textContent==='New account');
 await openNativeForm(page);
 await page.locator('#loginMethod').selectOption('device');
 await page.locator('#loginAlias').fill('Device');
 await page.locator('#loginStart').click();
 await page.getByText('Code: ABCD-EFGH',{exact:true}).waitFor();
 if(await page.locator('#loginCallbackForm').isVisible())throw new Error('Device flow showed browser callback form');
 await page.reload();
 await page.getByText('Code: ABCD-EFGH',{exact:true}).waitFor();
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:path.join(artifacts,'caw-login-mobile.png'),fullPage:true,animations:"disabled"});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Mobile horizontal overflow');
 await page.getByRole('button',{name:'Cancel login',exact:true}).click();
 await page.getByText('Login cancelled.',{exact:true}).waitFor();
 await page.setViewportSize({width:1280,height:1000});
 await navigate(page,'accounts');
 await page.screenshot({path:path.join(artifacts,'caw-accounts-desktop.png'),fullPage:true,animations:"disabled"});
 if(errors.length)throw new Error(errors.join('\n'));
 console.log('PASS: manual login, account unchanged on login, native switching, device view and cancellation, mobile layout, no JS errors');
 }finally{await page.close()}
}
async function layoutFlow(browser,base){
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:"Asia/Tehran"});
 try{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);await navigate(page,'accounts');await page.locator('.card').first().waitFor();
 await page.screenshot({path:path.join(artifacts,'caw-tailadmin-light.png'),fullPage:true,animations:"disabled"});
 await page.getByRole('button',{name:'Switch to dark mode',exact:true}).click();
 await page.reload();
 if(!await page.locator('html').evaluate(e=>e.classList.contains('dark')))throw Error('Theme not persisted');
 await page.screenshot({path:path.join(artifacts,'caw-tailadmin-dark.png'),fullPage:true,animations:"disabled"});
 await page.locator('.card').first().getByRole('button',{name:'Edit alias',exact:true}).click();
 await page.locator('#aliasInput').fill('Personal updated');
 await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
 if(await page.evaluate(()=>document.activeElement.id)!=='aliasInput')throw Error('Alias keyboard focus wrong');
 await page.keyboard.press('Escape');
 if(await page.locator('#aliasModal').evaluate(e=>e.classList.contains('open')))throw Error('Escape failed');
 const views=['dashboard','accounts','api-accounts','diagnostics','activity','settings'];
 for(const width of [375,768,1024,1280,1536]){
  await page.setViewportSize({width,height:900});
  for(const view of views){
   if(width<1280)await page.getByRole('button',{name:'Open navigation',exact:true}).click();
   await page.locator(`button[data-view="${view}"]`).click();
   if(width<1280 && await page.locator('#sidebarToggle').getAttribute('aria-expanded')!=='false')throw Error('Drawer failed to close');
   if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error(`Overflow ${view}@${width}`);
   if(!await page.locator(`#view-${view}`).isVisible())throw Error(`View missing ${view}`);
   for(const name of ['Restart services'])assert.equal(await page.getByRole('button',{name,exact:true}).isVisible(),view==='dashboard');
   assert.equal(await page.getByRole('button',{name:'Cached data',exact:true}).count(),0);
   assert.equal(await page.locator('section.view.active').getByRole('button',{name:'Refresh usage',exact:true}).count(),['dashboard','accounts','api-accounts'].includes(view)?1:0);
   const titleBounds=await page.locator('#pageTitle').boundingBox();const headerBounds=await page.locator('.topbar').boundingBox();
   assert(titleBounds.y>=headerBounds.y+headerBounds.height,'Sticky header hides page title');
   await page.screenshot({path:path.join(artifacts,`caw-tailadmin-${view}-${width}.png`),fullPage:true,animations:"disabled"});
  }
 }
 await page.setViewportSize({width:390,height:844});
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.screenshot({path:path.join(artifacts,'caw-tailadmin-mobile-menu.png'),animations:'disabled'});
 await page.keyboard.press('Escape');
 if(await page.evaluate(()=>document.activeElement.id)!=='sidebarToggle')throw Error('Drawer focus not restored');
 await page.getByRole('button',{name:'Open navigation',exact:true}).click();
 await page.setViewportSize({width:1440,height:1000});
 await page.waitForFunction(()=>!document.querySelector('.workspace').inert);
 await page.getByRole('button',{name:'Switch to light mode',exact:true}).click();
 if(errors.length)throw Error(errors.join('\n'));
 console.log('PASS: all six pages at 375/768/1024/1280/1536, theme persistence, keyboard modal and drawer, no overflow or JavaScript errors');
 }finally{await page.close()}
}
async function draftFlow(browser,base){
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 try{
  const errors=[];page.on('pageerror',error=>errors.push(error.message));await page.goto(base);await navigate(page,'accounts');
  await openNativeForm(page);await page.locator('#loginAlias').fill('Login draft');await page.locator('#loginMethod').selectOption('manual');
  assert(await page.locator('.app-shell').evaluate(e=>e.inert));
  assert.notEqual(await page.locator('.app-shell').evaluate(e=>getComputedStyle(e).filter),'none');
  await page.keyboard.press('Escape');await openNativeForm(page);
  assert.equal(await page.locator('#loginAlias').inputValue(),'Login draft');
  await page.reload();await page.locator('#loginAlias').waitFor();assert.equal(await page.locator('#loginAlias').inputValue(),'Login draft');
  await page.getByRole('button',{name:'Discard draft',exact:true}).click();assert.equal(await page.locator('#loginAlias').inputValue(),'');
  await page.getByRole('button',{name:'Close account dialog',exact:true}).click();
  await openNativeForm(page,'import');await page.locator('#authImportText').fill('{unfinished-auth');await page.locator('#authImportAlias').fill('Import draft');
  await page.locator('#accountDialog').click({position:{x:5,y:5}});
  await openNativeForm(page,'import');assert.equal(await page.locator('#authImportText').inputValue(),'{unfinished-auth');
  await page.getByRole('button',{name:'Discard draft',exact:true}).click();await page.getByRole('button',{name:'Close account dialog',exact:true}).click();
  for(const width of [375,1440]){
   await page.setViewportSize({width,height:900});await openApiForm(page);
   await page.locator('#profileName').fill('API draft');await page.locator('#profileApiKey').fill('synthetic-draft-key');await page.locator('#profileModel').fill('draft-model');
   await page.locator('#profileAdvanced').locator('..').locator('summary').click();
   await page.locator('#profileAdvanced').fill('{"http_headers":{"x-draft":"saved"},"sandbox_mode":"read-only"}');
   await page.keyboard.press('Escape');await openApiForm(page);
   assert.equal(await page.locator('#profileApiKey').inputValue(),'synthetic-draft-key');
   assert.equal(await page.locator('#profileModel').inputValue(),'draft-model');
   await page.reload();await page.locator('#profileApiKey').waitFor();
   assert.equal(await page.locator('#profileName').inputValue(),'API draft');assert.equal(await page.locator('#profileApiKey').inputValue(),'synthetic-draft-key');
   assert((await page.locator('#profileAdvanced').inputValue()).includes('x-draft'));
   assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth));
   await page.screenshot({path:path.join(artifacts,`account-dialog-${width}.png`),fullPage:true,animations:'disabled'});
   await page.getByRole('button',{name:'Discard draft',exact:true}).click();assert.equal(await page.locator('#profileApiKey').inputValue(),'');
   await page.getByRole('button',{name:'Close account dialog',exact:true}).click();
  }
  assert.equal(errors.length,0,errors.join('\n'));console.log('PASS: separate account pages, blurred modal, Escape/backdrop close, login/import/API draft cache, reload recovery, explicit discard, mobile forms');
 }finally{await page.close()}
}
async function parityFlow(browser,base){
 const page=await browser.newPage({viewport:{width:1440,height:1000},timezoneId:"Asia/Tehran"});
 try{
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 page.on('dialog',d=>d.accept());
 await page.goto(base);await navigate(page,'accounts');
 await page.locator('.card .name').getByText('Personal',{exact:true}).waitFor();
 await page.getByText('Last error: Synthetic prior Ping failed',{exact:true}).waitFor();
 assert(!/Self-hosted|YOUR CODEX WORKSPACE|Manage your accounts, usage limits and active configuration\./.test(await page.locator('body').innerText()));
 assert.equal(await page.getByRole('button',{name:'Previous account',exact:true}).count(),0);
 const selection=page.waitForResponse(r=>r.url().endsWith('/api/config/account-auto-ping'));
 await page.locator('.card').filter({has:page.getByText('Personal',{exact:true})}).locator('label[title="Auto Ping"] input').click();await selection;
 const pingState=await (await page.request.get(base+'/api/config')).json();
 if(pingState.auto_ping_enabled||Object.keys(pingState.auto_ping.accounts).length!==1)throw new Error('Cannot configure Auto Ping while globally disabled');
 const data=await (await page.request.get(base+'/api/profiles')).json();
 const home=path.dirname(data.config_file),dataRoot=path.dirname(data.provider_env_file);
 const env={...process.env,CODEX_HOME:home,CODEX_ACCOUNT_MANAGER_CONFIG:path.join(path.dirname(dataRoot),'config.json'),CODEX_ACCOUNT_MANAGER_DATA_DIR:dataRoot};
 assert(dataRoot!==path.join(home,'accounts'));
 assert(!fs.existsSync(path.join(home,'accounts')),'Legacy account storage was created');
 assert.equal(await page.getByRole('button',{name:'Cached data',exact:true}).count(),0);
 function cli(...args){const cp=spawnSync(python,[path.join(root,'app.py'),...args,'--json'],{env,encoding:'utf8'});if(cp.status!==0)throw new Error(cp.stdout+cp.stderr);return JSON.parse(cp.stdout)}
 await openApiForm(page);
 await page.locator('#profileImportText').fill('model_provider = "preview"\nmodel = "parsed-model"\n[model_providers.preview]\nname = "Parsed provider"\nbase_url = "https://ttmapi.site/v1"\nexperimental_bearer_token = "synthetic-key"');
 await page.getByRole('button',{name:'Parse text and fill fields',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('profileModel').value==='parsed-model');
 await page.locator('#profileName').fill('Browser API');
 await page.locator('#profileApiKey').fill('browser-private-key');
 await page.getByText('Advanced account fields',{exact:true}).click();
 await page.locator('#profileAdvanced').fill('{broken');
 await page.getByRole('button',{name:'Create API account',exact:true}).click();
 assert(await page.locator('#banner').evaluate(e=>e.classList.contains('err')));
 assert.equal(cli('profiles','list').profiles.filter(p=>p.kind==='api').length,0);
 await page.locator('#profileAdvanced').fill(JSON.stringify({http_headers:{'x-custom':'yes'},sandbox_mode:'read-only',approval_policy:'never'}));
 await page.getByRole('button',{name:'Create API account',exact:true}).click();
 await navigate(page,'api-accounts');
 await page.locator('.api-card .name').getByText('Browser API',{exact:true}).waitFor();
 const profile=cli('profiles','list').profiles.find(p=>p.name==='Browser API');
 if(!profile)throw new Error('GUI create not visible in CLI');
 await page.locator('#toggleApiExport').click();
 await page.locator('.api-export-account').check();
 const apiDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download API export',exact:true}).click();
 const apiPath=path.join(artifacts,'api-export.json');await (await apiDownload).saveAs(apiPath);
 const apiDocs=JSON.parse(fs.readFileSync(apiPath,'utf8'));assert.equal(apiDocs.length,1);assert.equal(apiDocs[0].api_key,'browser-private-key');
 const cliApiPath=path.join(artifacts,'cli-api-export.json');cli('profiles','export',profile.id,'--output',cliApiPath);
 assert.deepEqual(JSON.parse(fs.readFileSync(cliApiPath,'utf8')),apiDocs);
 await page.locator('.api-export-account').uncheck();assert(await page.locator('#apiExportButton').isDisabled());
 await page.locator('#apiExportScope').selectOption('all');assert(await page.locator('#apiExportButton').isEnabled());
 await page.keyboard.press('Escape');
 const apiUsageRequest=page.waitForResponse(response=>response.url().includes('/api/profiles/usage?'));
 let nativeLiveRequests=0;const countNative=request=>{if(request.url().endsWith('/api/accounts?refresh=1'))nativeLiveRequests++};page.on('request',countNative);
 await page.locator('.api-card').getByRole('button',{name:'Usage',exact:true}).click();await apiUsageRequest;
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);assert.equal(nativeLiveRequests,0);page.off('request',countNative);
 const detail=cli('profiles','detail',profile.id).profile;
 assert.equal(detail.model,'parsed-model');
 if(detail.http_headers['x-custom']!=='yes'||detail.sandbox_mode!=='read-only')throw new Error('Advanced fields lost');
 const headerFile=path.join(artifacts,'headers.json');
 fs.writeFileSync(headerFile,JSON.stringify({'X-Cockpit-Instance-ID':'custom-instance','x-openai-actor-authorization':'custom-actor','x-custom':'yes'}));
 cli('profiles','update',profile.id,'--model','updated-from-cli','--headers-file',headerFile);
 cli('profiles','apply',profile.id);
 await refreshFromDashboard(page);
 await page.waitForFunction(()=>document.getElementById('statActive').textContent==='Browser API');
 cli('switch','New account');
 await refreshFromDashboard(page);
 await page.waitForFunction(()=>document.querySelector('.card.active .name')?.textContent==='New account');
 await navigate(page,'api-accounts');
 const api=page.locator('.api-card').filter({has:page.getByText('Browser API',{exact:true})});
 await api.getByText('updated-from-cli',{exact:false}).waitFor();
 await api.getByRole('button',{name:'Edit',exact:true}).click();
 await page.locator('#profileName').fill('Browser API edited');
 await page.keyboard.press('Escape');await api.getByRole('button',{name:'Edit',exact:true}).click();
 await page.locator('#profileName').waitFor();
 assert.equal(await page.locator('#profileName').inputValue(),'Browser API edited');
 await page.reload();await page.locator('#profileName').waitFor();assert.equal(await page.locator('#profileName').inputValue(),'Browser API edited');
 await page.getByRole('button',{name:'Save changes',exact:true}).click();
 if(cli('profiles','detail',profile.id).profile.name!=='Browser API edited')throw new Error('GUI update not visible to CLI');
 const preserved=cli('profiles','detail',profile.id).profile;
 assert.equal(preserved.http_headers['X-Cockpit-Instance-ID'],'custom-instance');
 assert.equal(preserved.http_headers['x-openai-actor-authorization'],'custom-actor');
 assert.equal(fs.readFileSync(path.join(dataRoot,'profiles',profile.id,'token'),'utf8').trim(),'browser-private-key');
 await navigate(page,'api-accounts');
 const edited=page.locator('.api-card').filter({has:page.getByText('Browser API edited',{exact:true})});
 await edited.getByRole('button',{name:'Usage',exact:true}).click();
 await edited.getByText('Quota remaining',{exact:true}).waitFor();
 assert((await edited.locator('.api-usage').innerText()).includes('42'));
 await edited.getByRole('button',{name:'Select API account',exact:true}).click();
 await page.waitForFunction(()=>document.getElementById('statActive').textContent==='Browser API edited');
 await navigate(page,'accounts');
 await page.locator('.card').filter({has:page.locator('.name').getByText('New account',{exact:true})}).getByRole('button',{name:'Switch account',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.card.active .name')?.textContent==='New account');
 await navigate(page,'api-accounts');
 await edited.getByRole('button',{name:'Delete',exact:true}).click();
 await edited.waitFor({state:'detached'});
 assert(!cli('profiles','list').profiles.some(p=>p.id===profile.id),'Deleted provider still in CLI');
 const personal=page.locator('.card').filter({has:page.getByText('Personal',{exact:true})});
 await navigate(page,'accounts');
 assert.equal(await page.locator('.card .export-account').count(),0);
 await page.locator('#toggleNativeExport').click();
 const exportPersonal=page.locator('.export-row').filter({has:page.getByText('Personal',{exact:true})});
 await exportPersonal.locator('.export-account').check();
 await refreshFromDashboard(page);
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
 assert(await exportPersonal.locator('.export-account').isChecked(),'Cached refresh cleared selected export');
 const liveRefresh=page.waitForResponse(r=>r.url().endsWith('/api/accounts?refresh=1'));
 await refreshFromDashboard(page,true);await liveRefresh;
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
 assert(await exportPersonal.locator('.export-account').isChecked(),'Live refresh cleared selected export');
 const downloadPromise=page.waitForEvent('download');
 await page.getByRole('button',{name:'Download export',exact:true}).click();
 const download=await downloadPromise;await download.saveAs(path.join(artifacts,'caw-selected-export.json'));
 const exported=JSON.parse(fs.readFileSync(path.join(artifacts,'caw-selected-export.json'),'utf8'));
 if(exported.length!==1)throw new Error('GUI selected export did not match selection');
 await page.locator('#exportFormat').selectOption('cpa');
 const cpaDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download export',exact:true}).click();
 const cpaPath=path.join(artifacts,'selected-cpa.json');await (await cpaDownload).saveAs(cpaPath);
 const cpa=JSON.parse(fs.readFileSync(cpaPath,'utf8'));assert.equal(cpa.length,1);assert(cpa[0].access_token);
 await exportPersonal.locator('.export-account').uncheck();
 assert(await page.locator('#exportButton').isDisabled(),'Empty selection must not silently export all accounts');
 await page.locator('#exportScope').selectOption('all');
 assert(await page.locator('#exportButton').isEnabled());
 const allDownload=page.waitForEvent('download');await page.getByRole('button',{name:'Download export',exact:true}).click();
 const allPath=path.join(artifacts,'all-cpa.json');await (await allDownload).saveAs(allPath);
 assert.equal(JSON.parse(fs.readFileSync(allPath,'utf8')).length,cli('list').accounts.length);
 await page.locator('#exportFormat').selectOption('standard');
 await navigate(page,'accounts');
 await personal.getByRole('button',{name:'Ping',exact:true}).click();
 await page.getByText('Synthetic Ping completed.',{exact:true}).waitFor();
 const activity=cli('activity');assert(activity.accounts.find(a=>a.name==='Personal').ping.success>=1);
 await navigate(page,'activity');await page.waitForFunction(()=>Number(document.getElementById('statPings').textContent)>=1);
 const dashboardSwitches=activity.accounts.reduce((sum,a)=>sum+(a.switch?.success||0),0);
 await page.waitForFunction(total=>Number(document.getElementById('statSwitches').textContent)===total,dashboardSwitches);
 await navigate(page,'accounts');
 const payload={sub:'imported-user',email:'imported@example.test','https://api.openai.com/auth':{chatgpt_user_id:'imported-user',chatgpt_account_id:'imported-account',chatgpt_plan_type:'plus'}};
 const imported={...exported[0],tokens:{...exported[0].tokens,account_id:'imported-account',id_token:'header.'+Buffer.from(JSON.stringify(payload)).toString('base64url')+'.signature'}};
 await openNativeForm(page,'import');
 await page.locator('#authImportAlias').fill('Imported account');
 await page.locator('#authImportText').fill('{broken');
 await page.locator('#authImportButton').click();
 await page.waitForFunction(()=>document.getElementById('banner').textContent.includes('Invalid JSON'));
 assert.equal(await page.locator('#authImportText').inputValue(),'{broken');
 await page.locator('#authImportText').fill(JSON.stringify(imported));
 await page.locator('#authImportButton').click();
 await page.waitForFunction(()=>document.getElementById('authImportText').value==='');
 await navigate(page,'accounts');
 const importedCard=page.locator('.card').filter({has:page.locator('.name').getByText('Imported account',{exact:true})});
 await importedCard.waitFor();
 await importedCard.getByRole('button',{name:'Edit alias',exact:true}).click();
 await page.locator('#aliasInput').fill('Imported renamed');
 await page.getByRole('button',{name:'Save alias',exact:true}).click();
 const renamed=page.locator('.card').filter({has:page.locator('.name').getByText('Imported renamed',{exact:true})});
 await renamed.waitFor();
 assert(cli('list').accounts.some(a=>a.alias==='Imported renamed'));
 await renamed.getByRole('button',{name:'Edit alias',exact:true}).click();
 await page.getByRole('button',{name:'Clear alias',exact:true}).click();
 const cleared=page.locator('.card').filter({has:page.locator('.name').getByText('imported@example.test',{exact:true})});
 await cleared.waitFor();assert(!cli('list').accounts.some(a=>a.alias==='Imported renamed'));
 await cleared.getByRole('button',{name:'Remove',exact:true}).click();await cleared.waitFor({state:'detached'});
 await openNativeForm(page,'import');
 await page.locator('#authImportFormat').selectOption('cpa');
 await page.locator('#authImportAlias').fill('CPA account');
 await page.locator('#authImportText').fill(JSON.stringify({type:'codex',...imported.tokens}));
 await page.locator('#authImportButton').click();
 await page.waitForFunction(()=>document.getElementById('authImportText').value==='');
 await navigate(page,'accounts');
 const cpaCard=page.locator('.card').filter({has:page.locator('.name').getByText('CPA account',{exact:true})});
 await cpaCard.waitFor();assert(cli('list').accounts.some(a=>a.alias==='CPA account'));
 await cpaCard.getByRole('button',{name:'Remove',exact:true}).click();await cpaCard.waitFor({state:'detached'});
 await page.route('**/api/accounts?refresh=1',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({ok:false,error:'Synthetic usage unavailable'})}));
 await refreshFromDashboard(page,true);
 await page.getByText('Synthetic usage unavailable',{exact:true}).waitFor();
 assert(await page.locator('.card.active .name').isVisible(),'Refresh failure removed saved account rows');
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
 await page.unroute('**/api/accounts?refresh=1');
 await refreshFromDashboard(page);
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
 assert.equal(await page.locator('#banner').innerText(),'');
 await page.getByRole('button',{name:'Diagnostics',exact:true}).click();
 await page.getByRole('button',{name:'Check health',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#doctorReport').textContent.includes('"healthy": true'));
 const credentialDirectory=path.join(dataRoot,'vault','credentials');
 const snapshot=fs.readdirSync(credentialDirectory).find(name=>name.endsWith('.json'));
 const snapshotPath=path.join(credentialDirectory,snapshot);fs.chmodSync(snapshotPath,0o644);
 await page.getByRole('button',{name:'Check health',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#doctorReport').textContent.includes('"healthy": false'));
 await page.getByRole('button',{name:'Fix private file permissions',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#doctorReport').textContent.includes('"healthy": true'));
 assert.equal(fs.statSync(snapshotPath).mode&0o777,0o600);
 await page.getByRole('button',{name:'Settings',exact:true}).click();
 for(const [id,key] of [['restartAfterSwitchToggle','restart_after_switch'],['autoPingToggle','auto_ping_enabled']]){
  await page.locator('#'+id).check();
  await page.waitForFunction(id=>!document.getElementById(id).disabled,id);
  assert.equal(cli('config','show')[key],true);
  await page.locator('#'+id).uncheck();
  await page.waitForFunction(id=>!document.getElementById(id).disabled,id);
  assert.equal(cli('config','show')[key],false);
 }
 cli('config','set','restart_after_switch','true');await refreshFromDashboard(page);
 await page.waitForFunction(()=>document.getElementById('restartAfterSwitchToggle').checked);
 cli('config','set','restart_after_switch','false');await refreshFromDashboard(page);
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
 assert(!await page.locator('#restartAfterSwitchToggle').isChecked());
 for(const service of ['app']){
  assert.equal(await page.locator('#restartService').inputValue(),service);
  const result=page.waitForResponse(r=>r.url().endsWith('/api/restart-services'));
  await page.getByRole('button',{name:'Restart Codex app server',exact:true}).click();
  const body=await (await result).json();assert(body.ok);assert(body.output.includes('Synthetic systemctl output'));
 }
 await restartFromDashboard(page);
 await page.waitForFunction(()=>document.getElementById('serviceOutput').textContent.includes('codex-app-server'));
 assert(fs.existsSync(path.join(dataRoot,'vault','accounts.json')));
 assert(!fs.existsSync(path.join(home,'accounts')),'CLI/WebUI wrote into legacy account storage');
 const runtime=cli('web','status');await page.getByText('WebUI server status',{exact:true}).click();
 const displayed=JSON.parse(await page.locator('#statusRaw').innerText());assert.equal(displayed.pid,runtime.pid);assert.equal(displayed.running,true);
 await page.getByRole('button',{name:'Check Auto Ping now',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('#autoPingReport').textContent.includes('"refreshed": false'));
 await page.setViewportSize({width:390,height:844});
 await page.screenshot({path:path.join(artifacts,'caw-settings-mobile.png'),fullPage:true,animations:"disabled"});
 if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw new Error('Settings mobile overflow');
 await page.locator('label[title="Enable WebUI"] input').click();
 await page.getByText('WebUI disabled and stopping. To return, run: python3 app.py web start',{exact:true}).waitFor();
 for(let i=0;i<30 && cli('web','status').running;i++)await new Promise(r=>setTimeout(r,100));
 if(cli('web','status').enabled||cli('web','status').running)throw new Error('GUI disable did not stop server');
 if(errors.length)throw new Error(errors.join('\n'));
 console.log('PASS: GUI/CLI API provider CRUD, selection, usage and TOML parsing; native import/alias/remove/Ping; advanced fields, selected credential export, diagnostics, Auto Ping cycle, mobile settings, actual WebUI shutdown');
 }finally{await page.close()}
}
async function freePort(){
 const listener=net.createServer();await new Promise(resolve=>listener.listen(0,'127.0.0.1',resolve));
 const port=listener.address().port;await new Promise(resolve=>listener.close(resolve));return port;
}
(async()=>{
 const port=await freePort();const base=`http://127.0.0.1:${port}`;
 const fixture=spawn(python,[path.join(__dirname,'ui_fixture.py')],{cwd:root,env:{...process.env,CODEX_UI_TEST_PORT:String(port)},stdio:['ignore','pipe','pipe']});
 let output='';fixture.stdout.on('data',chunk=>output+=chunk);fixture.stderr.on('data',chunk=>output+=chunk);
 let browser;
 try{
  let ready=false;
  for(let attempt=0;attempt<100;attempt++){
   if(fixture.exitCode!==null)throw Error('Fixture failed: '+output);
   try{const r=await fetch(base+'/api/health');if(r.ok){ready=true;break}}catch(_){}
   await new Promise(resolve=>setTimeout(resolve,100));
  }
  assert(ready,'Fixture failed to start: '+output);
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  await loginFlow(browser,base);
  if(!process.argv.includes('--storage-only')){
   await layoutFlow(browser,base);
   await draftFlow(browser,base);
  }
  await parityFlow(browser,base); // Verifies actual WebUI shutdown last.
  console.log('Browser screenshots and synthetic export: '+artifacts);
 }finally{
  if(browser)await browser.close();
  if(fixture.exitCode===null&&fixture.signalCode===null){const stopped=new Promise(resolve=>fixture.once('exit',resolve));fixture.kill('SIGTERM');await stopped}
 }
})().catch(error=>{console.error(error);process.exitCode=1});
