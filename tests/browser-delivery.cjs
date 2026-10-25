/* Real isolated notebook round trip. No cloud accounts, provider jobs or mocked training. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL,python=process.env.MULTIGPU_TEST_PYTHON;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&python,'Use an isolated controller and an explicit test Python.');
 const bootResponse=await fetch(base+'/api/bootstrap'),metadata=await bootResponse.json(),token=bootResponse.headers.get('x-multigpu-token');
 assert(metadata.app.workspace_dir.replaceAll('\\','/').includes('/.test-runs/'),'Never use the installed workspace.');
 const api=async(url,body)=>{const r=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MultiGPU-Token':token},body:JSON.stringify(body)});const value=await r.json();assert(r.ok,JSON.stringify(value));return value;};
 const out=path.resolve(process.env.MULTIGPU_TEST_OUTPUT||'.test-runs/browser-v042');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  await api('/api/draft',{draft:{wizard_step:0,spec:{}}});
  const draft=await api('/api/draft');
  await page.goto(base+'/#new');await page.getByRole('heading',{name:'What are you training?',exact:true}).waitFor();
  if(!draft.draft.has_saved_draft)assert.equal(await page.getByText('Draft restored. Paid compute and script permissions need your confirmation again.',{exact:true}).count(),0);
  const spec={name:'Real notebook round trip '+Date.now(),recipe:'starter',recipe_version:2,providers:['colab'],device:'cpu',steps:12,guided:true,free_only:true};
  const plan=await api('/api/jobs/preview',spec);assert(plan.preparation_eligible);
  const job=await api('/api/jobs',{...spec,preview_digest:plan.preview_digest});
  await page.goto(base+'/#job/'+job.id);await page.getByRole('button',{name:'Download training notebook',exact:true}).waitFor();
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download training notebook',exact:true}).click();
  const file=path.join(out,'roundtrip-training.ipynb');await(await download).saveAs(file);
  const notebook=JSON.parse(await fs.readFile(file,'utf8')),code=notebook.cells.filter(c=>c.cell_type==='code').map(c=>c.source.join(''));
  const training=code.find(c=>c.includes('launch_notebook'));assert(training,'Notebook has no isolated worker launcher');
  const runtime=path.join(out,'runtime-'+job.id);await fs.mkdir(runtime,{recursive:true});
  const script=path.join(runtime,'roundtrip.py');
  const source=code.join('\n')+`\nimport hashlib, json\nfirst_checkpoint = (output / 'checkpoint.json').read_bytes()\nfirst_events = (output / 'events.jsonl').read_bytes()\nexec(${JSON.stringify(training)})\nassert (output / 'checkpoint.json').read_bytes() == first_checkpoint, 'Completed rerun replaced checkpoint'\nassert (output / 'events.jsonl').read_bytes() == first_events, 'Completed rerun trained again'\nfrom multigpu.notebook_runtime import package_results\nresult = package_results(output)\nPath('result-path.txt').write_text(str(result))\n`;
  await fs.writeFile(script,source);
  const executed=spawnSync(python,[script],{cwd:runtime,encoding:'utf8',timeout:60000,maxBuffer:4*1024*1024});
  await fs.writeFile(path.join(runtime,'execution.log'),(executed.stdout||'')+(executed.stderr||''));
  assert.equal(executed.status,0,executed.stderr||executed.error?.message);const results=(await fs.readFile(path.join(runtime,'result-path.txt'),'utf8')).trim();
  await page.getByText('Already downloaded results? Import them here',{exact:true}).click();
  await page.getByLabel('Downloaded output folder or ZIP',{exact:true}).fill(path.join(runtime,'missing.zip'));await page.getByRole('button',{name:'Preview & verify results',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#job-action-error')?.textContent.trim());
  await page.getByLabel('Downloaded output folder or ZIP',{exact:true}).fill(results);await page.getByRole('button',{name:'Preview & verify results',exact:true}).click();await page.getByRole('heading',{name:'Results verified',exact:true}).waitFor();
  assert.equal((await page.locator('#job-action-error').textContent()).trim(),'','Old import error survived a successful preview');
  assert.equal(await page.locator('#allow-rollback').count(),0,'Unneeded rollback consent was shown');
  assert((await page.locator('#import-preview').textContent()).includes('12 of 12 steps'));
  await page.getByRole('button',{name:'Import verified results',exact:true}).click();await page.locator('#guided-setup').getByRole('heading',{name:'Results verified',exact:true}).waitFor();
  assert.equal(await page.getByRole('heading',{name:'Continue this run in Google Colab',exact:true}).count(),0);
  assert(!(await page.locator('#job-setup-guide').isVisible()),'Completed setup was not collapsed');
  const imported=await api('/api/jobs/'+job.id);assert.equal(imported.status,'completed');assert(imported.metrics.length>=2&&imported.metrics.length<=12);assert.equal(imported.metrics.at(-1).step,12);assert(imported.last_summary);assert(imported.checkpoint.verified_at);assert(imported.export_verified_at);
  assert((await page.locator('#job-live').textContent()).includes('Source: imported notebook'));
  assert(await page.locator('#job-chart svg').count());
  assert(!(await page.locator('#export-form').isVisible()),'Completed export form was not secondary');
  for(let i=1;i<=4;i++)await page.getByLabel('feature_'+i,{exact:true}).fill(String(i));
  await page.getByRole('button',{name:'Run prediction',exact:true}).click();await page.locator('.prediction-result').waitFor();
  for(const width of [1440,390]){await page.setViewportSize({width,height:width===390?844:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'imported-results-'+width+'.png'),fullPage:true});}
  // Modal's unverified free-only guard is visible before native installation/sign-in.
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'Repeat settings',exact:true}).click();
  await page.getByRole('button',{name:'Continue',exact:true}).click();await page.getByRole('button',{name:'Continue',exact:true}).click();
  await page.getByLabel('Primary resource',{exact:true}).selectOption('modal');await page.getByText('Modal retired its hosted notebook editor on 23 October 2026.',{exact:false}).waitFor();
  assert((await page.locator('.selected-resource').textContent()).includes('Automatic free-only execution is unavailable'));
  assert.equal(await page.getByRole('button',{name:'Review a manual notebook',exact:true}).count(),0,'Retired Modal notebook action remained visible');
  for(const width of [1440,390]){await page.setViewportSize({width,height:width===390?844:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(out,'modal-compute-only-'+width+'.png'),fullPage:true});}
  await page.setViewportSize({width:1440,height:1000});await page.getByRole('button',{name:'Continue',exact:true}).click();await page.getByRole('button',{name:'Preview training plan',exact:true}).click();await page.getByRole('heading',{name:'Training preparation is blocked',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Start training',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'Set up Modal training',exact:true}).count(),0);
  await page.getByRole('button',{name:'Compute & limits',exact:false}).click();await page.getByLabel('Primary resource',{exact:true}).selectOption('colab');await page.getByRole('button',{name:'Continue',exact:true}).click();await page.getByRole('button',{name:'Preview training plan',exact:true}).click();await page.getByRole('heading',{name:'Manual notebook setup is available',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Set up Google Colab training',exact:true}).count(),1);
  const legacy={...imported,status:'paused',provider_id:'modal',execution_mode:'guided',step:4,total_steps:12,export_path:null,export_verified_at:null,checkpoint:{...imported.checkpoint,step:4},metrics:[],events:[],last_summary:null,observation_source:'notebook_import',allowed_actions:['import','continue-guided','resume-local'],guided_providers:['colab'],spec:{...imported.spec,providers:['modal','colab'],guided:true}};
  await page.route(url=>url.pathname==='/api/jobs/'+job.id,route=>route.fulfill({json:legacy}));
  await page.route('**/api/jobs/'+job.id+'/setup',route=>route.fulfill({json:{provider_id:'modal',title:'Recover existing Modal notebook results',steps:[{id:'retired',title:'Hosted notebooks are unavailable',completion:'automatic',ready:false,can_complete:false,instructions:['Import previously saved results or choose another supported provider.'],actions:[]}],progress:{current_step:'retired',completed_steps:[]}}}));
  await page.goto(base+'/#job/'+job.id);await page.getByRole('heading',{name:'Saved notebook session in Modal',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Continue in Modal',exact:true}).count(),0);assert.equal(await page.getByRole('button',{name:'Continue with a notebook',exact:true}).count(),1);
  assert.equal(await page.getByRole('button',{name:'Download training notebook',exact:true}).count(),0);
  await page.getByText('Already downloaded results? Import them here',{exact:true}).click();await page.getByLabel('Downloaded output folder or ZIP',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'delivery-proof.json'),JSON.stringify({passed:true,at:new Date().toISOString(),job_id:job.id,recipe:'starter',device:'cpu',training_steps:12,imported_metrics:imported.metrics.length,checkpoint_verified:true,model_export_verified:true,completed_rerun_reused:true,prediction:true,modal_free_only_block_visible:true,modal_notebook_route_removed:true,legacy_modal_import_available:true,colab_assisted_alternative:true,cloud_jobs_launched:false},null,2));
  console.log('Real notebook delivery passed: safe completed rerun, one current archive, verified import/history, readable preview, cleared errors, collapsed completed guide/export, prediction, responsive layout and early Modal free-only alternatives.');
 }catch(e){await page.screenshot({path:path.join(out,'delivery-failure.png'),fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
