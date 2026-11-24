// Exercise the actual browser handler with isolated network and confirmation mocks.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const source = fs.readFileSync('web/assets/app.js','utf8').split('async function resetAccountUsage(')[1];
async function run(accepted) {
  const calls=[]; let confirmation; let refreshed=false;
  const context={
    fetch:async(url,options)=>{
      calls.push({url,body:JSON.parse(options.body)});
      return {ok:true,json:async()=>url.endsWith('prepare')?
        {ok:true,query:'stable-account',confirmation_token:'one-use',message:'Consume one credit?',warnings:['60% weekly remaining','Next reset in less than 24 hours']}:
        {ok:true,message:'Usage reset completed.'}};
    },
    window:{confirm:text=>{confirmation=text;return accepted;},alert:()=>{}},
    loadAccounts:async()=>{refreshed=true;}
  };
  vm.createContext(context);
  vm.runInContext('async function resetAccountUsage('+source,context);
  const button={disabled:false};
  await context.resetAccountUsage('stable-account',button);
  assert.match(confirmation,/60%/); assert.match(confirmation,/24 hours/);
  assert.equal(calls.length,accepted?2:1);
  assert.equal(refreshed,accepted); assert.equal(button.disabled,false);
  if(accepted) assert.equal(calls[1].body.confirmation_token,'one-use');
}
(async()=>{await run(false);await run(true);console.log('Web reset confirmation and cancellation passed');})().catch(e=>{console.error(e);process.exitCode=1;});
