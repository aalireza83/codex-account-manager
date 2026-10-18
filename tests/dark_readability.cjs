// Focused regression for dark links/icons/switches and compact native account cards.
const {chromium}=require('playwright');
const {spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),net=require('node:net'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),ref=process.env.THEME_BASELINE_REF||'HEAD';
const old=file=>execFileSync('git',['show',`${ref}:${file}`],{cwd:root,encoding:'utf8'});
function contrast(element,pseudo=null){
 const ctx=document.createElement('canvas').getContext('2d');
 const rgba=value=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=value;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].map((v,i)=>i===3?v/255:v)};
 const over=(f,b)=>[0,1,2].map(i=>f[i]*f[3]+b[i]*(1-f[3])).concat(1);
 const bg=e=>e?over(rgba(getComputedStyle(e).backgroundColor),bg(e.parentElement)):[255,255,255,1];
 const lum=rgb=>rgb.slice(0,3).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
 const s=getComputedStyle(element,pseudo),background=bg(element),foreground=over(rgba(pseudo?s.backgroundColor:s.color),background),values=[lum(foreground),lum(background)].sort((a,b)=>b-a);
 return (values[0]+.05)/(values[1]+.05);
}
const paint=e=>{const s=getComputedStyle(e);return ['color','backgroundColor','backgroundImage','borderTopColor','opacity','stroke','fill'].map(p=>s[p])};
(async()=>{
 const listener=net.createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));const port=listener.address().port;await new Promise(r=>listener.close(r));
 const base=`http://127.0.0.1:${port}`,fixture=spawn(process.env.PYTHON||'python3.12',['tests/ui_fixture.py'],{cwd:root,env:{...process.env,CODEX_UI_TEST_PORT:String(port)},stdio:['ignore','pipe','pipe']});
 let output='',browser;fixture.stdout.on('data',c=>output+=c);fixture.stderr.on('data',c=>output+=c);
 try{
  for(let i=0;i<100;i++){if(fixture.exitCode!==null)throw Error(output);try{if((await fetch(base+'/api/health')).ok)break}catch{}await new Promise(r=>setTimeout(r,100))}
  browser=await chromium.launch({args:['--no-sandbox']});
  const baseline={},screenshots=fs.mkdtempSync(path.join(os.tmpdir(),'cam-readability-'));
  for(const release of ['old','new']){
   const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   if(release==='old')for(const file of ['app.css','app.js'])await page.route(`**/assets/${file}?*`,r=>r.fulfill({contentType:file.endsWith('css')?'text/css':'application/javascript',body:old('web/assets/'+file)}));
   await page.goto(base,{waitUntil:'networkidle'});await page.waitForFunction(()=>document.querySelectorAll('#grid .card').length===2);
   await page.evaluate(()=>{showView('accounts');document.documentElement.classList.remove('dark')});
   const light={};
   for(const selector of ['.card .name','.card .email','.card .plan','.card .metric .label','.card .metric .value','.card button','.stat-icon','.switch-track','a'])light[selector]=await page.locator(selector).first().evaluate(paint);
   await page.evaluate(()=>{const e=document.getElementById('autoPingToggle');e.checked=true});
   light.thumb=await page.locator('#autoPingToggle + .switch-track').evaluate(e=>getComputedStyle(e,'::after').backgroundColor);
   if(release==='old')baseline.light=light;else assert.deepEqual(light,baseline.light,'Light palette changed');
   for(const width of [1440,1920,768,390]){
    await page.setViewportSize({width,height:1000});
    const heights=await page.locator('#grid .card').evaluateAll(es=>es.map(e=>e.getBoundingClientRect().height));
    const actions=await page.locator('#grid .card button').evaluateAll(es=>es.map(e=>[e.textContent,e.getAttribute('onclick'),e.disabled]));
    if(release==='old')baseline[width]={heights,actions};else{
     heights.forEach((h,i)=>assert(h<baseline[width].heights[i],`Card did not shrink at ${width}: ${h} >= ${baseline[width].heights[i]}`));
     assert.deepEqual(actions,baseline[width].actions,'Account controls changed');
     assert.equal(await page.locator('#grid .account-details .account-resets').count(),2);
     assert.equal(await page.locator('#grid .usage-stack .metric').count(),4);
     assert(!await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),'Horizontal overflow');
     console.log(`Cards ${width}px: ${baseline[width].heights.join('/')} → ${heights.join('/')}px`);
    }
   }
   if(release==='new'){
    await page.setViewportSize({width:1440,height:1000});await page.evaluate(()=>document.documentElement.classList.add('dark'));
    await page.screenshot({path:path.join(screenshots,'accounts-dark.png')});
    await page.evaluate(()=>{const e=document.createElement('a');e.id='contrastLink';e.href='#';e.textContent='Link';document.querySelector('.panel').append(e)});
    const link=page.locator('#contrastLink');assert(await link.evaluate(contrast)>=4.5,'Dark link contrast');await link.hover();assert(await link.evaluate(contrast)>=4.5,'Hovered link contrast');await link.focus();assert(await link.evaluate(contrast)>=4.5,'Focused link contrast');
    assert(await page.locator('.stat-icon:not(.green):not(.purple)').first().evaluate(contrast)>=3,'Blue icon contrast');
    await page.evaluate(()=>showView('settings'));
    assert(await page.locator('#autoPingToggle + .switch-track').evaluate(e=>getComputedStyle(e,'::after').backgroundColor)==='rgb(255, 255, 255)','Checked switch thumb');
    assert(await page.locator('#autoPingToggle + .switch-track').evaluate(contrast,'::after')>=3,'Checked switch contrast');
    await page.evaluate(()=>document.getElementById('autoPingToggle').checked=false);
    assert(await page.locator('#autoPingToggle + .switch-track').evaluate(contrast,'::after')>=3,'Unchecked switch contrast');
    // A zero reset balance remains visible; an absent balance adds no empty row.
    await page.evaluate(()=>{const data=structuredClone(lastAccountData);data.accounts[0].usage.reset_credits=0;delete data.accounts[1].usage.reset_credits;render(data);showView('accounts')});
    assert.equal(await page.locator('#grid .account-resets').count(),1);assert.equal(await page.locator('#grid .account-resets .value').textContent(),'0');
   }
   assert.deepEqual(errors,[]);await page.close();
  }
  console.log('PASS: dark link/icon/switch contrast, Light colors, compact responsive cards, preserved controls and reset values. Screenshot: '+screenshots);
 }finally{if(browser)await browser.close();if(fixture.exitCode===null){const stopped=new Promise(r=>fixture.once('exit',r));fixture.kill();await stopped}}
})().catch(e=>{console.error(e);process.exitCode=1});
