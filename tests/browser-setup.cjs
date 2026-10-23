/* In-app guides against an isolated controller. Creates only a local guided job; never logs in or starts cloud compute. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&!base.endsWith(':8765'),'Use an isolated localhost test server.');
 const out=path.resolve('.test-runs/browser');await fs.mkdir(out,{recursive:true});
 const response=await fetch(base+'/api/bootstrap'),token=response.headers.get('x-multigpu-token');
 const request=async(url,body,expected=200)=>{const r=await fetch(base+url,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-MultiGPU-Token':token},body:JSON.stringify(body)});const value=await r.json();assert.equal(r.status,expected,JSON.stringify(value));return value;};
 const spec={name:'In-app guide acceptance '+Date.now(),recipe:'starter',recipe_version:2,steps:20,providers:['colab'],free_only:true,guided:true,runtime_minutes:30};
 const plan=await request('/api/jobs/preview',spec),job=await request('/api/jobs',{...spec,preview_digest:plan.preview_digest},201);
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 let downloads=0;page.on('download',()=>downloads++);
 const jobGuide='/api/jobs/'+job.id+'/setup';
 const next=()=>page.locator('.setup-next[data-context="job"]');
 try{
  await page.goto(base+'/#job/'+job.id);
  await page.locator('#job-setup-guide .setup-navigation').waitFor();
  assert.equal(downloads,0,'Opening the guided job downloaded instructions automatically');
  assert((await page.locator('.manual-handoff').textContent()).includes('cannot launch, stop, or observe'));
  const guide=await request(jobGuide);assert.equal(guide.progress.current_step,'download');
  assert(await next().isDisabled(),'Unconfirmed manual step was allowed');
  const download=page.waitForEvent('download');await page.getByRole('button',{name:'Download training notebook',exact:true}).click();
  const notebook=await download;assert.equal(notebook.suggestedFilename(),'training.ipynb');await notebook.saveAs(path.join(out,'guide-training.ipynb'));
  const parsed=JSON.parse(await fs.readFile(path.join(out,'guide-training.ipynb'),'utf8'));assert.equal(parsed.nbformat,4);
  await page.locator('[data-setup-ack="job"]').check();await next().click();
  await page.waitForFunction(()=>document.querySelector('[data-context="job"][aria-current="step"]')?.dataset.step==='upload');
  await page.reload();await page.locator('[data-context="job"][data-step="upload"][aria-current="step"]').waitFor();
  assert.equal((await request(jobGuide)).progress.current_step,'upload');
  await request(jobGuide,{action:'advance',step_id:'upload',acknowledged:false,guide_revision:(await request(jobGuide)).revision},400);
  const still=await request(jobGuide);assert.equal(still.progress.current_step,'upload');assert(!still.progress.completed_steps.includes('upload'));
  await page.locator('[data-setup-ack="job"]').check();await next().click();
  await page.locator('[data-context="job"][data-step="runtime"][aria-current="step"]').waitFor();
  await page.locator('[data-setup-ack="job"]').check();await next().click();
  await page.locator('[data-context="job"][data-step="run"][aria-current="step"]').waitFor();
  const manual=await request(jobGuide),runtime=manual.steps.find(s=>s.id==='runtime');assert.equal(runtime.ready,false,'A manual acknowledgement falsely became provider readiness');assert(manual.progress.completed_steps.includes('runtime'));
  // Keep manual output recovery available to users who already ran an older notebook.
  await page.locator('#guided-import').evaluate(el=>el.open=true);
  await page.getByLabel('Downloaded output folder or ZIP').fill('C:/not-yet-imported/output.zip');
  await page.waitForTimeout(5300);
  assert.equal(await page.locator('#import-path').inputValue(),'C:/not-yet-imported/output.zip');assert(await page.locator('#import-path').evaluate(el=>el===document.activeElement),'Polling stole import focus');
  await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(out,'guided-job-desktop.png'),fullPage:true});
  // Provider setup can be read freely; native verification remains an actual check.
  await page.goto(base+'/#providers');
  await page.getByLabel('Search providers',{exact:true}).fill('Kaggle');await page.locator('.provider-title').click();
  await page.locator('#provider-setup-guide .setup-navigation').waitFor();
  await page.locator('[data-context="provider"][data-step="verify"]').click();
  await page.getByLabel('Kaggle username',{exact:true}).waitFor();
  const p=await request('/api/providers/kaggle/setup');if(!p.steps.find(s=>s.id==='verify').ready)assert(await page.locator('.setup-next[data-context="provider"]').isDisabled());
  await page.getByLabel('Kaggle username',{exact:true}).fill('unsaved-local-ui-test');await page.waitForTimeout(5300);
  assert.equal(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'unsaved-local-ui-test');assert(await page.getByLabel('Kaggle username',{exact:true}).evaluate(el=>el===document.activeElement));
  assert(await page.locator('.setup-next[data-context="provider"]').isDisabled(),'Unsaved config bypassed verification');
  await page.screenshot({path:path.join(out,'provider-guide-desktop.png'),fullPage:true});
  page.once('dialog',d=>d.accept());await page.getByRole('button',{name:'Close provider setup',exact:true}).click();
  await page.locator('.provider-title').click();await page.locator('[data-context="provider"][data-step="verify"][aria-current="step"]').waitFor();
  assert.notEqual(await page.getByLabel('Kaggle username',{exact:true}).inputValue(),'unsaved-local-ui-test');
  await page.locator('[data-context="provider"][data-step="signin"]').click();await page.locator('.command').first().waitFor();
  await page.keyboard.press('Escape');await page.locator('.provider-title').click();await page.locator('[data-context="provider"][data-step="signin"][aria-current="step"]').waitFor();
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Phone provider guide overflowed');
  await page.screenshot({path:path.join(out,'provider-guide-mobile.png'),fullPage:true});
  await page.keyboard.press('Escape');await page.goto(base+'/#job/'+job.id);
  await page.locator('#job-setup-guide .setup-navigation').waitFor();assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Phone job guide overflowed');
  await page.screenshot({path:path.join(out,'guided-job-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);console.log('In-app guide acceptance passed: explicit IPYNB download, saved Next/Back navigation, server-validated acknowledgements, no false readiness, no auto ZIP, manual handoff, accessible existing-output import, focus persistence, provider config protection, desktop/mobile.');
 }catch(e){await page.screenshot({path:path.join(out,'guide-failure.png'),fullPage:true});throw e;}
 finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
