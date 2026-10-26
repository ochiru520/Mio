/* Production-bundle interaction tests. Synthetic API/SSE; no personal data or model calls. */
const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base=process.env.MIO_UI_BASE || 'http://127.0.0.1:18461'
const output=process.env.MIO_UI_EVIDENCE || require('node:path').join(require('node:os').tmpdir(),'mio-conversation-layout-ui')
async function main(){
 await fs.mkdir(output,{recursive:true})
 const browser=await chromium.launch({channel:'msedge',headless:true})
 const ctx=await browser.newContext({viewport:{width:1280,height:850}})
 ctx.setDefaultTimeout(15000)
 const errors=[],checks=[],writes=[]
 const projects=[{id:'a',name:'首个项目',directory:'D:/test/a'},{id:'b',name:'当前项目',directory:'D:/test/b'}]
 const conversations=[{id:'desktop_main_ui',title:'聊天会话',kind:'main'},
  {id:'desktop_agent_ui',title:'当前对话',kind:'agent'},
  {id:'desktop_agent_other',title:'未读对话',kind:'agent',unread:true}]
 const links={projects:{desktop_agent_ui:'b'},modes:{desktop_agent_ui:'work'}}
 const models=[],configs={};let artifactCount=0
 for(const p of projects) configs[p.id]={code:{root:p.directory,read_enabled:false,write_enabled:false,execute_enabled:false,revision:0,verification:{}},desktop:{enabled:false,windows:[],executables:[],revision:0,available:true}}
 const originalMessages=[{id:1,role:'user',content:'请检查项目文件',created_at:'2026-10-08T12:00:00'},
  {id:2,role:'assistant',content:'已核对可用操作。',agent_task_id:'ui-task',agent_run_id:'ui-run',created_at:'2026-10-08T12:00:02',attachments:[{kind:'citation_manifest',citations:[]}]}]
 const messages=Array.from({length:135},(_,i)=>({id:i+1,role:i%2?'assistant':'user',content:'合成对话 '+(i+1)+'\n'+('这是用于阅读位置检查的长段落。'.repeat(10)),created_at:'2026-10-08T12:00:00',request_id:'request-'+i}));let denyNextSave=false
 await ctx.addInitScript(()=>{
  localStorage.setItem('mio_agent_active_view','chat')
  localStorage.setItem('mio_agent_conversation_id','desktop_agent_ui')
  localStorage.setItem('mio_conversation_id','desktop_main_ui')
  localStorage.setItem('mio_app_preferences',JSON.stringify({theme:'xuan',default_open_page:'chat'}))
  window.__streams=[]
  window.EventSource=class {
   constructor(url){this.url=url;this.handlers={};window.__streams.push(this);setTimeout(()=>this.onopen?.(),20)}
   addEventListener(n,cb){this.handlers[n]=cb}
   close(){this.closed=true}
   emit(n,data){if(!this.closed)this.handlers[n]?.({data:JSON.stringify(data)})}
  }
 })
 await ctx.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname
  if(!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p))return route.continue()
  let value={}
  if(p==='/api/onboarding/status')value={completed:true}
  else if(p==='/api/agent/bootstrap')value={onboarding:{completed:true},conversation_id:'desktop_main_ui',conversations,models,messages,diaries:[],settings:{},qq:{},model:models[0] || {},provider_presets:[]}
  else if(p==='/api/agent/models/catalog')value={models,provider_presets:[],hidden_model_providers:[]}
  else if(p==='/api/agent/providers' && req.method()==='POST'){
   const body=req.postDataJSON();models.push({id:'shared-api-model',model:body.models[0].model,display_name:'共享测试模型',provider_id:'ui-provider',provider_name:body.provider_name,provider:body.provider_name,is_custom:true})
   value={models}
  }
  else if(p==='/api/agent/conversations')value=conversations
  else if(p==='/api/agent/messages'){const before=Number(url.searchParams.get('before_id')||0);value=messages.filter(m=>!before||m.id<before).slice(-120)}
  else if(p==='/api/agent/context-usage')value={used_chars:2400,max_chars:18000,percent:13.3,used_tokens:1200,max_tokens:16000,token_percent:7.5}
  else if(p.endsWith('/state') && p.includes('/conversations/')){const id=p.split('/').at(-2);Object.assign(conversations.find(c=>c.id===id),req.postDataJSON());value=conversations.find(c=>c.id===id)}
  else if(p.endsWith('/fork')){value={id:'desktop_agent_branch',title:'当前对话的分支',kind:'agent',parent_id:'desktop_agent_ui'};conversations.push(value);links.projects[value.id]='b'}
  else if(p==='/api/workspace')value={projects,tasks:[]}
  else if(p==='/api/workspace/conversations')value=links
  else if(p.endsWith('/project')){const id=p.split('/').at(-2);if(req.method()==='DELETE')delete links.projects[id];else links.projects[id]=req.postDataJSON().project_id;value=links}
  else if(p==='/api/workspace/pending')value={items:[],total:0}
  else if(p==='/api/artifacts'){
   const offset=Number(url.searchParams.get('offset') || 0),limit=Number(url.searchParams.get('limit') || 40)
   value={artifacts:Array.from({length:artifactCount},(_,i)=>({id:'artifact-'+i,name:'成果'+i+'.txt',kind:'document',version:1,url:'/synthetic/'+i,integrity_status:'verified'})).slice(offset,offset+limit),has_more:offset+limit<artifactCount,next_offset:offset+limit}
  }
  else if(p==='/api/agent/work')value={tasks:[],limits:{}}
  else if(p.endsWith('/inputs'))value={inputs:[],has_more:false,next_version:0}
  else if(p.endsWith('/history'))value={changes:[],jobs:[],observations:[]}
  else if(/^\/api\/workspace\/execution\/projects\/[ab](\/(code|desktop))?$/.test(p)){
   const parts=p.split('/'),id=parts[5],kind=parts[6]
   if(req.method()==='PUT' && denyNextSave){denyNextSave=false;return route.fulfill({status:400,json:{detail:'合成保存失败'}})}
   if(req.method()==='PUT') {configs[id][kind]={...configs[id][kind],...req.postDataJSON(),revision:configs[id][kind].revision+1};writes.push({id,kind});value=configs[id][kind]}
   else value=configs[id]
  }
  else if(p.includes('/profile'))value={profile:{},settings:{}}
  else if(p==='/api/companion/status')value={pet:{settings:{}},screen:{},settings:{}}
  else if(p.includes('startup-greeting'))value={enabled:false}
  else if(/^\/(diaries|reviews|weekly)$/.test(p))value=[]
  await route.fulfill({json:value})
 })
 const agent=await ctx.newPage();agent.on('pageerror',e=>errors.push(e.message))
 const chat=await ctx.newPage();chat.on('pageerror',e=>errors.push(e.message))
 try{
  await agent.goto(base+'/?workspace=agent&conversation_id=desktop_agent_ui')
  await agent.getByLabel('当前对话的菜单').waitFor()
  await agent.setViewportSize({width:1280,height:720})
  const scroll=agent.locator('.message-scroll')
  await agent.waitForTimeout(400)
  console.log('INITIAL_SCROLL',await scroll.evaluate(el=>({top:el.scrollTop,height:el.scrollHeight,client:el.clientHeight})));
  assert.ok(await scroll.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop<50),'Initially open at latest message')
  const sizes=await agent.locator('.composer-wrap').evaluate(el=>({composer:el.getBoundingClientRect().height,viewport:innerHeight}))
  assert.ok(sizes.composer<150,JSON.stringify(sizes))
  assert.ok(sizes.composer/sizes.viewport<.22)
  assert.equal(await agent.locator('.context-usage-control').isVisible(),true)
  await agent.locator('.composer textarea').fill(Array(20).fill('长文本输入').join('\n'))
  assert.ok(await agent.locator('.composer textarea').evaluate(el=>el.clientHeight<=Math.min(140,innerHeight*.18)+2))
  await agent.locator('.composer textarea').fill('')
  checks.push('输入区默认低于150px、长输入有高度上限，上下文用量可见')
  await chat.goto(base+'/?conversation_id=desktop_main_ui')
  await chat.locator('.composer-input').fill('聊天窗口独立草稿')
  assert.equal(await agent.locator('.composer textarea').inputValue(),'')
  await agent.locator('.composer textarea').fill('Agent 窗口独立草稿')
  assert.equal(await chat.locator('.composer-input').inputValue(),'聊天窗口独立草稿')
  await agent.locator('.composer textarea').fill('')
  checks.push('聊天与Agent窗口的领域状态和输入草稿独立')
  await scroll.evaluate(el=>el.scrollTop=el.scrollHeight*.4)
  await agent.waitForTimeout(100)
  const before=await scroll.evaluate(el=>el.scrollTop)
  messages.push({id:136,role:'assistant',content:'后台追加的新消息',request_id:'new-message',created_at:'2026-10-08T12:00:01'})
  await agent.waitForFunction(()=>document.querySelector('[data-message-anchor="assistant-136"]'),{},{timeout:12000})
  assert.ok(Math.abs(await scroll.evaluate(el=>el.scrollTop)-before)<3,'Polling must preserve reading position')
  const anchor=await scroll.evaluate(el=>[...el.querySelectorAll('[data-message-anchor]')].find(x=>x.getBoundingClientRect().bottom>el.getBoundingClientRect().top+4).dataset.messageAnchor)
  await agent.getByRole('button',{name:'我的空间',exact:true}).click()
  await agent.locator('.agent-personal-menu').getByRole('button',{name:'设置',exact:true}).click()
  await agent.getByRole('button',{name:'执行权限',exact:true}).click()
  await agent.getByLabel('执行权限项目').waitFor()
  await agent.getByRole('button',{name:'返回应用',exact:true}).click()
  await agent.waitForTimeout(400)
  const anchorAfter=await scroll.evaluate(el=>[...el.querySelectorAll('[data-message-anchor]')].find(x=>x.getBoundingClientRect().bottom>el.getBoundingClientRect().top+4).dataset.messageAnchor)
  assert.equal(anchorAfter,anchor,'Settings return must restore same visible message')
  assert.ok(Math.abs(await scroll.evaluate(el=>el.scrollTop)-before)<4)
  await scroll.evaluate(el=>el.scrollTop=0)
  await agent.getByRole('button',{name:'加载更早对话',exact:true}).click()
  await agent.locator('[data-message-anchor="user-1"]').waitFor()
  assert.equal(await agent.locator('[data-message-anchor]').count(),136)
  await agent.waitForTimeout(5500)
  assert.equal(await agent.locator('[data-message-anchor]').count(),136,'Polling must retain loaded older messages')
  checks.push('后台追加、设置返回保持阅读锚点；加载更早消息后刷新不丢历史')
  configs.b.code.root='';configs.b.code.read_enabled=false;configs.b.code.write_enabled=false;configs.b.code.execute_enabled=false
  await agent.evaluate(()=>sessionStorage.setItem('mio_execution_draft_b',JSON.stringify({config:{code:{root:'',read_enabled:true,write_enabled:true,execute_enabled:true,revision:0},desktop:{enabled:false,windows:[],executables:[],revision:0}},commands:{test:'',build:'',lint:'',typecheck:''},executableText:''})))
  await agent.getByRole('button',{name:'我的空间',exact:true}).click()
  await agent.locator('.agent-personal-menu').getByRole('button',{name:'设置',exact:true}).click()
  await agent.getByRole('button',{name:'执行权限',exact:true}).click()
  await agent.getByText('高级：项目能力授权',{exact:true}).click()
  const write=agent.getByRole('checkbox',{name:'允许提出代码修改与恢复',exact:true})
  await write.waitFor()
  assert.equal(await write.isChecked(),false)
  assert.equal(await write.isDisabled(),true)
  await agent.getByLabel('本机项目目录',{exact:true}).fill('D:/test/b')
  await agent.getByRole('button',{name:'保存项目目录',exact:true}).click()
  await agent.getByText('项目目录已保存；原目录权限未迁移，请为新目录重新授权。',{exact:true}).waitFor()
  assert.equal(configs.b.code.root,'D:/test/b')
  assert.equal(await write.isDisabled(),false)
  denyNextSave=true
  await write.check()
  await agent.getByText('未保存：合成保存失败',{exact:true}).waitFor()
  assert.equal(await write.isChecked(),false)
  assert.equal(await agent.getByRole('checkbox',{name:'读取与搜索项目文本',exact:true}).isChecked(),false)
  await write.check()
  await agent.getByText('权限已保存，重新打开仍会保留。',{exact:true}).waitFor()
  assert.equal(configs.b.code.read_enabled,true)
  assert.equal(configs.b.code.write_enabled,true)
  await agent.getByText('测试与构建命令（可选）',{exact:true}).click()
  await agent.getByLabel('测试命令',{exact:true}).fill('synthetic-test')
  await agent.getByRole('button',{name:'保存代码权限',exact:true}).click()
  await agent.getByText('权限已保存，重新打开仍会保留。',{exact:true}).waitFor()
  await agent.getByText('测试与构建命令（可选）',{exact:true}).click()
  await agent.locator('.mio-settings-content').evaluate(el=>el.scrollTop=0)
  await agent.screenshot({path:output+'/execution-settings.png'})
  await agent.getByRole('button',{name:'返回应用',exact:true}).click()
  await agent.getByRole('button',{name:'我的空间',exact:true}).click()
  await agent.locator('.agent-personal-menu').getByRole('button',{name:'设置',exact:true}).click()
  await agent.getByRole('button',{name:'执行权限',exact:true}).click()
  await agent.getByText('高级：项目能力授权',{exact:true}).click()
  assert.equal(await write.isChecked(),true)
  checks.push('空目录禁用权限，旧草稿不伪装授权，保存失败回退开关，成功保存跨重开保持')
  await agent.getByRole('button',{name:'返回应用',exact:true}).click()
  await agent.waitForTimeout(400)
  await scroll.evaluate(el=>el.scrollTop=el.scrollHeight)
  await agent.waitForTimeout(100)
  assert.ok(await scroll.evaluate(el=>el.scrollHeight-el.clientHeight-el.scrollTop<50),'Manual scroll can reach latest')
  await agent.screenshot({path:output+'/compact-composer.png'})
  assert.deepEqual(errors,[])
  await fs.writeFile(output+'/report.json',JSON.stringify({checks,pageErrors:errors,scope:'production dist, synthetic API; no real model or permissions changed'},null,2))
  console.log(JSON.stringify({checks,pageErrors:errors}))
 }catch(error){await agent.screenshot({path:output+'/failure.png'});await fs.writeFile(output+'/failure-dom.txt',await agent.locator('body').innerText());throw error}finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exit(1)})
