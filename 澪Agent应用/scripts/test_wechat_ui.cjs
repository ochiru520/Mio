const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:1439'
const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-evidence')

async function main() {
  await fs.mkdir(output,{recursive:true})
  const browser=await chromium.launch({channel:'msedge',headless:true})
  const errors=[], requests=[]
  let configured=false, enabled=false, polls=0
  try {
    const context=await browser.newContext({viewport:{width:1100,height:900}})
    await context.route('**/*', async route=>{
      const request=route.request(), url=new URL(request.url())
      if(url.origin!==new URL(base).origin) return route.abort()
      if(!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(url.pathname)) return route.continue()
      let value={}
      if(url.pathname.startsWith('/api/wechat/')) {
        requests.push({path:url.pathname,method:request.method()})
        if(url.pathname==='/api/wechat/status') value={configured,enabled,connected:enabled,message:enabled?'微信已连接，仅接收扫码账号的私聊':'尚未连接微信'}
        if(url.pathname==='/api/wechat/login') value={login_id:'a'.repeat(32),status:'wait',qr_image:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5aIAAAAASUVORK5CYII='}
        if(url.pathname==='/api/wechat/login/status') {
          polls++
          const body=request.postDataJSON()
          if(body.verify_code==='123456'){configured=true;enabled=true;value={status:'confirmed'}}
          else value={status:'need_verifycode'}
        }
        if(url.pathname==='/api/wechat/connection') {
          if(request.method()==='DELETE'){configured=false;enabled=false}
          else enabled=request.postDataJSON().enabled
          value={configured,enabled,connected:enabled,message:enabled?'微信已连接':'微信已暂停'}
        }
      } else if(url.pathname==='/api/onboarding/status')value={completed:true}
      else if(url.pathname==='/api/agent/bootstrap')value={onboarding:{completed:true},conversation_id:'test',conversations:[],messages:[],models:[],diaries:[],qq:{},settings:{}}
      else if(url.pathname==='/api/companion/status')value={pet:{settings:{}},screen:{},settings:{}}
      else if(url.pathname==='/api/agent/messages'||/^\/(diaries|reviews|weekly)$/.test(url.pathname))value=[]
      else if(url.pathname.includes('startup-greeting'))value={enabled:false}
      else if(url.pathname.includes('/profile'))value={profile:{},settings:{}}
      else if(url.pathname==='/api/creation/configuration')value={environment:{},defaults:{}}
      await route.fulfill({json:value})
    })
    const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message))
    await page.goto(`${base}/?view=settings&settings-section=qq`)
    await page.getByRole('heading',{name:'微信与QQ',exact:true}).waitFor()
    await page.getByRole('button',{name:'扫码连接微信',exact:true}).click()
    await page.getByLabel('手机验证码',{exact:true}).waitFor()
    assert.equal(polls,1)
    assert.equal(await page.locator('.wechat-qr img').count(),1)
    await page.screenshot({path:path.join(output,'wechat-login.png')})
    await page.getByLabel('手机验证码',{exact:true}).fill('123456')
    await page.getByRole('button',{name:'确认验证码',exact:true}).click()
    await page.getByRole('button',{name:'暂停微信',exact:true}).waitFor()
    assert.equal(await page.locator('.wechat-qr').count(),0)
    await page.getByRole('button',{name:'暂停微信',exact:true}).click()
    await page.getByRole('button',{name:'恢复连接',exact:true}).waitFor()
    await page.getByRole('button',{name:'恢复连接',exact:true}).click()
    await page.getByRole('button',{name:'暂停微信',exact:true}).waitFor()
    for(const [theme,label,width] of [['xuan','宣纸',1100],['ink','墨夜',760]]) {
      await page.setViewportSize({width,height:900})
      await page.goto(`${base}/?view=settings&settings-section=appearance`)
      await page.locator('.theme-picker-option').filter({hasText:label}).click()
      await page.getByLabel('字体大小').selectOption('large')
      await page.locator('.settings-savebar').getByRole('button',{name:'保存',exact:true}).click()
      await page.goto(`${base}/?view=settings&settings-section=qq`)
      await page.getByRole('button',{name:'暂停微信',exact:true}).waitFor()
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme)
      assert.equal(await page.evaluate(()=>getComputedStyle(document.documentElement).colorScheme),theme==='ink'?'dark':'light')
      const box=await page.locator('.wechat-settings').boundingBox()
      assert.ok(box.x+box.width<=width+1)
      await page.screenshot({path:path.join(output,`wechat-${theme}.png`)})
    }
    await page.getByRole('button',{name:'解除本机绑定',exact:true}).click()
    await page.getByRole('button',{name:'扫码连接微信',exact:true}).waitFor()
    assert.equal(await page.getByRole('button',{name:'重新扫码绑定',exact:true}).count(),0)
    assert.deepEqual(errors,[])
    const report={qrLogin:true,verification:true,pauseResume:true,unbind:true,themes:2,errors,requests}
    await fs.writeFile(path.join(output,'wechat-ui.json'),JSON.stringify(report,null,2))
    console.log(JSON.stringify(report))
  } finally {await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
