/* Real Vue controls with isolated fixture transport. No user data or paid API. */
const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const os=require('node:os'),path=require('node:path')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base=process.env.MIO_UI_BASE || 'http://127.0.0.1:18476'
const output=process.env.MIO_UI_EVIDENCE || path.join(os.tmpdir(),'mio-agent-experience-ui-20261026')
const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')
async function main(){
await fs.mkdir(output,{recursive:true})
const browser=await chromium.launch({channel:'msedge',headless:true})
const context=await browser.newContext({viewport:{width:1280,height:900}})
const checks=[],errors=[],writes=[],controls=[],stdin=[],sent=[]
let integratedTask=false,conflict=false,referenceChanged=false,rejectChat=false,control={revision:1,visible:false,active:false,owner:'user'},draft={answers:{},flagged_item_ids:[],active_item_id:'q1',revision:0},notifications=[]
const session=()=>({id:'s',title:'学习验证',goal:'草稿隔离',draft,items:[{id:'q1',prompt:'第一题'},{id:'q2',prompt:'第二题'}],progress:{answered:0,total:2}})
await context.route('**/api/**',async route=>{
const request=route.request(),url=new URL(request.url()),p=url.pathname,method=request.method();let value={}
if(p==='/api/workspace/projects/p/learning/s')value=session()
else if(p.endsWith('/draft')){const body=request.postDataJSON();if(conflict)return route.fulfill({status:409,json:{detail:'草稿已在别处修改，未覆盖当前答案'}});assert.equal(body.expected_revision,draft.revision);draft={...body,revision:draft.revision+1};writes.push(draft);value=draft}
else if(p.endsWith('/answer'))value={score:1,feedback:'已提交'}
else if(p.endsWith('/browser/pages'))value={pages:[{page_id:'page',url:'https://example.test',title:'受控页面',control}]}
else if(p.endsWith('/control')){const body=request.postDataJSON();controls.push(body);if(body.expected_revision!==control.revision)return route.fulfill({status:409,json:{detail:'页面控制版本改变'}});const changed=['visible','active','owner'].some(key=>body[key]!==control[key]);control={...body,revision:control.revision+(changed?1:0)};value={control}}
else if(p.endsWith('/frame'))return route.fulfill({contentType:'image/png',body:png})
else if(p.endsWith('/select'))value={page_id:'page',url:'https://example.test',selector:'#submit',fingerprint:'fp',control_revision:control.revision,element:{tag:'button',name:'提交'},expires_in_seconds:120}
else if(p.endsWith('/user-action'))value={input_sent:true,control}
else if(p.endsWith('/inputs'))value={inputs:[{id:1,version:1,revision:1,content:'新增约束',state:'received'}],has_more:false}
else if(p.endsWith('/inputs/1'))value={state:method==='DELETE'?'withdrawn':'received',revision:2}
else if(p.endsWith('/stream')){const events=[{id:0,event_type:'tool_batch_completed',payload:{tools:[{tool_name:'browser_open',status:'completed',result:{page_id:'page'}}]}},{id:1,event_type:'response_stream_started',payload:{stream_id:'s'}},{id:2,event_type:'response_text_delta',payload:{stream_id:'s',text:'尚未保存正文'}},{id:3,event_type:'response_stream_finished',payload:{stream_id:'s',status:'generated'}},{id:4,event_type:'final_response_completed',payload:{reply:'已保存最终正文',persistence_state:'committed'}}];return route.fulfill({contentType:'text/event-stream',body:events.map((event,index)=>{event.id=index+1;return `event: progress\ndata: ${JSON.stringify(event)}\n\n`}).join('')+'event: task_state\ndata: {"status":"completed"}\n\nevent: idle\ndata: {}\n\n'})}
else if(p==='/api/workspace/execution/projects/p')value={code:{read_enabled:true,execute_enabled:true},desktop:{}}
else if(p.endsWith('/history'))value={changes:[],jobs:[{id:'j',task_id:'t',command:'python',status:'running'}],observations:[]}
else if(p.endsWith('/changes/change'))value={files:[{path:'a.py',diff:'-old\n+new'},{path:'b.py',diff:'+second file'}]}
else if(p.endsWith('/jobs/j'))value={id:'j',task_id:'t',kind:'terminal',command:'python',status:'running',interactive:true,interaction_state:'waiting_input',stdout:'>>> ',stderr:'',has_more:false,next_offset:12000}
else if(p.endsWith('/terminal/j/input')){stdin.push(request.postDataJSON());value={input_sent:true}}
else if(p==='/api/workspace/references/candidates')value={candidates:[{kind:'file',target:'D:/fixture/notes.txt',name:'notes.txt'}]}
else if(p==='/api/workspace/references/resolve'){if(referenceChanged)value={blocked:[{reason:'引用来源已变化，请重新选择'}],resolved:[]};else value={resolved:[{kind:'file',target:'D:/fixture/notes.txt',name:'notes.txt',version:'sha',excerpt:'已授权资料'}],blocked:[]}}
else if(p==='/api/agent/bootstrap')value={onboarding:{completed:true},conversation_id:'desktop_agent_test',conversations:[{id:'desktop_agent_test',kind:'agent',title:'集成测试'}],models:[{id:'m',display_name:'本地测试模型',model:'fixture',provider_id:'p',provider_name:'本地'}],messages:[],diaries:[],settings:{},qq:{},model:{},provider_presets:[]}
else if(p==='/api/onboarding/status')value={completed:true}
else if(p==='/api/agent/conversations')value=[{id:'desktop_agent_test',kind:'agent',title:'集成测试'}]
else if(p==='/api/agent/models/catalog')value={models:[{id:'m',display_name:'本地测试模型',model:'fixture',provider_id:'p',provider_name:'本地'}],provider_presets:[]}
else if(p==='/api/agent/messages' && url.searchParams.has('before_id'))value=[{id:1,role:'user',content:'更早的问题'}]
else if(p==='/api/agent/messages')value=[{id:2,role:'user',content:'集成问题',source:'desktop',created_at:'2026-10-26T09:00:00'},{id:3,role:'assistant',content:'集成回答',prompt_tokens:100,cached_prompt_tokens:0,cached_tokens_reporting:'reported',created_at:'2026-10-26T09:00:01'}]
else if(p.endsWith('/turn-index'))value={turns:[{id:1,role:'user',content:'更早的问题'},{id:2,role:'user',content:'集成问题'}],has_more:false}
else if(p==='/api/workspace')value={projects:[],tasks:[]}
else if(p==='/api/workspace/conversations')value={projects:{},modes:{desktop_agent_test:'work'}}
else if(p==='/api/workspace/pending')value={items:[],total:0}
else if(p==='/api/agent/work')value={tasks:integratedTask?[{id:'t',conversation_id:'desktop_agent_test',status:'running',snapshot:{},observations:[]}]:[],limits:{}}
else if(p==='/api/agent/work/done')value={task:{id:'done',conversation_id:'c',status:'completed',snapshot:{goal:'已完成任务仍可定位'},observations:[]}}
else if(p.endsWith('/avatar') || p.endsWith('/user-avatar'))return route.fulfill({contentType:'image/png',body:png})
else if(p.endsWith('/notifications'))value={notifications}
else if(p==='/api/artifacts')value={artifacts:[]}
else if(p.includes('/profile'))value={profile:{},settings:{}}
else if(p==='/api/companion/status')value={pet:{settings:{}},screen:{},settings:{}}
else if(p.includes('startup-greeting'))value={enabled:false}
else if(p==='/api/agent/chat'){sent.push(request.postDataJSON());if(rejectChat)return route.fulfill({status:400,json:{detail:'引用资料已经变化，请重新选择'}});value={reply:'完成',replies:['完成']}}
else if(p.includes('/context-usage'))value={used_tokens:200,max_tokens:272000}
await route.fulfill({json:value})
})
const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message))
try{
await page.goto(base+'/scripts/agent-experience-preview.html?mode=learning')
await page.getByLabel('本题草稿答案').fill('未提交的第一题答案')
await page.waitForFunction(()=>document.body.textContent.includes('草稿已保存'))
assert.equal(writes.at(-1).answers.q1,'未提交的第一题答案')
await page.getByRole('button',{name:'标记待检查',exact:true}).click()
await page.getByRole('button',{name:'2',exact:true}).click()
await page.getByLabel('本题草稿答案').fill('第二题草稿')
await page.waitForTimeout(650)
assert.equal(writes.at(-1).active_item_id,'q2');assert.deepEqual(writes.at(-1).flagged_item_ids,['q1'])
await page.reload();await page.getByLabel('本题草稿答案').waitFor();assert.equal(await page.getByLabel('本题草稿答案').inputValue(),'第二题草稿')
conflict=true;await page.getByLabel('本题草稿答案').fill('保存失败仍保留')
await page.getByRole('alert').filter({hasText:'草稿已在别处修改'}).waitFor();assert.equal(await page.getByLabel('本题草稿答案').inputValue(),'保存失败仍保留');conflict=false
checks.push('学习草稿自动存、题位/标记恢复、冲突不覆盖')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=browser')
await page.getByAltText('受控浏览器当前画面').waitFor()
await page.getByRole('button',{name:'指出网页元素',exact:true}).click();await page.getByAltText('受控浏览器当前画面').click()
await page.getByTestId('selection').filter({hasText:'#submit'}).waitFor()
await page.getByRole('button',{name:'交给澪',exact:true}).click();await page.getByText('澪正在使用此页面',{exact:false}).waitFor()
await page.getByRole('button',{name:'关闭检查组件'}).click();await page.waitForTimeout(200);assert.equal(controls.at(-1).visible,false)
checks.push('网页目标可见、点选传回、接管/隐藏释放')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=browser-auto')
await page.getByText('澪正在使用此页面',{exact:false}).waitFor()
await page.getByRole('button',{name:'关闭检查组件'}).click();await page.waitForTimeout(200)
assert.equal(controls.at(-1).visible,false)
await page.getByRole('button',{name:'重开检查组件'}).click()
await page.getByText('你已接管',{exact:false}).waitFor()
assert.equal(controls.at(-1).owner,'user')
checks.push('新网页自动可见交接，手动隐藏后重开不夺回')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=progress')
await page.waitForFunction(()=>document.querySelector('.task-progress-group')&&!document.querySelector('.task-progress-group').open)
assert.equal(await page.getByText('已保存最终正文',{exact:true}).count(),0)
await page.locator('.task-progress-group>summary').click();assert.equal(await page.locator('.task-progress-group').evaluate(el=>el.open),true)
await page.getByTestId('selection').filter({hasText:'page'}).waitFor()
checks.push('流结算后成功折叠、正文不重复、手动可展开')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=files')
await page.getByText('+second file',{exact:true}).waitFor()
await page.getByRole('button',{name:'a.py',exact:true}).click();await page.getByText('-old\n+new',{exact:true}).waitFor()
await page.getByRole('button',{name:'b.py · 已查看',exact:true}).click()
await page.getByRole('button',{name:'终端',exact:true}).click();await page.getByRole('button',{name:'python',exact:false}).click();await page.getByLabel('终端标准输入').fill('print(42)');await page.getByRole('button',{name:'发送到终端'}).click()
assert.equal(stdin.at(-1).text,'print(42)\n')
await page.getByRole('button',{name:'结束输入',exact:true}).click();assert.equal(stdin.at(-1).close,true)
checks.push('真实多文件diff查看状态、交互终端输入')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=references')
await page.getByLabel('测试引用输入').fill('@notes');await page.getByRole('button',{name:'notes.txt',exact:true}).click();await page.getByRole('button',{name:'@notes.txt',exact:true}).waitFor()
referenceChanged=true;await page.getByRole('button',{name:'@ 引用文件、目录或会话'}).click();await page.getByRole('button',{name:'notes.txt',exact:true}).click();await page.getByRole('alert').filter({hasText:'引用来源已变化'}).waitFor();referenceChanged=false
checks.push('@引用来源版本、授权失败可见')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=navigator')
const tick=page.getByRole('button',{name:'跳转到第 1 条用户发言：更早的问题',exact:true})
await tick.waitFor();assert.equal(await page.locator('.message-ticks>button').count(),2)
await tick.click();await page.locator('[data-message-anchor="user-1"].message-jump-target').waitFor()
assert.equal(await page.getByRole('button',{name:/跳转.*不应有AI刻度/}).count(),0)
checks.push('未加载用户刻度先加载再定位，AI发言不进入刻度')
await page.goto(base+'/scripts/agent-experience-preview.html?mode=tasks')
await page.getByText('已完成任务仍可定位',{exact:true}).waitFor()
checks.push('已完成通知目标在活动筛选中仍可定位')
integratedTask=true
await page.goto(base+'/?workspace=agent&conversation_id=desktop_agent_test')
await page.getByText('集成回答',{exact:true}).waitFor()
await page.getByAltText('受控浏览器当前画面').waitFor()
await page.getByText('澪正在使用此页面',{exact:false}).waitFor()
await page.getByRole('button',{name:'收起文件预览',exact:true}).click()
await page.waitForTimeout(200);assert.equal(controls.at(-1).visible,false)
checks.push('实际Agent恢复任务SSE自动打开受控网页，隐藏释放控制')
await page.locator('.model-picker-trigger').click();await page.getByRole('button',{name:'模型',exact:false}).first().click()
await page.getByRole('searchbox',{name:'搜索模型'}).fill('本地测试');await page.getByRole('button',{name:'本地测试模型',exact:false}).waitFor()
await page.screenshot({path:path.join(output,'agent-integrated.png')})
checks.push('实际Agent页面加载、中文缓存0、模型搜索、用户刻度')
await page.locator('.model-picker-trigger').click()
rejectChat=true
await page.locator('.composer-input').fill('发送失败仍应保留这段文字')
await page.locator('.file-input').setInputFiles({name:'失败保留.txt',mimeType:'text/plain',buffer:Buffer.from('合成附件正文')})
await page.getByRole('button',{name:'移除附件',exact:false}).waitFor()
await page.getByRole('button',{name:'发送',exact:true}).click()
await page.waitForFunction(()=>document.querySelector('.composer-input')?.value==='发送失败仍应保留这段文字' && !document.querySelector('.send-button[title="停止回复"]'))
assert.equal(sent.at(-1).attachments[0].text,'合成附件正文')
assert.equal(await page.locator('.attachment-preview-list').getByText('失败保留.txt',{exact:true}).count(),1)
rejectChat=false
checks.push('真实Agent发送被拒后，原文字和附件恢复且未自动重发')
assert.deepEqual(errors,[])
await fs.writeFile(path.join(output,'result.json'),JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({checks,errors,output}))
}finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
