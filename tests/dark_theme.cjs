// Color-only regression against the public Blue preview; synthetic fixture only.
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),net=require('node:net'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const palette=JSON.parse(fs.readFileSync(path.join(root,'docs/blue-dark-tokens.json'),'utf8'));
const baselineRef=process.env.THEME_BASELINE_REF||'HEAD';
const oldCSS=execFileSync('git',['show',`${baselineRef}:web/assets/app.css`],{cwd:root,encoding:'utf8'});
const newCSS=fs.readFileSync(path.join(root,'web/assets/app.css'),'utf8');
const props=['color','backgroundColor','backgroundImage','borderTopColor','borderRightColor','borderBottomColor','borderLeftColor','outlineColor','boxShadow','opacity','fill','stroke','scrollbarColor','caretColor','accentColor'];
async function snapshot(page){
 return page.evaluate(props=>({
  paint:[document.body,...document.body.querySelectorAll('*')].map(e=>{const s=getComputedStyle(e);return [e.tagName,e.id,...props.map(p=>s[p])]}),
  geometry:[...document.querySelectorAll('.sidebar,.topbar,.panel,.card,.api-card,.dashboard-account,.stat-card,.modal-card,.form-grid,input,select,textarea,button')].filter(e=>e.getClientRects().length&&!e.closest('details:not([open])')).map(e=>{const r=e.getBoundingClientRect();return [e.tagName,e.id,r.x,r.y,r.width,r.height]})
 }),props);
}
async function token(page,selector,property,name){
 const result=await page.locator(selector).first().evaluate((e,{property,name})=>{
  const probe=document.createElement('span');probe.style.color=name.startsWith('--')?`var(${name})`:name;document.body.append(probe);
  const expected=getComputedStyle(probe).color;probe.remove();
  return {actual:getComputedStyle(e)[property],expected};
 },{property,name});
 assert.equal(result.actual,result.expected,selector+' '+property+' != '+name);
}
async function noOverflow(page){assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),'Horizontal overflow')}
async function navigate(page,view){
 await page.evaluate(()=>{if(document.getElementById('accountDialog').classList.contains('open'))closeAccountDialog();if(document.getElementById('exportDialog').classList.contains('open'))closeExportDialog();if(document.getElementById('aliasModal').classList.contains('open'))closeAliasEditor()});
 await page.evaluate(view=>showView(view),view);
 await page.waitForFunction(()=>!document.getElementById('refreshWorkspace').disabled);
}
(async()=>{
 const listener=net.createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
 const base=`http://127.0.0.1:${port}`,fixture=spawn(process.env.PYTHON||'python3',['tests/ui_fixture.py'],{cwd:root,env:{...process.env,CODEX_UI_TEST_PORT:String(port)},stdio:['ignore','pipe','pipe']});
 let output='',browser;fixture.stdout.on('data',c=>output+=c);fixture.stderr.on('data',c=>output+=c);
 try{
  for(let i=0;i<100;i++){if(fixture.exitCode!==null)throw Error(output);try{if((await fetch(base+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',timezoneId:'UTC'});
  // Make dates, rendered expiry labels, and layout snapshots deterministic.
  await context.addInitScript(()=>{
   const RealDate=Date;window.Date=class extends RealDate{constructor(...args){super(...(args.length?args:['2026-10-19T10:00:00Z']))}static now(){return new RealDate('2026-10-19T10:00:00Z').getTime()}};
  });
  const created=await context.request.post(base+'/api/profiles',{data:{name:'Theme API',api_key:'synthetic-theme-key',base_url:'https://ttmapi.site/v1'}});assert(created.ok());const profile=(await created.json()).profile;
  const errors=[];
  const scenarios=[...['dashboard','accounts','api-accounts','diagnostics','activity','settings'].map(view=>({name:view,view})),
   {name:'login',view:'accounts',kind:'login'}, {name:'import',view:'accounts',kind:'import'},
   {name:'api-add',view:'api-accounts',kind:'add-api'}, {name:'api-edit',view:'api-accounts',edit:true},
   {name:'native-export',view:'accounts',exportKind:'accounts'}, {name:'api-export',view:'api-accounts',exportKind:'api'},
   {name:'alias',view:'accounts',alias:true}, {name:'login-session',view:'accounts',kind:'login',session:true}];
  const artifacts=fs.mkdtempSync(path.join(os.tmpdir(),'codex-blue-theme-'));
  const captures={old:{},new:{}};
  for(const release of ['old','new']){
   const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/assets/app.css?*',route=>route.fulfill({contentType:'text/css',body:release==='old'?oldCSS:newCSS}));
   await page.goto(base,{waitUntil:'networkidle'});await page.evaluate(()=>document.documentElement.classList.remove('dark'));
   await page.waitForFunction(()=>document.querySelectorAll('#grid .card').length===2);
   for(const scenario of scenarios){
    await navigate(page,scenario.view);
    if(scenario.kind)await page.evaluate(kind=>openAccountDialog(kind),scenario.kind);
    if(scenario.edit)await page.evaluate(id=>openProfileEditor(id),profile.id);
    if(scenario.exportKind)await page.evaluate(kind=>toggleExport(kind),scenario.exportKind);
    if(scenario.alias)await page.locator('#grid .card').first().getByRole('button',{name:'Edit alias',exact:true}).click();
    if(scenario.session)await page.evaluate(()=>renderLogin({status:'pending',method:'manual',url:'https://example.test/synthetic-theme-login',expires_at:1792407600,message:'Waiting for synthetic sign-in'}));
    await page.evaluate(()=>{clearBanner();document.activeElement?.blur()});await page.mouse.move(0,0);
    captures[release][scenario.name]=await snapshot(page);
    if(release==='new'){
     assert.deepEqual(captures.new[scenario.name].paint,captures.old[scenario.name].paint,'Light colors changed: '+scenario.name);
     assert.deepEqual(captures.new[scenario.name].geometry,captures.old[scenario.name].geometry,'Light layout changed: '+scenario.name);
     await page.evaluate(()=>document.documentElement.classList.add('dark'));
     const dark=await snapshot(page);assert.deepEqual(dark.geometry,captures.old[scenario.name].geometry,'Dark layout changed: '+scenario.name);
     const values=await page.evaluate(colors=>Object.entries(colors).map(([name,value])=>{
      const probe=document.createElement('span');document.body.append(probe);
      probe.style.color=name.startsWith('--')?`var(${name})`:name;const actual=getComputedStyle(probe).color;
      probe.style.color=value;const expected=getComputedStyle(probe).color;probe.remove();return [name,actual,expected];
     }),palette.colors);
     for(const [name,actual,expected]of values)assert.equal(actual,expected,'Source token changed: '+name);
     await token(page,'body','backgroundColor','--background');await token(page,'.sidebar','backgroundColor','--sidebar');
     if(scenario.kind||scenario.edit||scenario.exportKind||scenario.alias)await token(page,'.modal.open .modal-card','backgroundColor','--popover');
     await noOverflow(page);
     if(['dashboard','settings','native-export','api-edit','login-session'].includes(scenario.name))
      await page.screenshot({path:path.join(artifacts,scenario.name+'-dark.png'),fullPage:false,animations:'disabled'});
     await page.evaluate(()=>document.documentElement.classList.remove('dark'));
    }
   }
   if(release==='new'){
    await navigate(page,'settings');await page.evaluate(()=>document.documentElement.classList.add('dark'));
    await token(page,'#saveRestartCommands','backgroundColor','--primary');await token(page,'#saveRestartCommands','color','--primary-foreground');
    await page.locator('#restartAppCommands').focus();await token(page,'#restartAppCommands','borderTopColor','--ring');
    assert.match(await page.locator('#restartAppCommands').evaluate(e=>getComputedStyle(e).boxShadow),/okl(?:ch|ab)/);
    await page.locator('#saveRestartCommands').hover();
    await token(page,'#saveRestartCommands','backgroundColor','color-mix(in oklab,var(--primary) 90%,transparent)');
    await page.mouse.down();await token(page,'#saveRestartCommands','backgroundColor','color-mix(in oklab,var(--primary) 80%,transparent)');
    // Prevent the simulated press from saving test configuration.
    await page.mouse.move(0,0);await page.mouse.up();
    await page.locator('#defaultRestartCommands').hover();await token(page,'#defaultRestartCommands','backgroundColor','color-mix(in oklab,var(--secondary) 80%,transparent)');
    await page.evaluate(()=>document.getElementById('saveRestartCommands').disabled=true);
    assert.equal(await page.locator('#saveRestartCommands').evaluate(e=>getComputedStyle(e).opacity),'0.5');
    await page.evaluate(()=>document.getElementById('saveRestartCommands').disabled=false);
    await page.locator('#restartAppCommands').evaluate(e=>e.setAttribute('aria-invalid','true'));
    await token(page,'#restartAppCommands','borderTopColor','--destructive');
    await page.locator('#restartAppCommands').evaluate(e=>e.removeAttribute('aria-invalid'));
    await page.evaluate(()=>{show('Synthetic theme success');});await token(page,'#banner','color','--chart-2');
    await page.evaluate(()=>{show('Synthetic theme failure',false);});await token(page,'#banner','color','--destructive');
    await page.evaluate(()=>{clearBanner();document.getElementById('autoPingToggle').checked=true;});
    assert.equal(await page.locator('#autoPingToggle').evaluate(e=>getComputedStyle(e.nextElementSibling).backgroundColor),await page.locator('#saveRestartCommands').evaluate(e=>getComputedStyle(e).backgroundColor));
    await page.evaluate(()=>document.getElementById('autoPingToggle').checked=false);
    await page.locator('#restartAppCommands').press('Tab');assert.equal(await page.evaluate(()=>document.activeElement.id),'saveRestartCommands');
    await page.evaluate(()=>{document.getElementById('restartAppCommands').blur();renderLogin({status:'cancelled',method:'manual',message:'Synthetic cancelled session'})});
    for(const width of [390,768]){
     await page.setViewportSize({width,height:900});
     for(const view of ['dashboard','accounts','api-accounts','diagnostics','activity','settings']){
      await navigate(page,view);await noOverflow(page);
     }
     await navigate(page,'accounts');await page.evaluate(()=>openAccountDialog('login'));
     const a=await page.locator('#loginAlias').boundingBox(),b=await page.locator('#loginMethod').boundingBox();
     assert.equal(a.height,b.height);if(width>=768)assert.equal(a.y,b.y);
     assert.notEqual(await page.locator('#loginMethod').evaluate(e=>getComputedStyle(e).backgroundImage),'none','Select affordance removed');
     await page.locator('#loginAlias').fill('Theme draft');await page.keyboard.press('Escape');
     await page.evaluate(()=>openAccountDialog('login'));assert.equal(await page.locator('#loginAlias').inputValue(),'Theme draft');
     await noOverflow(page);await page.screenshot({path:path.join(artifacts,`login-dark-${width}.png`),fullPage:false});
     await page.evaluate(()=>closeAccountDialog());
     await page.evaluate(()=>toggleExport('accounts'));await page.locator('.export-account').first().check();
     await token(page,'.export-row:has(input:checked)','borderTopColor','--primary');
     await page.keyboard.press('Escape');await noOverflow(page);
    }
   }
   await page.close();
  }
  assert.deepEqual(errors,[]);console.log('PASS: exact source tokens, six pages and eight modal states; Light colors/layout and dark layout preserved. Screenshots: '+artifacts);
 }finally{
  if(browser)await browser.close();
  if(fixture.exitCode===null&&fixture.signalCode===null){const stopped=new Promise(r=>fixture.once('exit',r));fixture.kill('SIGTERM');await stopped}
 }
})().catch(e=>{console.error(e);process.exitCode=1});
