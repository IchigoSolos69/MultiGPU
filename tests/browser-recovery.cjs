/* Browser contracts only: real isolated run as a seed, fixture recovery responses, no training or cloud mutations. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base),'Use an isolated localhost controller.');
 const metadata=await(await fetch(base+'/api/bootstrap')).json();
 assert(metadata.app.workspace_dir.replaceAll('\\','/').includes('/.test-runs/'),'Never use the installed workspace.');
 const seed=metadata.jobs.find(j=>j.status==='completed'&&j.spec.recipe==='starter');
 assert(seed,'Run browser-delivery or browser-smoke first to create a real completed starter.');
 const actual=await(await fetch(base+'/api/jobs/'+seed.id)).json();
 const out=path.resolve('.test-runs/browser-v042');await fs.mkdir(out,{recursive:true});
 const partial={...actual,name:'Recovery browser fixture',status:'paused',step:40,total_steps:100,execution_mode:'guided',provider_id:'colab',export_path:null,export_verified_at:null,artifacts:[],evaluation:null,
  message:'The notebook paused after saving a checkpoint. Review continuation before starting a new session.',events:[],metrics:[],last_summary:null,elapsed_seconds:20,
  checkpoint:{...actual.checkpoint,step:40,sha256:'reviewed-checkpoint-sha',verified_at:new Date().toISOString()},
  spec:{...actual.spec,steps:100,guided:true,providers:['colab']},allowed_actions:['continue-guided'],guided_providers:['colab']};
 let job=structuredClone(partial),guidedPreviews=0,finishPreviews=0,rejectCommit=true;
 const guidedCommits=[],finishCommits=[],unexpectedWrites=[],errors=[];
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
 try{
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()),body=request.method()==='POST'?request.postDataJSON():null;
   if(url.pathname==='/api/jobs/'+seed.id&&request.method()==='GET')return route.fulfill({json:job});
   if(url.pathname==='/api/jobs/'+seed.id+'/setup')return route.fulfill({json:{provider_id:'colab',title:'Fixture continuation guide',steps:[{id:'account',title:'Review notebook session',completion:'acknowledgement',ready:false,can_complete:true,actions:[]}],progress:{current_step:'account',completed_steps:[]}}});
   if(url.pathname==='/api/jobs/'+seed.id+'/continue-guided'&&request.method()==='POST'){
    if(body.preview){guidedPreviews++;return route.fulfill({json:{preview_digest:'guided-review-'+guidedPreviews,provider_id:'colab',checkpoint:{step:40,sha256:'reviewed-checkpoint-sha'},target:{steps:100,recipe:'starter'},requires_stopped_confirmation:true,plan:{resolved_resource:{provider_id:'colab',device:'runtime',gpu:null},warnings:['The original training target is preserved.']}}});}
    guidedCommits.push(body);
    if(rejectCommit){rejectCommit=false;return route.fulfill({status:409,json:{error:'The reviewed checkpoint changed. Preview continuation again.'}});}
    job={...job,status:'awaiting_user',allowed_actions:[]};return route.fulfill({json:job});
   }
   if(url.pathname==='/api/jobs/'+seed.id+'/finish-delivery'&&request.method()==='POST'){
    if(body.preview){finishPreviews++;return route.fulfill({json:{preview_digest:'delivery-review-'+finishPreviews,provider_id:'local',checkpoint:{step:100,sha256:'full-target-checkpoint-sha'},target:{steps:100,recipe:'starter'},requires_stopped_confirmation:false,plan:{resolved_resource:{provider_id:'local',device:'cpu',gpu:null},warnings:[]}}});}
    finishCommits.push(body);job={...job,status:'completed',allowed_actions:[]};return route.fulfill({json:job});
   }
   if(request.method()!=='GET'){unexpectedWrites.push(url.pathname);return route.fulfill({status:400,json:{error:'Unexpected write in recovery contract test'}});}
   return route.continue();
  });
  await page.goto(base+'/#job/'+seed.id);
  const review=page.locator('#recovery-review');
  const openGuided=()=>page.getByRole('button',{name:'Continue in Google Colab',exact:true}).click();
  const confirmation=()=>review.getByRole('checkbox',{name:'I checked that training in the previous notebook has stopped'});
  const prepare=()=>review.getByRole('button',{name:'Prepare continuation notebook',exact:true});
  await openGuided();await review.getByRole('heading',{name:'Continue with a notebook',exact:true}).waitFor();
  assert.equal(guidedPreviews,1);assert.equal(guidedCommits.length,0,'Preview performed a commit');
  assert((await review.textContent()).includes('Step 40'));assert((await review.textContent()).includes('100 steps'));
  assert((await review.textContent()).includes('Google Colab'));assert((await review.textContent()).includes('does not launch or stop provider compute'));
  assert(await prepare().isDisabled(),'Unobserved notebook continuation skipped stopped confirmation');
  assert.equal(await confirmation().isChecked(),false);
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:width===390?844:1000});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Recovery preview overflowed the viewport');
   await page.screenshot({path:path.join(out,'recovery-notebook-'+width+'.png'),fullPage:true});
  }
  await review.getByRole('button',{name:'Cancel',exact:true}).click();
  assert.equal((await review.textContent()).trim(),'');assert.equal(guidedCommits.length,0,'Cancel performed a commit');
  await openGuided();await confirmation().waitFor();await confirmation().check();assert(await prepare().isEnabled());
  await confirmation().uncheck();assert(await prepare().isDisabled());await confirmation().check();await prepare().click();
  await page.getByText('The reviewed checkpoint changed. Preview continuation again.',{exact:true}).waitFor();
  assert.deepEqual(guidedCommits,[{preview_digest:'guided-review-2',confirmed_stopped:true,provider_id:'colab'}]);
  assert.equal(job.step,40);assert.equal(job.total_steps,100);
  // A consumed review must disappear, including after a rejected commit.
  assert.equal((await review.textContent()).trim(),'');assert.equal(await prepare().count(),0);assert.equal(guidedCommits.length,1);
  await openGuided();await page.waitForFunction(()=>!document.querySelector('#job-action-error')?.textContent.trim());assert.equal(guidedPreviews,3);
  assert.equal(await confirmation().isChecked(),false,'Stopped confirmation leaked into a fresh review');
  assert(await prepare().isDisabled());await confirmation().check();await prepare().click();
  await page.waitForFunction(()=>!document.querySelector('#recovery-review')?.textContent.trim());
  assert.deepEqual(guidedCommits[1],{preview_digest:'guided-review-3',confirmed_stopped:true,provider_id:'colab'});
  assert.equal(job.spec.steps,100);assert.equal(job.step,40);
  job={...partial,status:'error',message:'Training reached its target, but model export failed. The full-target checkpoint is saved.',step:100,execution_mode:'automatic',provider_id:'local',checkpoint:{...partial.checkpoint,step:100,sha256:'full-target-checkpoint-sha'},spec:{...partial.spec,providers:['local'],guided:false},allowed_actions:['finish-delivery']};
  await page.reload();await page.getByRole('button',{name:'Finish evaluation and export',exact:true}).click();
  await review.getByRole('heading',{name:'Finish evaluation and export locally',exact:true}).waitFor();
  assert.equal(finishPreviews,1);assert.equal(finishCommits.length,0);
  const deliveryText=await review.textContent();assert(deliveryText.includes('Step 100')&&deliveryText.includes('100 steps'));
  assert(deliveryText.includes('CPU without further training updates'));assert(deliveryText.includes('Requested compute: CPU'));
  assert.equal(await review.getByRole('checkbox').count(),0,'Stopped local execution demanded an irrelevant notebook confirmation');
  for(const width of [1440,390]){
   await page.setViewportSize({width,height:width===390?844:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(out,'recovery-delivery-'+width+'.png'),fullPage:true});
  }
  await review.getByRole('button',{name:'Finish evaluation and export',exact:true}).click();
  await page.locator('#job-status').getByText('Completed',{exact:true}).waitFor();
  assert.deepEqual(finishCommits,[{preview_digest:'delivery-review-1',confirmed_stopped:false,provider_id:'local'}]);
  assert.deepEqual(unexpectedWrites,[]);assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'recovery-proof.json'),JSON.stringify({passed:true,at:new Date().toISOString(),seed_job_id:seed.id,fixture_only:true,preview_does_not_commit:true,stopped_confirmation_required:true,stale_review_rejected_and_repreviewed:true,target_preserved:true,delivery_cpu_without_training:true,responsive_widths:[1440,390],cloud_jobs_launched:false},null,2));
  console.log('Recovery browser contracts passed: preview/cancel safety, required stopped confirmation, stale review and fresh retry, preserved checkpoint/target, CPU delivery wording, and 1440/390 layouts.');
 }catch(error){await page.screenshot({path:path.join(out,'recovery-failure.png'),fullPage:true});throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
