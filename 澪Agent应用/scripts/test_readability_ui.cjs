const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const fs = require('node:fs/promises')
const path = require('node:path')
const assert = require('node:assert/strict')
async function main() {
  const output=process.argv[2], base=process.env.MIO_UI_BASE || 'http://127.0.0.1:18473'
  await fs.mkdir(output,{recursive:true})
  const browser=await chromium.launch({headless:true,channel:'msedge'}), page=await browser.newPage({viewport:{width:1250,height:850}}), errors=[], checks=[]
  page.on('pageerror',e=>errors.push(e.message))
  await page.route('**/api/agent/tool-receipts*',r=>r.fulfill({json:{receipts:[{id:1,tool_name:'agent_task_update',status:'completed'},{id:2,tool_name:'agent_read_document',status:'completed',result:{summary:'已读取文档正文。'}},{id:3,tool_name:'agent_write_document',status:'failed',error:'没有写入权限'}]}}))
  try {
    for (const view of ['home','pet','voice','screen','tasks','receipts','profile']) {
      await page.goto(`${base}/scripts/readability-preview.html?view=${view}`)
      if(view==='home') {const box=await page.getByAltText('首页图片').boundingBox();assert(box.height>=100&&box.width>=100);checks.push('首页图片放大')}
      if(view==='pet') {await page.getByText('桌宠大小',{exact:true}).waitFor();assert.equal(await page.getByLabel('已选择').count(),1);assert.equal(await page.locator('.appearance-choice img').first().evaluate(el=>getComputedStyle(el).width),'60px');checks.push('形象卡片及选择状态')}
      if(view==='tasks') {await page.getByText('Word 文档已生成。',{exact:true}).waitFor();assert.equal(await page.locator('.task-technical-records[open]').count(),0);assert((await page.locator('.task-list').innerText()).length<500);checks.push('聊天执行中心精简中文摘要')}
      if(view==='receipts') {await page.getByText('已读取文档正文。',{exact:true}).waitFor();checks.push('Agent设置执行记录中文摘要')}
      if(view==='profile') {await page.getByText('思考展示',{exact:true}).waitFor();await page.getByLabel('自定义表达',{exact:true}).fill('用自然中文说明进度。');await page.getByRole('button',{name:'保存',exact:true}).click();assert.equal(await page.evaluate(()=>window.savedAnalysisStyle),'用自然中文说明进度。');const opacity=await page.getByRole('button',{name:'套用预设',exact:true}).evaluate(el=>getComputedStyle(el).opacity);assert.equal(opacity,'1');checks.push('自定义表达保存及禁用按钮可读')}
      await page.screenshot({path:path.join(output,view+'.png')})
    }
    await page.goto(`${base}/scripts/readability-preview.html?view=pet&theme=ink`)
    await page.screenshot({path:path.join(output,'pet-dark.png')})
    await page.setViewportSize({width:600,height:850})
    await page.goto(`${base}/scripts/readability-preview.html?view=pet`)
    assert(await page.locator('.compact-pet-panel').evaluate(el=>el.scrollWidth<=el.clientWidth+1))
    checks.push('窄屏布局和深色主题截图')
    assert.deepEqual(errors,[])
    await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify({ok:true,checks,errors,scope:'actual Vue components with synthetic context; no pet or audio operations'},null,2))
    console.log('Readability UI checks passed: '+checks.length)
  } catch(error) { console.error(errors); await page.screenshot({path:path.join(output,'failure.png')}); throw error } finally {await browser.close()}
}
main().catch(error=>{console.error(error);process.exit(1)})
