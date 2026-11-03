const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18489, strictPort: true } })
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/桌宠动态陪看-20261102/ui')
  await fs.mkdir(output, { recursive: true })
  let browser
  const calls = [], checks = [], errors = [], layouts = []
  let running = false, settings = { screen_watch_mode: 'single', screen_watch_pace: 'normal', screen_audio_enabled: false, screen_vision_route: 'local' }
  let delayStart = false, releaseStart = null
  const screen = () => ({ running, mode: 'screen', screen_scope: 'primary', title: '隔离示例屏幕' })
  const status = () => ({ pet: { settings }, screen: screen(), screen_analysis: { watch: { mode: settings.screen_watch_mode, pace: settings.screen_watch_pace, selected_frames: 4, skipped_frames: 30, buffered_frames: 2, sheets: 1 } } })
  try {
    await server.listen()
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1152, height: 1100 } })
    page.on('pageerror',error=>errors.push(error.message))
    await page.route('**/api/**',async route=>{
      const req=route.request(), url=new URL(req.url()), body=req.postDataJSON()
      calls.push({path:url.pathname,method:req.method(),body})
      let result
      if(url.pathname.endsWith('/status')) result=status()
      else if(url.pathname.endsWith('/settings')) { settings={...settings,...body}; result=status() }
      else if(url.pathname.endsWith('/screen/start')) {
        if (delayStart) {
          await new Promise(resolve => { releaseStart = resolve })
          await route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:'启动已取消'})})
          return
        }
        running=true; result=screen()
      }
      else if(url.pathname.endsWith('/screen/stop')) { running=false; result=screen() }
      else throw new Error(`Unexpected ${url.pathname}`)
      await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)})
    })
    await page.goto('http://127.0.0.1:18489/scripts/screen-watch-preview.html')
    await page.getByRole('combobox',{name:'观察方式',exact:true}).click()
    await page.getByRole('option',{name:'动态陪看（试用）',exact:true}).click()
    await page.getByRole('combobox',{name:'变化灵敏度',exact:true}).click()
    await page.getByRole('option',{name:'灵敏 · 更小变化',exact:true}).click()
    await page.getByText('画面有有效变化就提交，不设固定分析间隔；识别和回答仍需时间',{exact:true}).waitFor()
    assert.equal(await page.getByRole('combobox',{name:'陪看节奏',exact:true}).count(),0)
    assert.equal(calls.length,0)
    await page.getByRole('button',{name:'应用修改',exact:true}).click()
    await page.waitForFunction(()=>window.screenWatchFixture.context.companionBusy==='')
    assert.equal(calls.length,1)
    assert.equal(calls[0].body.screen_watch_pace,'dense')
    assert.equal(calls[0].body.screen_audio_enabled,false)
    checks.push('选择和应用只保存，不采集、不启用系统音频或云端')
    await page.getByRole('button',{name:'开始观察',exact:true}).click()
    await page.getByText('动态陪看中',{exact:false}).waitFor()
    assert.equal(running,true)
    assert.equal(settings.screen_watch_mode,'adaptive')
    assert.equal(settings.screen_vision_route,'local')
    assert.equal(settings.screen_audio_enabled,false)
    checks.push('显式开始按所选模式和变化灵敏度启动，界面不再显示旧固定等待，保留本地视觉及音频偏好')
    for(const width of [1152,800,390]) {
      await page.setViewportSize({width,height:1300})
      const result=await page.locator('[aria-label="桌宠陪看设置"]').evaluate(el=>({width:innerWidth,doc:document.documentElement.scrollWidth,clipped:[...el.querySelectorAll('button,p,small,strong')].filter(node=>{ const b=node.getBoundingClientRect(); return b.width && (b.left < -1 || b.right > innerWidth+1 || node.scrollHeight>node.clientHeight+2) }).map(node=>({tag:node.tagName,text:node.textContent}))}))
      assert.ok(result.doc<=width,JSON.stringify(result))
      assert.deepEqual(result.clipped,[],JSON.stringify(result))
      layouts.push(result)
      await page.screenshot({path:path.join(output,`screen-watch-${width}.png`),fullPage:true})
    }
    await page.getByRole('button',{name:'停止',exact:true}).click()
    await page.waitForFunction(()=>window.screenWatchFixture.context.companionStatus.screen.running===false)
    assert.equal(calls.at(-1).path,'/api/companion/screen/stop')
    assert.equal(settings.screen_audio_enabled,false)
    assert.equal(await page.getByText('动态陪看中',{exact:false}).count(),0)
    checks.push('停止调用真实停止入口，等待服务回执并移除运行状态')
    delayStart = true
    await page.getByRole('button',{name:'开始观察',exact:true}).click()
    await page.waitForFunction(()=>window.screenWatchFixture.context.companionBusy==='observation-start')
    while (!releaseStart) await new Promise(resolve=>setTimeout(resolve,10))
    assert.equal(await page.getByRole('button',{name:'停止',exact:true}).isEnabled(),true)
    assert.equal(await page.getByRole('button',{name:'开始观察',exact:true}).isDisabled(),true)
    await page.getByRole('button',{name:'停止',exact:true}).click()
    await page.waitForFunction(()=>window.screenWatchFixture.context.companionBusy==='')
    releaseStart()
    await page.waitForFunction(()=>window.screenWatchFixture.context.observationStarting===false)
    assert.equal(await page.locator('[role="alert"]').innerText(),'')
    assert.equal(running,false)
    assert.equal(await page.getByRole('button',{name:'开始观察',exact:true}).isEnabled(),true)
    checks.push('冷加载期间可停止，迟到409不覆盖停止状态或显示旧错误')
    assert.deepEqual(errors,[])
    const result={ok:true,checks,layouts,errors,calls}
    await fs.writeFile(path.join(output,'ui-result.json'),JSON.stringify(result,null,2))
    console.log(JSON.stringify(result))
  } finally { await browser?.close(); await server.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
