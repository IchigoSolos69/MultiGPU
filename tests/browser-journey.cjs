/* Journey regressions use fixture APIs on an isolated controller. No provider or account calls. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.MULTIGPU_TEST_URL;
 assert(base&&/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base)&&!base.endsWith(':8765'),'Use an isolated controller.');
 const metadata=await(await fetch(base+'/api/bootstrap')).json();
 const out=path.resolve('.test-runs/browser');await fs.mkdir(out,{recursive:true});
 const stamp=new Date().toISOString(),id='f'.repeat(32);
 let draft={wizard_step:1,primary:'local',fallbacks:[],dataset_mode:'sample',spec:{name:'Restored fixture',recipe:'starter',providers:['local'],steps:100,output_dir:'C:/fixture/models'}};
 let task={id:'fixture-task',profile:'image',status:'running',cancellable:true,logs:['Fixture dependency setup'],created_at:stamp};
 let job={id,name:'Focused run fixture',provider_id:'local',status:'running',phase:'training',execution_mode:'automatic',step:2,total_steps:100,loss:.7,created_at:stamp,updated_at:stamp,logs:'First fixture log\nSecond fixture log',spec:{recipe:'starter',steps:100,providers:['local'],output_dir:'C:/fixture/models',free_only:true,runtime_minutes:30,attempt_id:'fixture-attempt'},metrics:[{step:1,loss:.8},{step:2,loss:.7}],events:[],checkpoint:{step:2},allowed_actions:['pause','export'],inference:{supported:false,reason:'Export the model before prediction.'},attention_required:false};
 const bootstrap=()=>({...metadata,jobs:[job],environment_tasks:[task],providers:metadata.providers.map(p=>p.id==='kaggle'?{...p,connection:{account_verified:true,ready:false,checked_at:stamp,quota:{remaining_hours:0,verified:true}}}:p)});
 let bootReads=0,draftReads=0,launches=0,cancelled=0,signin=0,resolution=null,relocation=null,predicted=null,planErrors=false;
 let guide={provider_id:'kaggle',title:'Fixture setup',steps:[{id:'signin',title:'Native sign-in',description:'Finish native sign-in, then verify.',commands:['fixture native login'],actions:[{type:'provider-signin',label:'Sign in with Kaggle',provider_id:'kaggle'}],completion:'acknowledgement',ready:false,can_complete:true}],progress:{current_step:'signin',completed_steps:[]}};
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{
  const request=route.request(),url=new URL(request.url()),api=url.pathname,body=request.method()==='POST'?request.postDataJSON():null;
  const send=(value,status=200)=>route.fulfill({status,json:value,headers:{'X-MultiGPU-Token':'fixture-token'}});
  if(api==='/api/bootstrap'){bootReads++;return bootReads===1?send({error:'Fixture controller offline'},503):send(bootstrap());}
  if(api==='/api/draft'){if(body)draft=body.draft;else draftReads++;return send({draft});}
  if(api==='/api/environment/tasks')return send({tasks:[task]});
  if(api==='/api/environment/tasks/'+task.id+'/cancel'){cancelled++;task={...task,status:'cancelled',cancellable:false};return send(task);}
  if(api==='/api/desktop/provider-signin'){signin++;assert.equal(body.provider_id,'kaggle');return send({started:true,message:'Fixture native sign-in started'});}
  if(api==='/api/providers/kaggle/setup'){if(body)guide.progress.current_step=body.step_id;return send(guide);}
  if(api==='/api/datasets/inspect')return send({errors:[],warnings:[],fingerprint:'fixture-inputs',dataset:{kind:body.recipe==='custom'?'custom':'tabular',rows:20,train_rows:16,validation_rows:4,classes:[0,1]}});
  if(api==='/api/jobs/preview')return send({eligible:false,preparation_eligible:!planErrors,execution_mode:planErrors?null:'guided',selected_provider:'colab',allowed_actions:planErrors?[]:['prepare_guided'],decisions:[{provider_id:'colab',eligible:false,preparation_eligible:!planErrors,mode:'guided',reason:planErrors?'Invalid input':'Manual notebook handoff is available.'}],warnings:[],preview_digest:'fixture-preview',preflight:{errors:planErrors?['Fixture export folder is not writable']:[],actions:[{type:'wizard',step:2,label:'Choose another export folder'}]}});
  if(api==='/api/jobs'){launches++;return send({error:'Unexpected fixture launch'},400);}
  if(api==='/api/jobs/'+id)return send(job);
  if(api==='/api/jobs/'+id+'/predict'){predicted=body;return send({prediction:0,classes:[0,1],probabilities:[.8,.2]});}
  if(api==='/api/jobs/'+id+'/resolve-submission'){resolution=body;if(body.recovery_token!==job.recovery_token)return send({error:'Attempt changed. Refresh and confirm again.'},400);job={...job,allowed_actions:['archive','relocate-inputs'],submission_resolution:{source:'user_confirmed',outcome:body.outcome,attempt_id:job.spec.attempt_id},attention_required:true};return send(job);}
  if(api==='/api/jobs/'+id+'/relocate-inputs'){relocation=body;job={...job,spec:{...job.spec,...body}};return send(job);}
  if(api==='/api/settings')return send({...metadata.settings,...body});
  return send({error:'Unmocked fixture endpoint '+api},404);
 });
 try{
  // Reconnect restores the saved route and draft, and creates exactly one polling timer.
  await page.goto(base+'/?desktop=1#new');await page.getByRole('button',{name:'Reconnect',exact:true}).click();
  await page.getByRole('heading',{name:'Name your run',exact:true}).waitFor();
  assert.equal(await page.getByLabel('Run name',{exact:true}).inputValue(),'Restored fixture');assert.equal(draftReads,1);
  const timer=await page.evaluate(()=>state.pollTimer);await page.getByRole('button',{name:'Refresh workspace',exact:true}).click();
  assert.equal(await page.evaluate(()=>state.pollTimer),timer);assert.equal(draftReads,1);
  await page.goto(base+'/?desktop=1#providers');
  const kaggle=page.locator('.provider-table tr').filter({has:page.locator('[data-action="provider"][data-id="kaggle"]')});
  assert((await kaggle.textContent()).includes('Signed in · allowance unavailable'));assert((await kaggle.textContent()).includes('Live account qualification pending'));
  await page.locator('.provider-title[data-id="kaggle"]').click();await page.getByRole('button',{name:'Sign in with Kaggle',exact:true}).click();assert.equal(signin,1);
  assert.equal(await page.getByRole('button',{name:'Copy',exact:true}).count(),1);await page.keyboard.press('Escape');
  // Persisted preparation can be discovered after reload and cancelled without starting another task.
  await page.goto(base+'/?desktop=1#settings');await page.getByRole('button',{name:'Cancel preparation',exact:true}).click();
  await page.getByRole('heading',{name:'image setup · cancelled',exact:true}).waitFor();assert.equal(cancelled,1);
  await page.reload();await page.getByRole('heading',{name:'image setup · cancelled',exact:true}).waitFor();
  // A polling transition updates status, actions and export results while preserving an edited field.
  await page.goto(base+'/#job/'+id);await page.getByLabel('Export destination',{exact:true}).fill('C:/fixture/edited-destination');
  job={...job,status:'completed',phase:'exporting',step:100,export_path:'C:/fixture/verified-model',allowed_actions:['export','open-export','predict','archive'],inference:{supported:true,kind:'tabular',features:['height','weight'],classes:[0,1]}};
  await page.waitForTimeout(5300);assert.equal(await page.locator('#job-status').textContent(),'Completed');
  assert.equal(await page.getByRole('button',{name:'Save & pause',exact:true}).count(),0);
  assert.equal(await page.getByLabel('Export destination',{exact:true}).inputValue(),'C:/fixture/edited-destination');assert(await page.getByLabel('Export destination',{exact:true}).evaluate(e=>e===document.activeElement));
  await page.getByRole('button',{name:'Download model files',exact:true}).waitFor();
  await page.getByLabel('height',{exact:true}).fill('165');await page.getByLabel('weight',{exact:true}).fill('62');await page.getByRole('button',{name:'Run prediction',exact:true}).click();assert.deepEqual(predicted,{features:{height:165,weight:62}});
  // Selecting logs leaves their text and selection intact while status continues to update.
  await page.locator('#raw-logs').evaluate(e=>e.open=true);
  await page.locator('#job-logs').evaluate(e=>{e.focus();const range=document.createRange();range.selectNodeContents(e);const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);});
  const selected=await page.evaluate(()=>getSelection().toString());job={...job,status:'error',logs:job.logs+'\nNew fixture log',allowed_actions:['export'],attention_required:true,attention_reasons:['Local export needs attention']};
  await page.evaluate(()=>refresh());assert.equal(await page.evaluate(()=>getSelection().toString()),selected);assert.equal(await page.locator('#job-status').textContent(),'Failed');
  await page.evaluate(()=>{getSelection().removeAllRanges();document.activeElement.blur();});
  // Uncertain launch is resolved explicitly against a saved attempt; no retry/new launch occurs.
  job={...job,status:'interrupted',provider_id:'kaggle',submission_status:'unknown',recovery_token:'fixture-token',submission_intent:{kernel:'fixture-owner/multigpu-attempt',identity:{username:'fixture-owner'}},spec:{...job.spec,providers:['kaggle'],dataset_path:'C:/fixture/original.csv',script_path:'C:/fixture/original.py'},allowed_actions:['resolve-submission','reconcile'],inference:{supported:false,reason:'No adapter for this custom model.'}};
  await page.reload();await page.getByText('Provider launch not confirmed',{exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:'Repeat settings',exact:true}).count(),0);
  await page.locator('#submission-resolution').evaluate(e=>e.open=true);await page.evaluate(()=>refresh());assert(await page.locator('#submission-resolution').evaluate(e=>e.open),'Polling collapsed recovery instructions');assert.equal(await page.getByRole('link',{name:'Open saved provider attempt'}).getAttribute('href'),'https://www.kaggle.com/code/fixture-owner/multigpu-attempt');
  assert((await page.locator('#submission-resolution').textContent()).includes('fixture-owner'));
  await page.getByLabel('What did you confirm?',{exact:true}).selectOption('stopped');await page.getByRole('checkbox',{name:'I checked the provider console'}).check();await page.getByLabel('Recovery note (optional)',{exact:true}).fill('Reviewed saved attempt');job={...job,recovery_token:'fixture-token-2'};await page.evaluate(()=>refresh());await page.getByRole('button',{name:'Save resolution',exact:true}).click();assert.equal(resolution.recovery_token,'fixture-token','Focused form silently adopted a different attempt token');await page.getByText('Attempt changed. Refresh and confirm again.',{exact:true}).waitFor();await page.getByRole('button',{name:'Refresh workspace',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#resolution-form')?.dataset.recoveryToken==='fixture-token-2');assert(!(await page.getByRole('checkbox',{name:'I checked the provider console'}).isChecked()),'Confirmation survived a changed attempt token');await page.getByRole('checkbox',{name:'I checked the provider console'}).check();await page.getByRole('button',{name:'Save resolution',exact:true}).click();
  assert.deepEqual({token:resolution.recovery_token,outcome:resolution.outcome,confirmed:resolution.confirmed},{token:'fixture-token-2',outcome:'stopped',confirmed:true});
  await page.getByText('Provider attempt resolved by you',{exact:true}).waitFor();await page.getByRole('button',{name:'Repeat settings',exact:true}).waitFor();assert.equal(launches,0);
  await page.locator('#input-relocation').evaluate(e=>e.open=true);await page.getByLabel('Dataset location',{exact:true}).fill('C:/fixture/moved.csv');await page.getByRole('button',{name:'Verify and update paths',exact:true}).click();assert.equal(relocation.dataset_path,'C:/fixture/moved.csv');
  assert.equal(await page.locator('#prediction-form').count(),0,'Unsupported custom inference displayed a form');
  await page.goto(base+'/#jobs');await page.getByLabel('Filter training status',{exact:true}).selectOption('error');assert.equal(await page.locator('.job-row').count(),1);
  // Equal custom entry, optional data path, trust invalidation, accurate review and blocked preflight.
  draft={wizard_step:0,primary:'colab',fallbacks:[],dataset_mode:'own',spec:{recipe:'custom',name:'Custom fixture',script_path:'C:/fixture/train.py',dataset_path:'C:/fixture/data',providers:['colab'],steps:100,output_dir:'C:/fixture/models'}};
  await page.goto(base+'/?fixture=custom#new');await page.getByRole('radio',{name:'Custom Python script'}).waitFor();
  assert.equal(await page.getByRole('radio').count(),3);await page.getByRole('checkbox',{name:'I trust this script',exact:false}).check();
  await page.getByRole('button',{name:'Check script and inputs',exact:true}).click();await page.getByRole('heading',{name:'Dataset inspection',exact:true}).waitFor();
  await page.getByLabel('Custom dataset (optional)',{exact:true}).fill('C:/fixture/new-data');assert(!(await page.getByRole('checkbox',{name:'I trust this script',exact:false}).isChecked()));assert((await page.locator('#dataset-report').textContent()).includes('Inputs changed'));
  await page.getByRole('checkbox',{name:'I trust this script',exact:false}).check();await page.getByRole('button',{name:'Continue',exact:true}).click();
  await page.getByRole('heading',{name:'Name your run',exact:true}).waitFor();await page.getByRole('button',{name:'Continue',exact:true}).click();await page.getByRole('heading',{name:'Your primary compute resource',exact:true}).waitFor();await page.getByRole('button',{name:'Continue',exact:true}).click();
  await page.getByRole('heading',{name:'Review this training target',exact:true}).waitFor();assert((await page.locator('.review-list').textContent()).includes('C:/fixture/train.py'));assert((await page.locator('.review-list').textContent()).includes('C:/fixture/new-data'));
  planErrors=true;await page.getByRole('button',{name:'Preview training plan'}).click();await page.getByRole('heading',{name:'Resolve preflight issues',exact:true}).waitFor();assert.equal(await page.locator('[data-action="guided"]').count(),0);await page.getByRole('button',{name:'Choose another export folder',exact:true}).click();await page.getByRole('heading',{name:'Your primary compute resource',exact:true}).waitFor();
  await page.getByRole('button',{name:'Continue',exact:true}).click();planErrors=false;await page.getByRole('button',{name:'Preview training plan'}).click();await page.getByRole('heading',{name:'Manual notebook setup is available',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Set up Google Colab training',exact:true}).count(),1);assert.equal(launches,0);
  for(const width of [1440,768,390]){await page.setViewportSize({width,height:width===390?844:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Wizard overflow at '+width);await page.evaluate(()=>window.scrollTo({top:0,behavior:'instant'}));await page.screenshot({path:path.join(out,'journey-review-'+width+'.png'),fullPage:true});}
  // Torch-visible local GPU selection remains within the open advanced settings.
  metadata.hardware={...metadata.hardware,cuda_available:true,gpus:[{name:'Fixture CUDA 0'},{name:'Fixture CUDA 1'}]};
  draft={wizard_step:1,primary:'local',fallbacks:[],dataset_mode:'sample',spec:{recipe:'pytorch',name:'GPU choice fixture',providers:['local'],steps:100,device:'auto'}};
  await page.goto(base+'/?fixture=gpus#new');await page.getByText('Advanced training settings',{exact:true}).click();
  await page.getByLabel('GPU on this computer',{exact:true}).selectOption('1');assert.equal(await page.evaluate(()=>trainingSpec().gpu_index),1);
  await page.getByLabel('Device preference',{exact:true}).selectOption('cpu');assert.equal(await page.getByLabel('GPU on this computer',{exact:true}).count(),0);
  await page.getByLabel('Device preference',{exact:true}).selectOption('cuda');assert.equal(await page.getByLabel('GPU on this computer',{exact:true}).inputValue(),'1');
  // A possibly running Lightning Studio requires stopped confirmation, never an absent-execution shortcut.
  job={...job,provider_id:'lightning',submission_status:'not_started',submission_resolution:null,cleanup_status:'failed',allowed_actions:['resolve-submission'],recovery_token:'studio-token',submission_intent:{identity:{owner:'fixture-owner',studio:'dedicated-fixture'}},spec:{...job.spec,providers:['lightning']}};
  await page.goto(base+'/#job/'+id);await page.locator('#submission-resolution').evaluate(e=>e.open=true);
  assert.equal(await page.locator('#resolution-outcome option').count(),1);assert.equal(await page.getByLabel('What did you confirm?',{exact:true}).inputValue(),'stopped');
  assert((await page.locator('#submission-resolution').textContent()).includes('may still be running and billing'));
  await page.getByRole('checkbox',{name:'I checked Lightning and confirm that the dedicated Studio is stopped',exact:false}).check();
  await page.getByRole('button',{name:'Save resolution',exact:true}).click();assert.equal(resolution.outcome,'stopped');assert.equal(resolution.confirmed,true);assert.equal(launches,0);
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'journey-proof.json'),JSON.stringify({at:new Date().toISOString(),base,result:'passed',checks:['reconnect-once','draft-restoration','persisted-setup-cancellation','native-signin-action','account-quota-separation','focused-completion-export','log-selection','named-inference','snapshot-bound-manual-resolution','input-relocation','attention-history','custom-data-and-trust','preflight-gates','local-gpu-index','studio-stop-confirmation'],viewports:[1440,768,390]},null,2));console.log('Journey contracts passed: reconnect initialization, persistent cancellable setup, native sign-in action, account/quota separation, focused live completion and export, selected logs, named inference, explicit attempt resolution, moved inputs, attention history, script entry and trust reset, accurate preflight actions, responsive review.');
 }catch(e){await page.screenshot({path:path.join(out,'journey-failure.png'),fullPage:true});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
