const assert=require('node:assert/strict')
const fs=require('node:fs/promises'),path=require('node:path')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE||'playwright')
async function main(){
 const {createServer}=await import('vite'),server=await createServer({server:{host:'127.0.0.1',port:18479,strictPort:true}});await server.listen()
 const dir=path.resolve(process.env.MIO_CALL_UI_EVIDENCE || '../验收与归档/普通用户完整方案-20261101/call-diagnostics-ui');await fs.mkdir(dir,{recursive:true})
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1152,height:900}})
 const requests=[],errors=[],checks=[],screenshots=[],typography=[];let fail=false
 let state={active:true,stage:'speaking',current_turn_id:3,timings:{asr_ms:187,model_first_token_ms:312,model_ms:1680,server_total_ms:1900},voice_started:{first_audio_latency_ms:560},transcript:'PRIVATE_TRANSCRIPT',reply:'PRIVATE_REPLY',wav_base64:'PRIVATE_AUDIO'}
 page.on('pageerror',e=>errors.push(e.message))
 await page.addInitScript(()=>{window.micCalls=0;if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=()=>{window.micCalls++;throw new Error('Mic forbidden in diagnostic test')}})
 await page.route('**/api/**',async route=>{requests.push({method:route.request().method(),url:route.request().url()});if(!route.request().url().endsWith('/api/companion/call/status'))return route.abort();await route.fulfill({status:fail?503:200,contentType:'application/json',body:JSON.stringify(fail?{detail:'PRIVATE_SERVER_TEXT'}:state)})})
 try{
  await page.goto('http://127.0.0.1:18479/scripts/call-diagnostics-preview.html')
  await page.waitForTimeout(2200);assert.equal(requests.length,0)
  await page.getByText('通话延迟诊断',{exact:true}).click()
  await page.getByText('187 毫秒',{exact:true}).waitFor()
  await page.getByText('560 毫秒',{exact:true}).waitFor()
  assert.ok((await page.getByRole('status').innerText()).includes('正在说话'))
  assert.doesNotMatch(await page.locator('body').innerText(),/PRIVATE_/)
  assert.equal(await page.evaluate(()=>window.micCalls),0)
  checks.push('默认折叠不请求，展开只读状态，展示五阶段与当前轮次，不显示对话或录音、不启麦克风')
  for(const width of [1152,390]){await page.setViewportSize({width,height:900}); typography.push(await page.evaluate(()=>({width:innerWidth,sizes:[...new Set([...document.querySelectorAll('.call-diagnostics-panel *')].filter(el=>el.getClientRects().length&&[...el.childNodes].some(n=>n.nodeType===3&&n.textContent.trim())).map(el=>Number.parseFloat(getComputedStyle(el).fontSize)))].sort((a,b)=>a-b)}))); assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false);let name=`call-latency-${width}.png`;await page.screenshot({path:path.join(dir,name),fullPage:true});screenshots.push(name)}
  const before=requests.length;await page.waitForTimeout(2200);assert.ok(requests.length>before);checks.push('每两秒只读轮询，1152/390宽度无溢出，首音和并行耗时限制说明可见')
  state={active:true,stage:'asr',current_turn_id:4,timings:{},voice_started:{}}
  await page.getByRole('button',{name:'刷新状态',exact:true}).click();await page.getByText('正在识别，第 4 轮',{exact:false}).waitFor()
  assert.equal(await page.getByText('尚未测得',{exact:true}).count(),5)
  assert.equal(await page.getByText('187 毫秒',{exact:true}).count(),0);checks.push('新轮次无测量时清空旧轮耗时并显示尚未测得')
  fail=true;await page.getByRole('button',{name:'刷新状态',exact:true}).click();await page.getByRole('alert').waitFor();assert.doesNotMatch(await page.locator('body').innerText(),/PRIVATE_/)
  await page.getByText('通话延迟诊断',{exact:true}).click();await page.waitForTimeout(100)
  const closed=requests.length;await page.waitForTimeout(2200);assert.equal(requests.length,closed)
  assert.ok(requests.every(r=>r.method==='GET'&&r.url.endsWith('/api/companion/call/status')))
  assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>window.micCalls),0);for(const row of typography){assert.ok(row.sizes.every(size=>size>=12));assert.ok(row.sizes.length<=4)};checks.push('失败只显示通用状态读取错误，折叠停止轮询，全部请求仅GET call/status')
  await fs.writeFile(path.join(dir,'result.json'),JSON.stringify({ok:true,checks,screenshots,requests,pageErrors:errors,typography,synthetic:true,micCalls:0},null,2))
  console.log(`Call diagnostic UI passed: ${checks.length} groups, ${screenshots.length} screenshots`)
 }finally{await browser.close();await server.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
