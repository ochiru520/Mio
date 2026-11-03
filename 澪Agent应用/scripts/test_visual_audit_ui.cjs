/* Actual components and full CSS cascade; all data is synthetic. No Mio services. */
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const fs = require('node:fs/promises')
const path = require('node:path')
const assert = require('node:assert/strict')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18477'
const output = path.resolve(process.argv[2])
const beforeDir = path.join(output, 'before-css')

async function inspect(page) {
  return page.evaluate(() => {
    const entries = [...document.querySelectorAll('body *')].filter(el => {
      if (el.namespaceURI !== 'http://www.w3.org/1999/xhtml') return false
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el)
      return rect.width && rect.height && style.visibility !== 'hidden' && style.display !== 'none' &&
        [...el.childNodes].some(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim())
    }).map(el => ({ tag:el.tagName, cls:el.className, text:el.textContent.trim().slice(0,50),
      content:Boolean(el.closest('.message-bubble,.markdown-body,pre,code')), size:parseFloat(getComputedStyle(el).fontSize),
      style:getComputedStyle(el).fontStyle, font:getComputedStyle(el).fontFamily, transform:getComputedStyle(el).textTransform }))
    const styles = selector => {
      const el = document.querySelector(selector)
      if (!el) return null
      const s = getComputedStyle(el), r=el.getBoundingClientRect()
      return { size:s.fontSize, font:s.fontFamily, image:s.backgroundImage, shadow:s.boxShadow,
        radius:s.borderRadius, borderLeft:s.borderLeftWidth, right:r.right, bottom:r.bottom, width:r.width }
    }
    return { under12:entries.filter(e=>e.size<11.999), chromeDecorations:entries.filter(e=>!e.content&&(e.style==='italic'||e.transform==='uppercase'||/monospace/i.test(e.font))), sizes:[...new Set(entries.map(e=>e.size))].sort((a,b)=>a-b),
      uiSizes:[...new Set(entries.filter(e=>!e.content).map(e=>e.size))].sort((a,b)=>a-b),
      heading:styles('h1'), shell:styles('.app-shell, .mio-agent-surface'), composer:styles('.composer'),
      tokens:['xs','sm','base','md','lg','xl','2xl','3xl'].map(k=>getComputedStyle(document.documentElement).getPropertyValue('--text-'+k).trim()),
      horizontalOverflow:document.documentElement.scrollWidth>innerWidth+1, entries }
  })
}

