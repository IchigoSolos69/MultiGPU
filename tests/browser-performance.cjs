/* Explicit isolated controller only. One local CPU run; no provider mutations. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/127\.0\.0\.1:\d+$/.test(base)&&!base.endsWith(':8765'));
 const response=await fetch(base+'/api/bootstrap'),bootstrap=await response.json(),token=response.headers.get('x-multigpu-token');
 assert(bootstrap.app.workspace_dir.replaceAll('\\','/').includes('/.test-runs/'));
 const post=async(route,body)=>{const r=await fetch(base+route,{method:'POST',headers:{'Content-Type':'application/json','X-MultiGPU-Token':token},body:JSON.stringify(body)});assert(r.ok,await r.clone().text());return r.json();};
 const spec={name:'Performance acceptance',recipe:'starter',recipe_version:2,steps:1000,batch_size:16,seed:42,device:'cpu',providers:['local'],free_only:true};
 const plan=await post('/api/jobs/preview',spec),job=await post('/api/jobs',{...spec,preview_digest:plan.preview_digest});
 let done;
 for(let i=0;i<120;i++){done=await(await fetch(base+'/api/jobs/'+job.id)).json();if(done.status==='completed'&&done.export_status==='verified')break;await new Promise(r=>setTimeout(r,250));}
 assert.equal(done.status,'completed');assert.equal(done.step,1000);assert(done.performance.samples_per_second>0);assert.equal(done.performance.memory.gpu_peak_allocated_bytes,null);
 const unchanged=await(await fetch(base+'/api/jobs/'+job.id+'?observations_revision='+done.observations_revision)).json();
 assert(unchanged.observations_unchanged);assert.equal(unchanged.metrics,undefined);
 const runtime=await(await fetch(base+'/api/runtime')).text();assert(runtime.length<JSON.stringify(bootstrap).length/2);
 const out=path.resolve('.test-runs/browser-performance');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.url().startsWith(base+'/api/'))requests.push(r.url());});
 try{
  await page.goto(base+'/#job/'+job.id);await page.getByText('Measured performance',{exact:true}).click();
  await page.getByText('Training throughput',{exact:true}).waitFor();
  assert((await page.locator('#job-performance').textContent()).includes('samples/s'));
  await page.locator('#main').focus();await page.waitForTimeout(5500);assert(await page.locator('#job-performance details').getAttribute('open')!==null,'Polling collapsed the performance ledger');
  assert(requests.some(url=>url.endsWith('/api/runtime')));assert.equal(requests.filter(url=>url.endsWith('/api/bootstrap')).length,1);
  await page.screenshot({path:path.join(out,'performance-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});await page.screenshot({path:path.join(out,'performance-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await post('/api/draft',{draft:{wizard_step:3,primary:'kaggle',fallbacks:['local'],dataset_mode:'sample',spec:{...spec,providers:['kaggle','local']}}});
  await page.goto(base+'/#new');await page.reload();await page.getByRole('button',{name:'Preview training plan',exact:true}).click();
  await page.getByRole('heading',{name:'Use your allowance efficiently',exact:true}).waitFor();
  assert((await page.getByLabel('Performance recommendations').textContent()).includes('Based on saved run'));
  assert.equal((await page.evaluate(()=>state.draft.primary)),'kaggle');
  const before=(await(await fetch(base+'/api/jobs')).json()).length;
  await page.getByRole('button',{name:'Use suggestion and preview again',exact:true}).click();
  await page.getByRole('heading',{name:'Use your allowance efficiently',exact:true}).waitFor();
  assert.equal((await(await fetch(base+'/api/jobs')).json()).length,before,'Accepting recommendation launched training');
  await page.screenshot({path:path.join(out,'recommendation-mobile.png'),fullPage:true});
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await post('/api/draft',{draft:{wizard_step:1,primary:'local',dataset_mode:'own',spec:{recipe:'image',name:'Image options',device:'cpu',dataset_path:'',epochs:5,batch_size:16,providers:['local']}}});
  await page.goto(base+'/#new');await page.reload();await page.getByText('Advanced training settings',{exact:true}).click();
  assert.equal(await page.getByLabel('Image loading',{exact:true}).inputValue(),'sync');
  assert.equal(await page.locator('[name="image_feature_cache"]').isChecked(),false);
  await page.locator('[name="image_feature_cache"]').check();await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>state.draft.spec.image_feature_cache),true);
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'proof.json'),JSON.stringify({passed:true,job_id:job.id,exact_target:1000,loopback_runtime_polling:true,unchanged_history_omitted:true,recommendation_requires_acceptance:true,acceleration_opt_in:true,cloud_jobs_launched:false,errors},null,2));
 }finally{await browser.close();}
 console.log('Performance browser acceptance passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
