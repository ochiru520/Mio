/* Production-bundle interaction tests. Synthetic API/SSE; no personal data or model calls. */
const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base=process.env.MIO_UI_BASE || 'http://127.0.0.1:18461'
const output=process.env.MIO_UI_EVIDENCE || require('node:path').join(require('node:os').tmpdir(),'mio-user-defects-ui')
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
 const messages=[{id:1,role:'user',content:'请检查项目文件',created_at:'2026-10-08T12:00:00'},
  {id:2,role:'assistant',content:'已核对可用操作。',agent_task_id:'ui-task',agent_run_id:'ui-run',created_at:'2026-10-08T12:00:02',attachments:[{kind:'citation_manifest',citations:[]}]}]
 await ctx.addInitScript(()=>{
  localStorage.setItem('mio_agent_active_view','chat')
  localStorage.setItem('mio_agent_conversation_id','desktop_agent_ui')
  localStorage.setItem('mio_conversation_id','desktop_main_ui')
  localStorage.setItem('mio_app_preferences',JSON.stringify({theme:'xuan'}))
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
  else if(p==='/api/agent/messages')value=messages
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
  for(const width of [1112,1280,1920,600]){
   await agent.setViewportSize({width,height:850})
   if(!await agent.locator('.mio-agent-project-sidebar').count())await agent.getByRole('button',{name:'收起或展开项目和会话栏',exact:true}).click()
   await agent.locator('.mio-agent-project-sidebar').evaluate(el=>{
    if(el.scrollWidth>el.clientWidth+1)throw new Error(`Sidebar overflows: ${el.scrollWidth}/${el.clientWidth}`)
    const row=el.querySelector('.agent-conversation-item'),menu=row?.querySelector('button:last-child')
    if(menu && menu.getBoundingClientRect().right>el.getBoundingClientRect().right)throw new Error('Conversation menu is clipped')
   })
  }
  await agent.setViewportSize({width:1280,height:850})
  checks.push('侧栏在窄窗口和最大化宽度不横向溢出，菜单可见')
  assert.equal(await agent.locator('.message-attachments').count(),0)
  assert.equal(await agent.getByText('0 B',{exact:true}).count(),0);checks.push('内部元数据不显示为0B附件')
  await agent.getByLabel('当前对话的菜单').click()
  await agent.getByRole('menuitem',{name:'置顶'}).click()
  await agent.locator('[title="已置顶"]').waitFor()
  await agent.getByTitle('当前对话',{exact:true}).click({button:'right'})
  await agent.getByRole('menuitem',{name:'取消置顶'}).waitFor()
  await agent.keyboard.press('Escape')
  await agent.getByTitle('未读对话',{exact:true}).click()
  await agent.waitForFunction(()=>document.querySelectorAll('.agent-unread-dot').length===0)
  checks.push('三点和右键同一菜单、置顶、打开自动已读')
  await agent.getByTitle('当前对话',{exact:true}).click()
  await agent.getByRole('button',{name:'我的空间',exact:true}).click(); await agent.locator('.agent-personal-menu').getByRole('button',{name:'设置',exact:true}).click()
  await agent.getByRole('button',{name:'代码与电脑',exact:true}).click()
  await agent.getByLabel('执行权限项目').waitFor()
  assert.equal(await agent.getByLabel('执行权限项目').inputValue(),'b')
  await agent.getByRole('checkbox',{name:'允许提出代码修改与恢复'}).check()
  await agent.getByText('权限已保存，重新打开仍会保留。',{exact:true}).waitFor()
  assert.equal(configs.b.code.write_enabled,true);assert.equal(configs.b.code.read_enabled,true)
  await agent.getByText('测试与构建命令（可选）',{exact:true}).click()
  await agent.getByLabel('测试命令',{exact:true}).fill('synthetic-test')
  await agent.getByLabel('执行权限项目').selectOption('a')
  await agent.getByLabel('执行权限项目').selectOption('b')
  await agent.getByText('已恢复未保存的修改，请保存后生效。',{exact:true}).waitFor()
  assert.equal(await agent.getByLabel('测试命令',{exact:true}).inputValue(),'synthetic-test')
  await agent.getByRole('checkbox',{name:'启用此项目的电脑操作'}).check()
  await agent.getByText('权限已保存，重新打开仍会保留。',{exact:true}).waitFor()
  await agent.getByRole('button',{name:'返回应用',exact:true}).click()
  await agent.getByRole('button',{name:'我的空间',exact:true}).click(); await agent.locator('.agent-personal-menu').getByRole('button',{name:'设置',exact:true}).click()
  await agent.getByRole('button',{name:'代码与电脑',exact:true}).click()
  assert.equal(await agent.getByRole('checkbox',{name:'允许提出代码修改与恢复'}).isChecked(),true)
  assert.equal(await agent.getByRole('checkbox',{name:'启用此项目的电脑操作'}).isChecked(),true)
  checks.push('当前项目权限自动保存、关闭重开、项目切换草稿保留')
  await agent.getByLabel('本机项目目录',{exact:true}).fill('D:/test/b-new-root')
  await agent.getByRole('button',{name:'保存项目目录',exact:true}).click()
  await agent.getByText('项目目录已保存；原目录权限未迁移，请为新目录重新授权。',{exact:true}).waitFor()
  assert.equal(configs.b.code.root,'D:/test/b-new-root')
  assert.equal(configs.b.code.read_enabled,false)
  assert.equal(configs.b.code.write_enabled,false)
  assert.equal(configs.b.code.execute_enabled,false)
  assert.equal(await agent.getByRole('checkbox',{name:'允许提出代码修改与恢复'}).isChecked(),false)
  checks.push('更换项目代码目录会清除旧目录读取、编辑和运行授权')
  await agent.getByRole('button',{name:'模型与 API',exact:true}).click()
  await chat.goto(base+'/?conversation_id=desktop_main_ui')
  await chat.getByRole('navigation',{name:'主要导航'}).getByRole('button',{name:'对话',exact:true}).click()
  await chat.locator('.composer textarea').fill('保留此草稿')
  await agent.getByRole('button',{name:'供应商',exact:true}).click()
  await agent.getByRole('button',{name:'新增供应商',exact:true}).click()
  const dialog=agent.getByRole('dialog',{name:'供应商设置'})
  await dialog.getByLabel('供应商名称').fill('合成供应商')
  await dialog.getByLabel('API地址').fill('https://example.invalid/v1')
  await dialog.getByLabel('API Key',{exact:true}).fill('synthetic-not-a-secret')
  await dialog.getByLabel('模型ID').fill('synthetic-model')
  await dialog.getByRole('button',{name:'保存1个模型',exact:true}).click()
  await dialog.waitFor({state:'hidden'})
  await chat.locator('.model-picker-trigger').click()
  await chat.locator('.model-picker-popover').getByRole('button').filter({hasText:'模型'}).first().click()
  await chat.getByRole('button',{name:/共享测试模型/}).waitFor()
  assert.equal(await chat.locator('.composer textarea').inputValue(),'保留此草稿')
  checks.push('已开聊天窗口实时取得Agent新增API模型，保留草稿')
  await agent.getByRole('button',{name:'返回应用',exact:true}).click()
  const process=agent.getByText('查看分析与执行过程',{exact:true})
  await process.click()
  await agent.waitForFunction(()=>window.__streams.length>0)
  await agent.evaluate(()=>{
   const stream=window.__streams.at(-1)
   for(const event of [
    {id:1,event_type:'analysis_stream_started',payload:{stream_id:'s1'}},
    {id:2,event_type:'analysis_text_delta',payload:{stream_id:'s1',text:'**先检查权限。**\n确认项目目录后再修改。'}},
   ])stream.emit('progress',{...event,run_id:'ui-run'})
  })
  await agent.locator('.analysis-text strong').getByText('先检查权限。',{exact:true}).waitFor()
  await agent.evaluate(()=>{
   const stream=window.__streams.at(-1)
   for(const event of [
    {id:3,event_type:'analysis_stream_finished',payload:{stream_id:'s1',status:'generated'}},
    {id:4,event_type:'tool_batch_completed',payload:{tools:[{step_id:1,display_name:'读取项目文件',tool_name:'code_read_file',status:'completed',result:{path:'example.py'}}]}},
    {id:5,event_type:'analysis_stream_started',payload:{stream_id:'s2'}},
    {id:6,event_type:'analysis_text_delta',payload:{stream_id:'s2',text:'已经读取文件，准备整理结果。'}},
    {id:7,event_type:'analysis_stream_finished',payload:{stream_id:'s2',status:'generated'}},
   ])stream.emit('progress',{...event,run_id:'ui-run'})
   stream.emit('task_state',{status:'completed',snapshot:{}})
  })
  assert.deepEqual(await agent.locator('.task-progress > details.analysis-block, .task-progress > details.process-actions').evaluateAll(items=>items.map(item=>item.className)),['analysis-block','process-actions','analysis-block'])
  assert.equal(await agent.locator('.analysis-block[open]').count(),0)
  await agent.locator('.analysis-block summary').first().click()
  await agent.screenshot({path:output+'/analysis-and-actions.png'})
  checks.push('真实事件格式增量显示、Markdown灰色分析、完成折叠、分析操作顺序')
  await agent.getByLabel('当前对话的菜单').click()
  await agent.getByRole('menu').getByText('分支',{exact:true}).click()
  await agent.getByRole('menuitem',{name:'从当前对话创建分支',exact:true}).click()
  await agent.getByLabel('当前对话的分支的菜单').waitFor()
  await agent.getByLabel('当前对话的分支的菜单').click()
  await agent.getByRole('menu').getByText('项目',{exact:true}).click()
  await agent.getByRole('menuitem',{name:'不归入项目',exact:true}).click()
  assert.equal(links.projects.desktop_agent_branch,undefined)
  await agent.getByLabel('当前对话的分支的菜单').click()
  await agent.getByRole('menuitem',{name:'归档'}).click()
  await agent.getByRole('button',{name:'已归档对话',exact:true}).click()
  await agent.getByLabel('当前对话的分支的菜单').waitFor()
  checks.push('分支、移出项目、归档后可找回')
  await agent.getByRole('button',{name:'项目空间',exact:true}).click()
  await agent.getByText('这里还没有生成的成果',{exact:true}).waitFor()
  assert.equal(await agent.getByText(/上一页|下一页|第\s*\d+\s*页/).count(),0)
  artifactCount=65
  await agent.getByLabel('刷新成果').click()
  await agent.locator('[data-artifact-id="artifact-0"]').waitFor()
  await agent.locator('.agent-library-main').evaluate(el=>el.scrollTop=el.scrollHeight)
  await agent.locator('[data-artifact-id="artifact-64"]').waitFor()
  assert.equal(await agent.locator('[data-artifact-id]').count(),65)
  checks.push('空成果无页码、触底加载65项并保留首批40项')
  assert.deepEqual(errors,[])
  await fs.writeFile(output+'/report.json',JSON.stringify({checks,writes,pageErrors:errors,scope:'production frontend with synthetic API/SSE; real API separately verified by pytest'},null,2))
  console.log(JSON.stringify({checks,pageErrors:errors}))
 }catch(error){await agent.screenshot({path:output+'/failure.png'});await fs.writeFile(output+'/failure-dom.txt',await agent.locator('body').innerText());throw error}finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exit(1)})
