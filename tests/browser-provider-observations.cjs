/* Response-order regressions. Every provider API is a fixture; no account or compute calls. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&!base.endsWith(':8765'),'Use an isolated controller.');
 const metadata=await(await fetch(base+'/api/bootstrap')).json();
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 let incoming={ready:false,account_verified:false,detail:'Not connected',config:{}},observed=incoming,pending=[],heldBoot=null,holdBoot=false,probeCalls=0;
 const verified=(second,extra={})=>({ready:true,account_verified:true,detail:'Fixture account verified',identity:{username:'fixture-owner',verified:true},quota:{remaining_hours:12,verified:true},checked_at:`2026-10-23T16:00:${String(second).padStart(2,'0')}Z`,config:{},...extra});
 const bootstrap=()=>({...metadata,jobs:[],environment_tasks:[],providers:metadata.providers.map(p=>p.id==='kaggle'?{...p,connection:incoming}:p)});
 const guide=()=>({provider_id:'kaggle',title:'Fixture account setup',steps:[{id:'verify',title:'Save settings and check the account',description:'Check your account.',completion:'automatic',ready:observed.account_verified,can_complete:observed.account_verified,completed:observed.account_verified,blocking_reason:'Verify the account before continuing.',actions:[{type:'probe',label:'Save & check'}]},{id:'review',title:'Review the limits',completion:'acknowledgement',ready:false,can_complete:true}],progress:{current_step:'verify',completed_steps:observed.account_verified?['verify']:[]}});
 await page.route('**/api/**',async route=>{
  const req=route.request(),url=new URL(req.url());
  const send=value=>route.fulfill({json:value,headers:{'X-MultiGPU-Token':'fixture'}});
  if(url.pathname==='/api/bootstrap'){const data=structuredClone(bootstrap());if(holdBoot){holdBoot=false;heldBoot=()=>send(data);return;}return send(data);}
  if(url.pathname==='/api/draft')return send({draft:{}});
  if(url.pathname==='/api/environment/tasks')return send({tasks:[]});
  if(url.pathname==='/api/providers/kaggle/setup')return send(guide());
  if(url.pathname==='/api/providers/kaggle/probe'){probeCalls++;pending.push({send,fail:()=>route.fulfill({status:503,json:{error:'Fixture latest account check failed'}})});return;}
  return route.fulfill({status:400,json:{error:'Unexpected fixture endpoint '+url.pathname}});
 });
 const check=()=>page.getByRole('button',{name:'Save & check',exact:true});
 const next=()=>page.locator('.setup-next[data-context="provider"]');
 const waitProbe=()=>page.waitForFunction(()=>!state.providerChecks.kaggle);
 const release=async result=>{observed=result;await pending.shift().send(result);await waitProbe();};
 const refresh=()=>page.evaluate(()=>refresh());
 const open=async()=>{await page.locator('[data-action="provider"][data-id="kaggle"]').first().click();await check().waitFor();};
 try{
  await page.goto(base+'/#providers');await open();await page.evaluate(()=>clearInterval(state.pollTimer));
  // The poll replaces the provider object while the account request is pending.
  await check().click();await page.waitForFunction(()=>state.providerChecks.kaggle);await refresh();
  await release(verified(1));
  assert((await page.locator('.setup-observed').textContent()).includes('fixture-owner'));
  assert.equal(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'fixture-owner');
  assert(!(await next().isDisabled()));assert((await page.locator('#toast').textContent()).includes('Provider account checked'));
  await fs.mkdir('.test-runs/browser',{recursive:true});await page.screenshot({path:'.test-runs/browser/provider-observations-verified.png',fullPage:true});
  // A response captured before a later probe must not overwrite the result afterward.
  holdBoot=true;await page.evaluate(()=>{window.pendingRefresh=refresh();});await page.waitForTimeout(50);assert(heldBoot);
  await check().click();await release(verified(2));await heldBoot();heldBoot=null;await page.evaluate(()=>window.pendingRefresh);
  assert.equal(await page.evaluate(()=>provider('kaggle').connection.checked_at),'2026-10-23T16:00:02Z');
  // Inputs typed after submission stay dirty and focused, while observations still update.
  await check().click();await page.waitForFunction(()=>state.providerChecks.kaggle);
  await page.getByLabel('Kaggle username',{exact:true}).fill('unsaved-new-owner');await release(verified(3));
  assert.equal(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'unsaved-new-owner');
  assert(await page.getByLabel('Kaggle username',{exact:true}).evaluate(e=>e===document.activeElement));assert(await next().isDisabled());
  assert((await page.locator('.setup-observed').textContent()).includes('fixture-owner'));
  // Verification succeeds even when the training allowance is exhausted.
  await check().click();await release(verified(4,{ready:false,quota:{remaining_hours:0,verified:true}}));
  assert((await page.locator('#toast').textContent()).includes('Provider account verified'));
  assert(!(await page.locator('#toast').evaluate(el=>el.classList.contains('error'))));
  assert((await page.locator('.setup-observed').textContent()).includes('0 reported GPU hours'));assert(!(await next().isDisabled()));
  // A newer authoritative failure replaces earlier success and refreshes the guide.
  incoming=observed={ready:false,account_verified:false,detail:'Fixture credentials expired',checked_at:'2026-10-23T16:00:05Z',config:{}};
  await refresh();assert((await page.locator('.setup-observed').textContent()).includes('credentials expired'));assert(await next().isDisabled());
  // A rejected request is visible and never saves edits or fabricates verification.
  await page.getByLabel('Kaggle username',{exact:true}).fill('retry-owner');await check().click();
  await pending.shift().fail();await waitProbe();await page.locator('#provider-error').getByText('Fixture latest account check failed',{exact:true}).waitFor();
  assert.equal(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'retry-owner');assert(await next().isDisabled());
  // Closing/reopening cannot launch overlapping checks that reorder backend storage.
  await check().click();await page.waitForFunction(()=>state.providerChecks.kaggle);
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Close provider setup',exact:true}).click();await open();
  const calls=probeCalls;await check().click();await page.getByText('An account check is already running for this provider. Wait for it to finish before checking again.',{exact:true}).waitFor();assert.equal(probeCalls,calls);
  await release(verified(6));await refresh();assert(!(await next().isDisabled()));
  await page.getByRole('button',{name:'Close provider setup',exact:true}).click();
  assert((await page.locator('tr').filter({hasText:'Kaggle Notebooks'}).textContent()).includes('Signed in'));
  // A completed check after closing setup cannot replace a focused search field.
  await open();await check().click();await page.waitForFunction(()=>state.providerChecks.kaggle);
  await page.getByRole('button',{name:'Close provider setup',exact:true}).click();
  await page.getByLabel('Search providers',{exact:true}).fill('Kaggle');await release(verified(7));
  assert.equal(await page.getByLabel('Search providers',{exact:true}).inputValue(),'Kaggle');
  assert(await page.getByLabel('Search providers',{exact:true}).evaluate(el=>el===document.activeElement));
  assert((await page.locator('tr').filter({hasText:'Kaggle Notebooks'}).textContent()).includes('Signed in'));
  // Closing a checked dialog updates overview even though its launcher retains focus.
  await page.goto(base+'/#overview');await page.evaluate(()=>clearInterval(state.pollTimer));await open();
  incoming={ready:false,account_verified:false,detail:'Old bootstrap',config:{}};
  await check().click();await release(verified(8));await page.getByRole('button',{name:'Close provider setup',exact:true}).click();
  const kaggle=page.locator('.resource-row').filter({hasText:'Kaggle Notebooks'});assert((await kaggle.textContent()).includes('Signed in'));
  // Explicit Refresh updates overview while the Refresh button is focused.
  incoming=observed={ready:false,account_verified:false,detail:'Fixture final check failed',checked_at:'2026-10-23T16:00:09Z',config:{}};
  await page.getByRole('button',{name:'Refresh workspace',exact:true}).focus();await page.evaluate(()=>refresh(true));assert((await kaggle.textContent()).includes('Account setup needs attention'));
  assert.equal(await page.evaluate(()=>providerConfigView({...provider('modal'),connection:{config:{native_spend_limit:0}}}).includes('value="0"')),true,'Zero spending cap rendered empty');
  assert.deepEqual(errors,[]);await fs.mkdir('.test-runs/browser',{recursive:true});
  await fs.writeFile('.test-runs/browser/provider-observations-proof.json',JSON.stringify({result:'passed',at:new Date().toISOString(),checks:['bootstrap-during-probe','stale-bootstrap-after-probe','native-identity-username','dirty-focused-inputs','verified-exhausted-quota','newer-failed-observation','transport-error','serialized-reopened-checks','directory-on-close','closed-dialog-search-focus','overview-on-close','focused-manual-refresh','zero-spending-cap']},null,2));
  await page.screenshot({path:'.test-runs/browser/provider-observations-overview.png',fullPage:true});
  console.log('Provider observation regressions passed: response ordering, current identity, dirty focus, quota distinction, newer failure, transport error, serialized checks, directory/overview refresh and zero cap.');
 }catch(e){await fs.mkdir('.test-runs/browser',{recursive:true});await page.screenshot({path:'.test-runs/browser/provider-observations-failure.png',fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
