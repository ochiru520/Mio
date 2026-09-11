const assert = require('node:assert/strict')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const browser = await chromium.launch({channel:'msedge',headless:true})
  const page = await browser.newPage({viewport:{width:1280,height:900}})
  const errors=[]
  page.on('pageerror', e=>errors.push(e.message))
  await page.route('**/api/**', async route=>{
    const req=route.request(), path=new URL(req.url()).pathname
    let data
    if (req.method() !== 'GET') data={ok:true}
    else if (path==='/api/agent/bootstrap') {
      const response=await route.fetch()
      data=await response.json()
      data.conversations=[{id:'desktop_a',title:'合成会话甲',kind:'main'},{id:'desktop_b',title:'合成会话乙',kind:'main'}]
      data.conversation_id='desktop_a'; data.messages=[]; data.diaries=[]
    }
    else if (path.includes('/handoff/source/')) data={handoff:path.endsWith('/desktop_a')?{task_id:'task_a',conversation_id:'desktop_agent_a',status:'ready'}:null}
    else if (path==='/api/agent/conversations') data=[{id:'desktop_a',title:'合成会话甲',kind:'main'},{id:'desktop_b',title:'合成会话乙',kind:'main'}]
    else if (path==='/api/agent/messages') data=[]
    else if (path.includes('onboarding')) data={completed:true}
    if(data!==undefined) return route.fulfill({json:data})
    return route.continue()
  })
  try {
    await page.goto((process.env.MIO_UI_BASE||'http://127.0.0.1:1422/')+'?conversation_id=desktop_a')
    await page.getByRole('navigation',{name:'主要导航'}).getByRole('button',{name:'对话',exact:true}).click()
    await page.locator('.chat-handoff-notice').waitFor()
    await page.reload()
    await page.getByRole('navigation',{name:'主要导航'}).getByRole('button',{name:'对话',exact:true}).click()
    await page.locator('.chat-handoff-notice').waitFor()
    await page.mouse.move(900,600)
    await page.locator('.conversation-drawer-trigger').click()
    await page.getByRole('button').filter({hasText:'合成会话乙'}).click()
    await page.waitForFunction(()=>!document.querySelector('.chat-handoff-notice'))
    await page.locator('.conversation-drawer-trigger').click()
    await page.getByRole('button').filter({hasText:'合成会话甲'}).click()
    await page.locator('.chat-handoff-notice').waitFor()
    assert.deepEqual(errors,[])
    console.log('Handoff UI: reload, source switch and return passed; no model requests')
  } finally { await browser.close() }
}
main().catch(e=>{console.error(e);process.exit(1)})
