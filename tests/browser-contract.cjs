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
  await page.route('**/api/jobs/'+actual.id,route=>route.fulfill({json:job}));
  await page.route('**/api/jobs/'+actual.id+'/predict',route=>route.fulfill({json:{prediction:0,classes:[0,1],probability:.9,probabilities:[.1,.9]}}));
  await page.route('**/api/jobs/'+actual.id+'/import',route=>{const body=route.request().postDataJSON();if(body.preview)return route.fulfill({json:{preview_digest:'fixture',step:40,current_step:80,rollback_required:true,verified:true,status:'paused'}});committed=body;job={...job,status:'paused',step:40};return route.fulfill({json:job});});
  await page.route('**/api/cache/cleanup',route=>{cleaned=true;return route.fulfill({json:{message:'Fixture cache clean completed.'}});});
  await page.goto(base+'/#job/'+actual.id);
  await page.getByRole('heading',{name:'Latest five-minute summary',exact:true}).waitFor();
  assert((await page.locator('#job-summary').textContent()).includes('Unknown'));
  await page.getByText('Model files (1)',{exact:true}).click();assert((await page.locator('.artifact-files').textContent()).includes('model.json'));
  await page.getByLabel('Numeric features, in dataset column order').fill('1,2,3,4');await page.getByRole('button',{name:'Run prediction',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.prediction-result')?.textContent.includes('10%'));
  await page.locator('#guided-import').evaluate(el=>el.open=true);
  await page.getByLabel('Downloaded output folder or ZIP').fill('C:/fixture/older.zip');await page.getByRole('button',{name:'Preview & verify results',exact:true}).click();
  const commit=page.getByRole('button',{name:'Import verified results',exact:true});await commit.waitFor();assert(await commit.isDisabled(),'Older import was allowed before explicit rollback');
  await page.getByRole('checkbox',{name:'Allow replacing newer progress with an earlier checkpoint'}).check();await commit.click();
  await page.locator('#guided-setup').waitFor();assert.equal(committed.allow_rollback,true);assert.equal(committed.preview_digest,'fixture');
  await page.goto(base+'/#settings');await page.getByRole('heading',{name:'Disposable downloads',exact:true}).waitFor();
  page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'Clean disposable downloads',exact:true}).click();assert.equal(cleaned,false);
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Clean disposable downloads',exact:true}).click();assert.equal(cleaned,true);
  assert.deepEqual(errors,[]);console.log('UI contract fixtures passed: five-minute summary, verified artifact list, class-zero confidence, explicit older-checkpoint import, persistent guided controls, cache confirmation.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
