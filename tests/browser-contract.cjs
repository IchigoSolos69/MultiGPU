/* Deterministic browser contract tests. All state-changing responses are fixtures, no cloud calls. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&!base.endsWith(':8765'),'Use an isolated localhost test server.');
 const data=await (await fetch(base+'/api/bootstrap')).json();
 const actual=data.jobs.find(j=>j.status==='completed'&&j.spec.recipe==='starter');assert(actual,'Run browser-smoke first to create the real local training fixture.');
 let job=await(await fetch(base+'/api/jobs/'+actual.id)).json(),committed=null,cleaned=false;
 job={...job,status:'awaiting_user',step:80,total_steps:100,execution_mode:'guided',provider_id:'colab',spec:{...job.spec,providers:['colab'],guided:true},allowed_actions:['import','bundle','predict','export','archive'],artifacts:[{path:'model.json',size_bytes:2048,sha256:'fixture'}],export_verified_at:new Date().toISOString(),last_summary:{remaining_session_seconds:90,checkpoint_age_seconds:null,active_gpu_count:0,allowance_confidence:'Unknown',required_action:'Import downloaded notebook results.'}};
 const browser=await chromium.launch({channel:'chrome',headless:true});const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.route(url=>url.pathname==='/api/jobs/'+actual.id,route=>route.fulfill({json:job}));
  await page.route('**/api/jobs/'+actual.id+'/predict',route=>route.fulfill({json:{prediction:0,classes:[0,1],probability:.9,probabilities:[.1,.9]}}));
  await page.route('**/api/jobs/'+actual.id+'/import',route=>{const body=route.request().postDataJSON();if(body.preview)return route.fulfill({json:{preview_digest:'fixture',step:40,current_step:80,rollback_required:true,verified:true,status:'paused'}});committed=body;job={...job,status:'paused',step:40};return route.fulfill({json:job});});
  await page.route('**/api/cache/cleanup',route=>{cleaned=true;return route.fulfill({json:{message:'Fixture cache clean completed.'}});});
  await page.goto(base+'/#job/'+actual.id);
  await page.getByRole('heading',{name:'Latest five-minute summary',exact:true}).waitFor();
  assert((await page.locator('#job-summary').textContent()).includes('Unknown'));
  await page.getByText('Model files (1)',{exact:true}).click();assert((await page.locator('.artifact-files').textContent()).includes('model.json'));
  const featureInputs=page.locator('[data-feature-name]');if(await featureInputs.count()){for(let i=0;i<await featureInputs.count();i++)await featureInputs.nth(i).fill(String([1, 2, 3, 4][i%4]));}else await page.getByLabel('Numeric features, in dataset column order').fill('1,2,3,4');await page.getByRole('button',{name:'Run prediction',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.prediction-result')?.textContent.includes('10%'));
  await page.locator('#guided-import').evaluate(el=>el.open=true);
  await page.getByLabel('Downloaded output folder or ZIP').fill('C:/fixture/older.zip');await page.getByRole('button',{name:'Preview & verify results',exact:true}).click();
  const commit=page.getByRole('button',{name:'Import verified results',exact:true});await commit.waitFor();assert(await commit.isDisabled(),'Older import was allowed before explicit rollback');
  await page.getByRole('checkbox',{name:'Allow replacing newer progress with an earlier checkpoint'}).check();await commit.click();
  await page.locator('#guided-setup').waitFor();assert.equal(committed.allow_rollback,true);assert.equal(committed.preview_digest,'fixture');
  let diagnosticReads=0;await page.route('**/api/diagnostics/logs',route=>{diagnosticReads++;return route.fulfill({json:{logs:'Kaggle command exited with code 2. Authorization: [REDACTED]\nRead '+diagnosticReads,path:'C:/fixture/workspace/logs/app.log'}});});
  await page.goto(base+'/#settings');await page.getByRole('heading',{name:'Disposable downloads',exact:true}).waitFor();
  await page.getByText('View application log',{exact:true}).click();await page.waitForFunction(()=>document.querySelector('#diagnostics-log')?.textContent.includes('Read 1'));
  assert((await page.locator('#diagnostics-path').textContent()).endsWith('logs/app.log'));
  await page.getByRole('button',{name:'Refresh log',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#diagnostics-log')?.textContent.includes('Read 2'));assert.equal(diagnosticReads,2);
  page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Clean disposable downloads',exact:true}).click();assert.equal(cleaned,false);
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Clean disposable downloads',exact:true}).click();assert.equal(cleaned,true);
  // Failure recovery uses the existing setup guide and reconciliation, never a new launch.
  await page.route('**/api/contract-http-error',route=>route.fulfill({status:400,json:{error:'Fixture client error',error_code:'unsupported_cli',transient:true}}));
  const metadata=await page.evaluate(async()=>{try{await api('/api/contract-http-error');}catch(e){return {code:e.error_code,transient:e.transient};}});
  assert.deepEqual(metadata,{code:'unsupported_cli',transient:true});
  let reconciled=0,probeCalls=0,launches=0,probeCode='missing_dependency';
  let providerGuide={provider_id:'kaggle',title:'Kaggle fixture setup',steps:[
   {id:'tools',title:'Prepare tools',description:'App-managed tools',completion:'automatic',ready:false,can_complete:false,actions:[{type:'environment',label:'Prepare Kaggle tools',profile:'kaggle'}]},
   {id:'signin',title:'Native sign-in',description:'Use native authentication',completion:'acknowledgement',ready:false,can_complete:true,commands:['native-signin-fixture'],actions:[]},
   {id:'verify',title:'Verify expected account',description:'Check the selected account',completion:'automatic',ready:false,can_complete:false,actions:[{type:'probe',label:'Save & check'}]}
  ],progress:{current_step:'verify',completed_steps:[]}};
  await page.route('**/api/providers/kaggle/setup',route=>{if(route.request().method()==='POST'){const body=route.request().postDataJSON();providerGuide={...providerGuide,progress:{...providerGuide.progress,current_step:body.step_id}};}return route.fulfill({json:providerGuide});});
  await page.route('**/api/providers/kaggle/probe',route=>{probeCalls++;return route.fulfill({json:{ready:false,connection_status:probeCode==='auth'?'auth_required':'unknown',error_code:probeCode,transient:probeCode==='network',detail:'Fixture provider setup failed',config:{username:'fixture-user'}}});});
  await page.route('**/api/jobs/'+actual.id+'/reconcile',route=>{reconciled++;return route.fulfill({json:job});});
  await page.route('**/api/jobs',route=>{if(route.request().method()==='POST')launches++;return route.fulfill({json:{error:'Unexpected launch request'},status:400});});
  job={...job,status:'interrupted',execution_mode:'automatic',provider_id:'kaggle',submission_status:'unknown',phase:'preparing',connection_status:'unknown',last_worker_event_at:null,error_code:'network',allowed_actions:['reconcile'],spec:{...job.spec,providers:['kaggle'],guided:false},message:'Fixture submission was not confirmed'};
  await page.goto(base+'/#job/'+actual.id);
  await page.getByText('Provider launch not confirmed',{exact:true}).waitFor();
  assert((await page.locator('#job-live').textContent()).includes('does not confirm that provider compute is stopped'));
  assert.equal(await page.getByRole('button',{name:'Resume training',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Start training',exact:true}).count(),0);
  assert.equal(await page.getByRole('button',{name:'Repeat settings',exact:true}).count(),0);
  await page.locator('#job-live').getByRole('button',{name:'Check provider state',exact:true}).click();assert.equal(reconciled,1);
  job={...job,error_code:'unsupported_cli'};await page.reload();await page.getByText('Provider launch not confirmed',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Repair provider tools',exact:true}).count(),0);
  job={...job,status:'error',submission_status:'prepared',error_code:'unsupported_cli',message:'Fixture unsupported CLI'};
  await page.reload();await page.getByText('Training failed',{exact:true}).waitFor();
  assert(!(await page.locator('#job-live').textContent()).includes('Preparing the environment'));
  await page.getByRole('button',{name:'Repair provider tools',exact:true}).click();
  await page.locator('[data-context="provider"][data-step="tools"][aria-current="step"]').waitFor();
  for(const [code,target,label] of [['missing_dependency','tools','Prepare provider tools'],['unsupported_cli','tools','Repair provider tools'],['auth','signin','Open sign-in steps'],['account_mismatch','verify','Review account settings'],['permission','verify','Review account permissions'],['quota','verify','Review provider allowance'],['invalid_resource','verify','Review resource settings']]){
   probeCode=code;await page.locator('[data-context="provider"][data-step="verify"]').click();
   await page.getByRole('button',{name:'Save & check',exact:true}).filter({hasNot:page.locator('.hidden')}).first().click();
   await page.locator('.setup-observed [data-error-code="'+code+'"]').waitFor();
   assert(await page.locator('#toast').evaluate(el=>el.classList.contains('error')),'Failed verification used a success toast');
   await page.locator('.setup-observed').getByRole('button',{name:label,exact:true}).click();
   await page.locator('[data-context="provider"][data-step="'+target+'"][aria-current="step"]').waitFor();
  }
  probeCode='network';await page.locator('[data-context="provider"][data-step="verify"]').click();
  await page.locator('[data-action="probe"]').click();await page.locator('.setup-observed [data-error-code="network"]').waitFor();
  const beforeRetry=probeCalls;await page.getByRole('button',{name:'Check connection again',exact:true}).click();assert.equal(probeCalls,beforeRetry+1);assert.equal(launches,0);
  assert.deepEqual(errors,[]);console.log('UI contract fixtures passed: five-minute summary, verified artifact list, class-zero confidence, explicit older-checkpoint import, persistent guided controls, cache confirmation, structured errors, targeted provider recovery, safe unconfirmed-launch reconciliation, lazy diagnostic log loading and refresh.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
