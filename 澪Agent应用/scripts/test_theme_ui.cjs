const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:1438'
const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-evidence')
const task = { id:'test-task', original_goal:'核对主题与字号', snapshot:{goal:'核对主题与字号',plan:['检查页面文字','核对结果']}, observations:[], status:'paused', spent_yuan:0 }
function contrast(a, b) {
  const luminance = color => {
    const c = color.match(/[\d.]+/g).slice(0,3).map(Number).map(v => v/255).map(v => v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4)
    return c[0]*.2126+c[1]*.7152+c[2]*.0722
  }
  const aa=luminance(a), bb=luminance(b)
  return (Math.max(aa,bb)+.05)/(Math.min(aa,bb)+.05)
}
async function main() {
  await fs.mkdir(output,{recursive:true})
  const { THEME_OPTIONS } = await import('../src/themeOptions.js')
  const browser=await chromium.launch({channel:process.env.MIO_UI_BROWSER || 'msedge',headless:true})
  const errors=[], requests=[], results=[]
  let oldBackend=false
  try {
    const context=await browser.newContext({viewport:{width:1280,height:900}})
    await context.route('**/*', async route => {
      const req=route.request(), url=new URL(req.url())
      if(url.origin !== new URL(base).origin) return route.abort()
      if(!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(url.pathname)) return route.continue()
      requests.push({path:url.pathname,method:req.method()})
      let value={}
      if (!['/api/capabilities','/api/onboarding/status','/api/agent/bootstrap','/api/companion/status','/api/agent/work','/api/agent/messages','/diaries','/reviews','/weekly','/api/creation/configuration'].includes(url.pathname) && !url.pathname.includes('startup-greeting') && !url.pathname.includes('/profile')) return route.continue()
      if(url.pathname==='/api/capabilities') {
        if(oldBackend)return route.fulfill({status:404,json:{detail:'Not Found'}})
        value={busy:false,capabilities:[{id:'comfyui',label:'ComfyUI',status:'unchecked',target:'settings-creation',description:'检查连接',level:'unverified'}]}
      } else if(url.pathname==='/api/onboarding/status') value={completed:true}
      else if(url.pathname==='/api/agent/bootstrap') value={onboarding:{completed:true},conversation_id:'main-test',conversations:[{id:'main-test',title:'测试对话',kind:'chat'},{id:'agent-test',title:'Agent 测试',kind:'agent'}],messages:[],models:[],diaries:[],qq:{},settings:{}}
      else if(url.pathname==='/api/companion/status') value={pet:{settings:{}},screen:{},settings:{}}
      else if(url.pathname==='/api/agent/work') value={tasks:[task],limits:{},has_more:false}
      else if(url.pathname==='/api/agent/messages'||/^\/(diaries|reviews|weekly)$/.test(url.pathname)) value=[]
      else if(url.pathname.includes('startup-greeting')) value={enabled:false}
      else if(url.pathname.includes('/profile')) value={profile:{},settings:{}}
      else if(url.pathname==='/api/creation/configuration') value={environment:{},defaults:{}}
      await route.fulfill({json:value})
    })
    const page=await context.newPage()
    page.on('pageerror',e=>errors.push(e.message))
    for(const theme of THEME_OPTIONS) {
      await page.goto(`${base}/scripts/theme-components-preview.html?theme=${theme.id}`)
      await page.locator('.task-item').waitFor()
      await page.getByRole('combobox',{name:'主题测试选项'}).click()
      await page.locator('.app-select-menu').waitFor()
      const colors=await page.evaluate(()=>{
        const style=s=>getComputedStyle(document.querySelector(s))
        return {taskText:style('.task-item strong').color,surface:style('main').backgroundColor,
          menuText:style('.app-select-menu').color,menuSurface:style('.app-select-menu').backgroundColor,
          updateSurface:style('.update-check').backgroundColor,scheme:style('html').colorScheme}
      })
      assert.ok(contrast(colors.taskText,colors.surface)>=4.5,`${theme.id}: task contrast`)
      assert.ok(contrast(colors.menuText,colors.menuSurface)>=4.5,`${theme.id}: dropdown contrast`)
      assert.equal(colors.scheme,theme.mode)
      results.push({theme:theme.id,...colors,taskContrast:contrast(colors.taskText,colors.surface),menuContrast:contrast(colors.menuText,colors.menuSurface)})
      if(theme.id==='ink')await page.screenshot({path:path.join(output,'components-ink.png')})
      await page.keyboard.press('Escape')
    }
    oldBackend=true
    await page.reload();await page.getByText('当前后端版本不支持能力实测，请更新完整的 Mio 程序后重试。').waitFor()
    assert.equal(await page.getByText('正在读取能力状态…',{exact:true}).count(),0)
    assert.equal(await page.locator('.capability-checks .check-actions').count(),0)
    oldBackend=false
    await page.getByRole('button',{name:'刷新状态',exact:true}).click()
    await page.locator('.capability-checks article').waitFor()
    assert.equal(requests.filter(r=>r.path.startsWith('/api/capabilities/')&&r.method==='POST').length,0)

    await page.goto(`${base}/?view=settings&settings-section=appearance`)
    await page.locator('.theme-picker-option').first().waitFor()
    for(const theme of THEME_OPTIONS) {
      await page.locator('.theme-picker-option').filter({hasText:theme.label}).click()
      await page.waitForFunction(id=>document.documentElement.dataset.theme===id,theme.id)
      assert.equal(await page.evaluate(()=>localStorage.getItem('mio_app_preferences')),null)
    }
    await page.locator('.settings-savebar').getByRole('button',{name:'取消',exact:true}).click()
    await page.waitForFunction(()=>document.documentElement.dataset.theme==='xuan')
    await page.locator('.theme-picker-option').filter({hasText:'墨夜'}).click()
    await page.locator('.settings-savebar').getByRole('button',{name:'保存',exact:true}).click()
    await page.reload();await page.locator('.theme-picker-option').first().waitFor()
    assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),'ink')
    const fonts=[]
    for(const [size,factor] of [['small',.92],['medium',1],['large',1.12]]) {
      await page.getByLabel('字体大小').selectOption(size)
      await page.locator('.settings-savebar').getByRole('button',{name:'保存',exact:true}).click()
      await page.waitForFunction(size=>document.documentElement.dataset.fontSize===size,size)
      const title=await page.locator('.settings-page-title h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))
      assert.ok(Math.abs(title-23*factor)<.05,`${size}: settings title ${title}`)
      await page.evaluate(()=>localStorage.setItem('mio_agent_active_view','home'))
      await page.goto(`${base}/?workspace=agent`);await page.locator('.agent-welcome h1').waitFor()
      const agentTitle=await page.locator('.agent-welcome h1').evaluate(el=>parseFloat(getComputedStyle(el).fontSize))
      assert.ok(Math.abs(agentTitle-24*factor)<.05,`${size}: Agent title ${agentTitle}`)
      fonts.push({size,title,agentTitle})
      await page.goto(`${base}/?view=settings&settings-section=appearance`);await page.locator('.theme-picker-option').first().waitFor()
    }
    let layoutCases=0
    for(const width of [1280,1024,760]) {
      await page.setViewportSize({width,height:900})
      for(const view of ['home','chat','diaries','memory','tasks','companion','settings']) {
        await page.goto(`${base}/?view=${view}&settings-section=appearance`)
        await page.locator('.app-shell').waitFor()
        if(view==='chat')await page.locator('.composer').waitFor()
        if(view==='settings')await page.locator('.theme-picker-option').first().waitFor()
        const bounds=await page.evaluate(()=>{
          const rect=s=>{const el=document.querySelector(s);if(!el)return null;const r=el.getBoundingClientRect();return {right:r.right,bottom:r.bottom,width:r.width}}
          return {shell:rect('.app-shell'),composer:rect('.composer')}
        })
        assert.ok(bounds.shell.right<=width+1&&bounds.shell.bottom<=901)
        if(view==='chat')assert.ok(bounds.composer.bottom<=901&&bounds.composer.width>150)
        layoutCases++
      }
    }
    await page.setViewportSize({width:1280,height:900})
    await page.goto(`${base}/?view=settings&settings-section=dependencies`)
    await page.locator('.capability-checks .check-actions').getByRole('button',{name:'打开对应设置'}).click()
    await page.getByRole('navigation',{name:'设置分类'}).waitFor()
    await page.getByRole('heading',{name:'创作环境',exact:true}).waitFor()
    assert.equal(new URL(page.url()).searchParams.get('workspace'),'agent')
    assert.deepEqual(errors,[])
    const report={themes:results,fonts,layoutCases,oldBackendRecovery:true,themeSaveCancelReload:true,creationNavigation:true,errors}
    await fs.writeFile(path.join(output,'theme-regression.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify({themeCount:results.length,fonts,layoutCases,errors}))
  } finally {await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
