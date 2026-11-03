/* Real Vue pages, synthetic API interception, one browser and one page. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE||'playwright')
const output=path.resolve(process.env.MIO_UI_EVIDENCE||'../验收与归档/界面一致性审查-20261031/details')
const sample='用于检查正文可读性的合成文本。Mixed text iii WWW 12345。'.repeat(5)
const code='const greeting = "synthetic-only";'
async function main(){
 const {createServer}=await import('vite'),server=await createServer({server:{host:'127.0.0.1',port:18643,strictPort:true}})
 await server.listen();await fs.mkdir(output,{recursive:true})
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage()
 page.setDefaultTimeout(8000)
 const errors=[],unexpected=[],requests=[],rows=[],checks=[]
 page.on('pageerror',e=>errors.push(e.message))
 await page.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url()),p=url.pathname
  if(url.origin!=='http://127.0.0.1:18643'){unexpected.push(req.url());return route.abort()}
  if(!p.startsWith('/api/'))return route.continue()
  requests.push({path:p,query:url.search,method:req.method(),body:req.postData()})
  let value
  if(p==='/api/settings/runtime')value={revision:'synthetic-revision',settings:{chat_summary_strategy:'independent',agent_summary_strategy:'independent',...(req.method()==='PATCH'?req.postDataJSON():{})}}
  else if(p==='/api/runtime/usage'){
   const days=Number(url.searchParams.get('days'))
   const item={calls:days,failed:1,prompt_tokens:123456,completion_tokens:1234,cached_prompt_tokens:54321,cache_write_tokens:1000,cache_hit_ratio:.44,cache_reporting_coverage:.8,input_usage_coverage:1,retries:2,unknown_cost_calls:3,known_cost_yuan:12.3456,p95_duration_ms:2345}
   value={modes:[{...item,mode:'chat'}],categories:[{...item,category:'context_compression'}],routes:[{...item,model_id:'synthetic-long-model-route-'+('0123456789'.repeat(6)),protocol_mode:'responses',route:'/synthetic/model-route-with-long-path'}],not_sent_calls:2}
  }
  else if(p==='/api/workspace')value={projects:[],tasks:[],templates:[],routines:[],browser_pending:[]}
  else if(p==='/api/workspace/tasks')value={tasks:[],has_more:false}
  else if(p==='/api/workspace/pending')value={items:[],total:0,has_more:false}
  else if(p==='/api/workspace/external-roots')value={roots:[]}
  else if(p==='/api/artifacts')value={artifacts:[{id:'plain',name:'正文.txt',version:1}],has_more:false}
  else if(p==='/api/artifacts/plain/preview')value={kind:'text',content:sample}
  else if(p==='/api/workspace/execution/projects/synthetic-project')value={code:{read_enabled:true,execute_enabled:false},desktop:{enabled:false}}
  else if(p.endsWith('/history'))value={changes:[],jobs:[],observations:[]}
  else if(p.endsWith('/files'))value={files:[{path:'synthetic.js'}],has_more:false}
  else if(p.endsWith('/file'))value={lines:[{number:1,text:code}],content:code,sha256:'synthetic-hash',has_more:false}
  else if(p==='/api/agent/tools')value={tools:[{name:'synthetic_read_file',description:'读取合成目录中的文件，不包含私人资料。',permission:'read_only',available:true,timeout_seconds:30,supports_cancellation:true,idempotent:true,dependencies:[]}]}
  else if(p==='/api/agent/autonomy')value={policy:{autonomy_level:'suggest',capability_overrides:{}},events:[]}
  else if(p==='/api/agent/tool-receipts')value={receipts:[]}
  else {unexpected.push(req.url());return route.fulfill({status:500,json:{detail:'Unmocked synthetic endpoint'}})}
  return route.fulfill({json:value})
 })
 try{
  for(const theme of ['xuan','calm-night'])for(const font of ['small','medium','large'])for(const width of [1152,390])for(const view of ['usage','workspace','preview','tools']){
   await page.setViewportSize({width,height:850})
   await page.goto(`http://127.0.0.1:18643/scripts/details-audit-preview.html?view=${view}&theme=${theme}&font=${font}`)
   if(view==='usage'){
    await page.locator('.usage-totals').waitFor()
    assert.equal(await page.getByRole('combobox',{name:'统计范围',exact:true}).count(),1)
    assert.equal(await page.getByRole('combobox',{name:'普通聊天',exact:true}).count(),1)
    assert.equal(await page.getByRole('combobox',{name:'Agent 工作',exact:true}).count(),1)
    const details=page.locator('.usage-details');assert.equal(await details.count(),3)
    for(const detail of await details.all())assert.equal(await detail.getAttribute('open'),null)
    for(const detail of await details.all())await detail.locator('summary').click()
    await page.getByText('上下文压缩',{exact:true}).waitFor()
    await page.getByText('/synthetic/model-route-with-long-path',{exact:true}).waitFor()
    await page.getByText('未知费用不会按零费用计算',{exact:false}).waitFor()
    assert.equal(await page.locator('.usage-table tbody tr').count(),3)
    const table=page.locator('.usage-details .usage-table').last()
    const scroll=await table.evaluate(el=>{el.scrollLeft=el.scrollWidth;return{left:el.scrollLeft,overflow:el.scrollWidth>el.clientWidth}})
    if(width===390)assert(scroll.overflow&&scroll.left>0,'narrow table content must remain horizontally accessible')
    if(theme==='xuan'&&font==='medium'&&width===1152){
     await page.getByRole('combobox',{name:'统计范围',exact:true}).click();await page.getByRole('option',{name:'近 7 天',exact:true}).click()
     await page.waitForFunction(()=>document.querySelector('.usage-totals strong')?.textContent==='7')
     const before=requests.filter(r=>r.path==='/api/runtime/usage').length
     await page.getByRole('button',{name:'刷新用量',exact:true}).click()
     await page.waitForFunction(()=>!document.querySelector('.usage-toolbar button').disabled)
     assert.equal(requests.filter(r=>r.path==='/api/runtime/usage').length,before+1)
     assert.match(requests.filter(r=>r.path==='/api/runtime/usage').at(-1).query,/days=7/)
     await page.getByRole('combobox',{name:'普通聊天',exact:true}).click();await page.getByRole('option',{name:'尝试复用原请求前缀',exact:true}).click()
     await page.getByRole('button',{name:'保存摘要策略',exact:true}).click();await page.getByRole('status').filter({hasText:'摘要策略已保存'}).waitFor()
     const write=requests.find(r=>r.path==='/api/settings/runtime'&&r.method==='PATCH');assert.equal(JSON.parse(write.body).chat_summary_strategy,'same_prefix')
     checks.push('真实用量范围切换与刷新请求/结果更新，摘要策略保存保留')
     for(const detail of await details.all())if(await detail.getAttribute('open')===null)await detail.locator('summary').click()
    }
   }else if(view==='workspace'){
    await page.waitForFunction(()=>document.querySelector('.workspace-hub')?.getAttribute('aria-busy')==='false')
    const titleSize=await page.locator('.hub-heading h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))
    assert(Math.abs(titleSize-22*({small:.92,medium:1,large:1.12}[font]))<.1)
    const projectLabel=await page.locator('.hub-project-bar label').evaluate(el=>{const text=[...el.childNodes].find(n=>n.nodeType===Node.TEXT_NODE&&n.textContent.includes('当前项目'));const range=document.createRange();range.selectNodeContents(text);return{lines:range.getClientRects().length,width:el.getBoundingClientRect().width}})
    assert.equal(projectLabel.lines,1,'current project label should stay on one line')
    assert(projectLabel.width<width,'project selector must fit narrow viewport')
    await page.getByRole('textbox',{name:'任务目标'}).fill('合成任务，仅验证草稿输入与刷新')
    const before=requests.filter(r=>r.path==='/api/workspace').length
    await page.getByRole('button',{name:'刷新',exact:true}).click()
    await page.waitForFunction(()=>document.querySelector('.workspace-hub')?.getAttribute('aria-busy')==='false')
    assert.equal(requests.filter(r=>r.path==='/api/workspace').length,before+1)
    assert.equal(await page.getByRole('textbox',{name:'任务目标'}).inputValue(),'合成任务，仅验证草稿输入与刷新')
   }else if(view==='preview'){
    const plain=page.locator('pre.agent-plain-text');await plain.waitFor();assert.equal(await plain.textContent(),sample)
    const plainFont=await plain.evaluate(el=>getComputedStyle(el).fontFamily)
    assert(!/Consolas|monospace/i.test(plainFont))
    if(font==='medium')await page.screenshot({path:path.join(output,`preview-plain-${theme}-${width}.png`),fullPage:true})
    await page.getByRole('button',{name:'代码',exact:true}).click();await page.getByRole('button',{name:'synthetic.js',exact:true}).click()
    const pre=page.locator('.agent-preview-scroll pre');await pre.waitFor();assert.match(await pre.textContent(),/1  const greeting/)
    assert.match(await pre.evaluate(el=>getComputedStyle(el).fontFamily),/Consolas|monospace/i)
    rows.push({theme,font,width,view:'preview-fonts',plainFont,codeFont:await pre.evaluate(el=>getComputedStyle(el).fontFamily)})
   }else{
    await page.getByText('synthetic_read_file',{exact:true}).waitFor()
    await page.getByRole('searchbox').fill('no-match');assert.equal(await page.locator('.agent-tool-card').count(),0)
    await page.getByRole('searchbox').fill('synthetic_read');assert.equal(await page.locator('.agent-tool-card').count(),1)
    await page.evaluate(()=>window.uiDetailsFixture.setToolTab('autonomy'));await page.getByRole('radiogroup',{name:'自主权限档位'}).waitFor()
    await page.evaluate(()=>window.uiDetailsFixture.setToolTab('receipts'));await page.getByText('还没有工具执行记录。',{exact:true}).waitFor()
   }
   const metric=await page.evaluate(()=>{const root=document.querySelector('main,aside');return{overflow:document.documentElement.scrollWidth>innerWidth+1,rootOverflow:root.scrollWidth>root.clientWidth+1,rootWidth:root.getBoundingClientRect().width,heading:document.querySelector('h1')?getComputedStyle(document.querySelector('h1')).fontSize:null}})
   assert.equal(metric.overflow,false,`${view}/${theme}/${font}/${width}: document overflow`)
   assert.equal(metric.rootOverflow,false,`${view}/${theme}/${font}/${width}: content pane overflow`)
   rows.push({theme,font,width,view,...metric})
   if(font==='medium')await page.screenshot({path:path.join(output,`${view}-${theme}-${width}.png`),fullPage:true})
  }
  checks.push('用量三折叠默认收起且展开保留所有统计/费用说明，窄屏表格可滚动访问','工作台字号跟随22px层级，刷新保留输入草稿','实际文件正文为常规字体，源码保持等宽与行号','工具过滤、自主策略与记录入口均可访问','四组件×明暗×三字号×两宽度，48case无文档横向溢出')
  assert.deepEqual(errors,[]);assert.deepEqual(unexpected,[])
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify({checks,cases:48,rows,requests,pageErrors:errors,unexpected},null,2))
  console.log(JSON.stringify({cases:48,checks:checks.length,pageErrors:errors}))
 }catch(error){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});const layout=await page.locator('main,.mio-usage-settings,.usage-details,.usage-table').evaluateAll(nodes=>nodes.map(el=>({class:el.className,client:el.clientWidth,scroll:el.scrollWidth,width:el.getBoundingClientRect().width,display:getComputedStyle(el).display,minWidth:getComputedStyle(el).minWidth,overflow:getComputedStyle(el).overflowX})));await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({message:error.message,url:page.url(),layout,errors,unexpected,rows,requests},null,2));throw error}
 finally{await browser.close();await server.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
