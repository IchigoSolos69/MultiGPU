/* Provider-tool completion regression. All provider and installer APIs are fixtures. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&!base.endsWith(':8765'),'Use an isolated localhost controller.');
 const metadata=await(await fetch(base+'/api/bootstrap')).json();
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let tasks=[],ready=false,current='tools',guideReads=0,installs=0,checks=0,accountVerified=false;
 const setup=()=>({provider_id:'kaggle',title:'Kaggle fixture setup',steps:[
  {id:'tools',title:'Prepare the native provider tools',description:'Install the provider client into the app environment.',completion:'automatic',ready,can_complete:ready,completed:ready,blocking_reason:ready?'':'Prepare the provider tools before continuing.',actions:[{type:'environment',label:'Prepare Kaggle tools',profile:'kaggle'}]},
  {id:'verify',title:'Save settings and check the account',description:'Account verification is separate from installation.',completion:'automatic',ready:accountVerified,can_complete:accountVerified,actions:[{type:'probe',label:'Save & check'}]}
 ],progress:{current_step:current,completed_steps:ready?['tools']:[]}});
 await page.route('**/api/**',async route=>{
  const req=route.request(),url=new URL(req.url()),body=req.method()==='POST'?req.postDataJSON():null;
  const send=value=>route.fulfill({json:value,headers:{'X-MultiGPU-Token':'fixture'}});
  if(url.pathname==='/api/bootstrap')return send({...metadata,jobs:[],environment_tasks:tasks});
  if(url.pathname==='/api/runtime')return send({jobs:[],environment_tasks:tasks,connections:Object.fromEntries(metadata.providers.map(p=>[p.id,p.connection]))});
  if(url.pathname==='/api/draft')return send({draft:{}});
  if(url.pathname==='/api/environment/tasks')return send({tasks});
  if(url.pathname==='/api/environment/setup'){assert.equal(body.profile,'kaggle');installs++;const task={id:'kaggle-'+installs,profile:'kaggle',status:'running',cancellable:true,logs:['Fixture installation']};tasks=[task,...tasks];return send(task);}
  if(url.pathname==='/api/providers/kaggle/setup'){if(body)current=body.step_id;else guideReads++;return send(setup());}
  if(url.pathname==='/api/providers/kaggle/probe'){checks++;return send({ready:false,account_verified:false,detail:'Fixture account has not been checked.',config:{username:body.username}});}
  return route.fulfill({status:400,json:{error:'Unexpected fixture endpoint '+url.pathname}});
 });
 const open=async key=>{await page.goto(base+'/?fixture='+key+'#providers');await page.locator('.provider-title[data-id="kaggle"]').click();await page.getByRole('heading',{name:'Prepare the native provider tools',exact:true}).waitFor();};
 const next=()=>page.locator('.setup-next[data-context="provider"]');
 const prepare=()=>page.getByRole('button',{name:'Prepare Kaggle tools',exact:true});
 const refresh=()=>page.evaluate(()=>refresh());
 try{
  // Bootstrap replaces running with completed before pollEnvironment reads it.
  await open('completion');assert(await next().isDisabled());await prepare().click();await page.waitForFunction(()=>state.environmentTask?.status==='running');await prepare().focus();
  ready=true;tasks=tasks.map(t=>({...t,status:'completed',cancellable:false}));const initialReads=guideReads;
  await refresh();assert.equal(guideReads,initialReads+1);assert.equal(await prepare().evaluate(e=>e===document.activeElement),true,'Installation completion stole focus');
  assert(!(await next().isDisabled()),'Continue remained disabled after fresh backend readiness');assert.equal(await page.locator('#provider-setup-blocker').textContent(),'');
  assert((await page.locator('#provider-environment-task').textContent()).includes('completed'));assert.equal(checks,0);assert.equal(accountVerified,false);
  await refresh();assert.equal(guideReads,initialReads+1,'Completed task repeatedly refreshed its guide');
  tasks=[{id:'unrelated-image',profile:'image',status:'completed',logs:[]},...tasks];await refresh();assert.equal(guideReads,initialReads+1,'An unrelated task refreshed the Kaggle guide');
  const out=path.resolve('.test-runs/browser');await fs.mkdir(out,{recursive:true});await page.screenshot({path:path.join(out,'provider-tools-completed.png'),fullPage:true});
  // A completed installer never overrides a backend requirement that is still missing.
  ready=false;tasks=[];current='tools';await open('still-missing');await prepare().click();await page.waitForFunction(()=>state.environmentTask?.status==='running');await prepare().focus();tasks=tasks.map(t=>({...t,status:'completed',cancellable:false}));const blockedReads=guideReads;
  await refresh();assert.equal(guideReads,blockedReads+1);assert(await next().isDisabled());assert.equal(await page.locator('#provider-setup-blocker').textContent(),'Prepare the provider tools before continuing.');
  await refresh();assert.equal(guideReads,blockedReads+1,'Missing backend readiness caused repeated guide refreshes');
  // Reloading with an already completed task uses a fresh guide when opening the dialog.
  ready=true;current='tools';const beforeReload=guideReads;await open('already-completed');assert.equal(guideReads,beforeReload+1);assert(!(await next().isDisabled()));await refresh();assert.equal(guideReads,beforeReload+1);
  // Completion during unsaved account editing leaves the input and focus untouched.
  await page.locator('[data-action="setup-goto"][data-step="verify"]').click();await page.getByLabel('Kaggle username',{exact:true}).fill('unsaved-fixture-owner');
  const dirtyReads=guideReads;tasks=[{id:'new-kaggle-completion',profile:'kaggle',status:'completed',logs:[]},...tasks];await refresh();
  assert.equal(guideReads,dirtyReads);assert.equal(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'unsaved-fixture-owner');assert(await page.getByLabel('Kaggle username',{exact:true}).evaluate(e=>e===document.activeElement));assert(await next().isDisabled());
  const savedGuide=page.waitForResponse(r=>r.url().endsWith('/api/providers/kaggle/setup')&&r.request().method()==='GET');await page.getByRole('button',{name:'Save & check',exact:true}).click();await savedGuide;await page.waitForFunction(()=>!state.providerDirty);assert.equal(checks,1);assert.equal(guideReads,dirtyReads+1);assert(await next().isDisabled(),'Installation falsely verified the account');
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'provider-tools-proof.json'),JSON.stringify({result:'passed',at:new Date().toISOString(),checks:['bootstrap-masked-completion','prepare-button-focus','continue-and-blocker','one-refresh-per-completed-task','unrelated-task-ignored','backend-still-false','reload-already-completed','unsaved-config-protection','no-account-readiness-inference']},null,2));
  console.log('Provider tools regression passed: bootstrap completion refresh, preserved focus, enabled Continue, cleared blocker, no repeated or unrelated refresh, false readiness stays blocked, completed-task reload, protected unsaved account config.');
 }catch(e){await fs.mkdir('.test-runs/browser',{recursive:true});await page.screenshot({path:'.test-runs/browser/provider-tools-failure.png',fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
