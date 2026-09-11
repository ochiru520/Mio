const assert=require('node:assert/strict')
const path=require('node:path')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
async function main(){
 const browser=await chromium.launch({channel:'msedge',headless:true})
 const page=await browser.newPage()
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 try{
  for(const width of [1280,800,390]){
   await page.setViewportSize({width,height:900})
   await page.goto('http://127.0.0.1:1422/scripts/diary-recovery-preview.html',{waitUntil:'domcontentloaded',timeout:60000})
   await page.getByRole('status').waitFor()
   assert.match(await page.getByRole('status').innerText(),/401/)
   assert.match(await page.getByRole('status').innerText(),/5 分钟/)
   const layout=await page.locator('.record-reader-layout').boundingBox()
   assert.ok(layout.height>500 && layout.y+layout.height<=901,JSON.stringify(layout))
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
   await page.screenshot({path:path.join(process.env.MIO_UI_EVIDENCE,`diary-error-${width}.png`)})
   await page.getByRole('button',{name:'检查补写',exact:true}).click()
   assert.equal(await page.evaluate(()=>window.retryCount),1)
   assert.ok(await page.getByRole('button',{name:'正在生成',exact:true}).isDisabled())
   await page.getByRole('button',{name:'日记设置',exact:true}).click()
   await page.getByLabel('日记专用模型').selectOption('model-b')
   await page.getByRole('button',{name:'保存',exact:true}).click()
   assert.equal(await page.evaluate(()=>window.savedModel),'model-b')
   await page.screenshot({path:path.join(process.env.MIO_UI_EVIDENCE,`diary-settings-${width}.png`)})
  }
  assert.deepEqual(errors,[])
  console.log('Diary UI: 1280/800/390 error, retry, settings selection and save passed')
 }catch(error){console.error('pageerrors',errors);await page.screenshot({path:path.join(process.env.MIO_UI_EVIDENCE,'ui-failure.png')});throw error}finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exit(1)})
