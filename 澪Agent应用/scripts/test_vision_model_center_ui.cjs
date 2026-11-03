/* Real Vue settings, synthetic runtime only; no model launch or user settings writes. */
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE||'playwright')
async function main(){
  const {createServer}=await import('vite')
  const server=await createServer({server:{host:'127.0.0.1',port:18483,strictPort:true}});await server.listen()
  const browser=await chromium.launch({channel:'msedge',headless:true})
  const output=path.resolve(process.env.MIO_UI_EVIDENCE||'../验收与归档/语音故障与排版修复-20261101/vision-model-center');await fs.mkdir(output,{recursive:true})
  const page=await browser.newPage();const errors=[],checks=[]
  page.on('pageerror',error=>errors.push(error.message))
  try{
    for(const width of [1152,390])for(const mode of ['stopped','running','empty','unknown','mismatch','cloud']){
      await page.setViewportSize({width,height:900})
      await page.goto(`http://127.0.0.1:18483/scripts/vision-model-center-preview.html?mode=${mode}`)
      await page.getByRole('button',{name:'屏幕观察',exact:true}).waitFor()
      if(mode==='cloud'){
        assert.equal(await page.getByRole('region',{name:'本地视觉模型'}).count(),0)
        assert.equal(await page.locator('select').first().inputValue(),'fixture-cloud')
      }else{
        const panel=page.getByRole('region',{name:'本地视觉模型'});await panel.waitFor()
        const text=await panel.innerText();assert.match(text,/配置模型：qwen3.5:2b/)
        if(mode==='stopped')assert.match(text,/已加载模型：无（服务未启动）/)
        if(mode==='running')assert.match(text,/已加载模型：qwen3.5:2b/)
        if(mode==='empty')assert.match(text,/无（模型尚未加载）/)
        if(mode==='unknown')assert.match(text,/当前加载情况待确认/)
        if(mode==='mismatch')assert.match(text,/different:4b[\s\S]*当前加载的模型与配置不同/)
        await panel.getByRole('button',{name:'刷新模型状态'}).click()
        await panel.getByRole('button',{name:'模型管理与安装'}).click()
        assert.deepEqual(await page.evaluate(()=>fixtureActions),['refresh','dependencies'])
      }
      assert.equal(await page.evaluate(()=>fixture.companionStatus.pet.settings.screen_vision_model_id),'fixture-cloud')
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true)
      await page.screenshot({path:path.join(output,`vision-${mode}-${width}.png`),fullPage:true});checks.push(`${mode}-${width}`)
    }
    let requests=0,dependencies=0
    await page.route('**/api/**',async route=>{
      const url=new URL(route.request().url());let result={}
      if(url.pathname==='/api/dependencies'){dependencies++;await route.abort();return}
      if(url.pathname==='/api/model-management'){
        requests++;const ready=requests>1
        result={checking:!ready,items:[{id:'fixture',category:'speech',label:'先显示的模型',description:'慢探针不会阻塞列表',status:ready?'installed':'checking',installed:ready?true:null,dependency_id:null,limitations:[],sources:[]}],storage:[],hardware:null,sections:{items:{state:ready?'error':'checking',has_value:ready,error:ready?'检查失败，可重新检查':'',stale:true},storage:{state:'checking',has_value:false}}}
      }else if(url.pathname.endsWith('/voices'))result={voices:[]}
      else if(url.pathname.includes('/capabilities'))result={capabilities:[],busy:false}
      await route.fulfill({contentType:'application/json',body:JSON.stringify(result)})
    })
    for(const width of [1152,390]){
      requests=0;await page.setViewportSize({width,height:900});await page.goto('http://127.0.0.1:18483/scripts/model-center-preview.html')
      await page.getByText('先显示的模型',{exact:true}).waitFor();await page.getByText('安装与运行状态待检查',{exact:true}).waitFor()
      assert.equal(await page.getByText('尚未安装',{exact:true}).count(),0)
      await page.getByText('文件已安装，待启动验证',{exact:true}).waitFor()
      await page.getByText('模型状态：检查失败，可重新检查，当前显示上次结果',{exact:true}).waitFor()
      assert.equal(dependencies,0)
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true)
      await page.screenshot({path:path.join(output,`catalog-partial-error-${width}.png`),fullPage:true});checks.push(`catalog-${width}`)
    }
    assert.deepEqual(errors,[])
    await fs.writeFile(path.join(output,'ui-results.json'),JSON.stringify({checks,errors,legacyDependenciesRequests:dependencies},null,2))
    console.log(JSON.stringify({passed:checks.length,errors,dependencies}))
  }finally{await browser.close();await server.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