async function main() {
  await fs.mkdir(output,{recursive:true})
  const browser=await chromium.launch({headless:true,channel:'msedge'})
  const context=await browser.newContext({viewport:{width:1250,height:850}})
  const requests=[], errors=[], rows=[], baseline=[]
  await context.route('**/*',async route=>{
    const req=route.request(),url=new URL(req.url())
    if(url.origin!==new URL(base).origin) return route.abort()
    if(!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(url.pathname)) return route.continue()
    requests.push({path:url.pathname,method:req.method()})
    let value={}
    if(url.pathname==='/api/workspace') value={projects:[],tasks:[],templates:[],routines:[],browser_pending:[]}
    if(url.pathname==='/api/workspace/tasks') value={tasks:[],has_more:false}
    if(url.pathname==='/api/workspace/pending')value={items:[],total:0,has_more:false}
    if(url.pathname==='/api/agent/work') value={tasks:[{id:'visual-task',status:'completed',original_goal:'完成一次语音试听'}]}
    if(url.pathname.includes('tool-receipts'))value={receipts:[]}
    return route.fulfill({json:value})
  })
  const page=await context.newPage()
  page.on('pageerror',e=>errors.push(e.message))
  const open=async(view,theme,font,width=1250)=>{
    await page.setViewportSize({width,height:850})
    await page.goto(`${base}/scripts/visual-audit-preview.html?view=${view}&theme=${theme}&font=${font}`)
    await page.locator('#app > *').first().waitFor()
    await page.evaluate(()=>document.fonts.ready)
    await page.waitForTimeout(100)
    if (['chat','agent-chat'].includes(view)) {
      const trigger = page.locator('.model-picker-trigger')
      await trigger.waitFor()
      assert.equal(await trigger.locator('svg').count(),0,'Actual ChatPage model menu trigger must have no dropdown arrow')
      assert.equal(await trigger.getAttribute('aria-expanded'),'false','ChatPage menu keeps its accessible expanded state')
    }
  }
  try {
    const hasBaseline = await fs.stat(beforeDir).then(info => info.isDirectory(), () => false)
    for(const view of hasBaseline ? ['home','workspace','profile'] : []) {
      await open(view,'calm-day','small')
      const cssOrder=['tokens.css','base.css','chat.css','home.css','records.css','settings.css','companion.css','creation.css','agent-workspace.css']
      let before=''
      for(const file of cssOrder) before+=await fs.readFile(path.join(beforeDir,'src/styles',file),'utf8')+'\n'
      before+=(await fs.readFile(path.join(beforeDir,'src/styles.css'),'utf8')).replace(/^@import.*$/gm,'')+'\n'
      for(const file of ['integrated.css','refinements.css','agent-settings.css','agent-surface.css','ui-polish.css','calm.css']) before+=await fs.readFile(path.join(beforeDir,'src/styles',file),'utf8')+'\n'
      await page.addStyleTag({content:before})
      baseline.push({view,...await inspect(page)})
      await page.screenshot({path:path.join(output,`before-${view}.png`)})
    }
    for(const theme of ['xuan','ink','calm-day','calm-night']) {
      for(const font of ['small','medium','large']) {
        for(const view of ['home','workspace','chat','agent-chat','profile']) {
          await open(view,theme,font)
          const result=await inspect(page)
          rows.push({view,theme,font,width:1250,...result})
          assert.deepEqual(result.under12,[],`${view}/${theme}/${font}: text below 12px`)
          assert.deepEqual(result.chromeDecorations,[],`${view}/${theme}/${font}: decorative chrome typography`)
          assert.equal(result.horizontalOverflow,false,`${view}/${theme}/${font}: horizontal overflow`)
          if(result.heading && ['calm-day','calm-night'].includes(theme)) assert(parseFloat(result.heading.size)<=22*1.12+0.1)
          if(font==='medium' && ['home','workspace','profile','chat'].includes(view))await page.screenshot({path:path.join(output,`${view}-${theme}.png`)})
        }
      }
      for(const view of ['home','workspace','chat','agent-chat','profile','pet','voice','screen','tasks']) {
        await open(view,theme,'small',600)
        const result=await inspect(page)
        rows.push({view,theme,font:'small',width:600,...result})
        assert.deepEqual(result.under12,[],`${view}/${theme}/narrow: text below 12px`)
        assert.deepEqual(result.chromeDecorations,[],`${view}/${theme}/narrow: decorative chrome typography`)
        assert.equal(result.horizontalOverflow,false,`${view}/${theme}/narrow: horizontal overflow`)
        if(theme==='calm-night')await page.screenshot({path:path.join(output,`narrow-${view}.png`)})
      }
      console.log(`Visual audit passed for ${theme}`)
    }
    assert.deepEqual(errors,[])
    assert(requests.every(r=>r.method==='GET'),'Fixture should not mutate any service')
    await page.emulateMedia({reducedMotion:'reduce'})
    await page.evaluate(()=>{const el=document.createElement('span');el.className='spin';el.textContent='加载';document.body.append(el)})
    const duration=await page.locator('body > .spin').evaluate(el=>getComputedStyle(el).animationDuration)
    assert.equal(duration,'0.001s')
    await fs.writeFile(path.join(output,'visual-audit.json'),JSON.stringify({ok:true,caseCount:rows.length,rows,baseline,errors,requests,reducedMotion:duration,scope:'Real Vue components with synthetic injected state, intercepted service requests, full production CSS order. No private data, audio, GPU, or desktop operations.'},null,2))
    console.log(`Visual audit passed: ${rows.length} cases`)
  } catch(error) {
    await page.screenshot({path:path.join(output,'visual-failure.png')})
    await fs.writeFile(path.join(output,'visual-audit-partial.json'),JSON.stringify({rows,baseline,errors,requests,error:String(error)},null,2))
    throw error
  } finally {await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
