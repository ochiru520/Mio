/* Real Vue components, synthetic preview API; no model calls or user documents. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18472'
const output = process.env.MIO_UI_EVIDENCE || require('node:path').join(require('node:os').tmpdir(),'mio-file-preview-ui')
const image = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII='
async function main() {
  await fs.mkdir(output, { recursive:true })
  const browser = await chromium.launch({channel:'msedge',headless:true})
  const context = await browser.newContext({viewport:{width:1100,height:850}})
  const errors = [], downloads = [], checks = []
  let appearance = { hero_url:'/test-avatar.png', personal_url:'/test-avatar.png', hero_shape:'original' }
  let packageUrls = []
  await context.route('**/*', async route => {
    const request = route.request(), p = new URL(request.url()).pathname
    if (!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p)) return route.continue()
    let value = {}
    if (p === '/api/onboarding/status') value = {completed:true}
    else if (p === '/api/agent/bootstrap') value = {onboarding:{completed:true},conversation_id:'desktop_main_preview',conversations:[{id:'desktop_main_preview',kind:'main',title:'聊天'},{id:'desktop_agent_preview',kind:'agent',title:'预览测试'}],models:[],messages:[],diaries:[],settings:{},qq:{},model:{},provider_presets:[]}
    else if (p === '/api/agent/conversations') value = [{id:'desktop_main_preview',kind:'main',title:'聊天'},{id:'desktop_agent_preview',kind:'agent',title:'预览测试'}]
    else if (p === '/api/agent/models/catalog') value = {models:[],provider_presets:[],hidden_model_providers:[]}
    else if (p === '/api/agent/messages') value = [{id:1,role:'user',content:'请生成文档',created_at:'2026-10-11T12:00:00'},{id:2,role:'assistant',content:'文件已生成。',created_at:'2026-10-11T12:00:01',attachments:[{kind:'document',name:'附件文档.docx',artifact_id:'docx',url:'/api/artifacts/docx/download',size:123}]}]
    else if (p === '/api/artifacts') value = {artifacts:[{id:'docx',name:'归档文档.docx',url:'/api/artifacts/docx/download',version:1,size:123,mime_type:'application/octet-stream'}],has_more:false}
    else if (p === '/api/workspace') value = {projects:[],tasks:[]}
    else if (p === '/api/workspace/conversations') value = {projects:{},modes:{desktop_agent_preview:'work'}}
    else if (p === '/api/workspace/pending') value = {items:[],total:0}
    else if (p === '/api/agent/work') value = {tasks:[],limits:{}}
    else if (p.includes('/profile')) value = {profile:{},settings:{}}
    else if (p === '/api/companion/status') value = {pet:{settings:{}},screen:{},settings:{}}
    else if (p.includes('startup-greeting')) value = {enabled:false}
    else if (/^\/(diaries|reviews|weekly)$/.test(p)) value = []
    else if (p.endsWith('/preview')) {
      const id = p.split('/').at(-2)
      if (id === 'docx') value = { kind:'document',format:'docx',notice:'内容预览',blocks:[{type:'heading',level:1,text:'文档标题'},{type:'paragraph',text:'直接在右侧浏览的正文。\n第二段内容。'},{type:'table',rows:[['姓名','事项'],['澪','文件预览']]},{type:'image',url:image,alt:'文档图片'}] }
      else if (id === 'xlsx') value = { kind:'document',format:'xlsx',sheets:[{name:'第一张表',rows:[['项目','金额'],['Mio','125']],row_numbers:[1,3]},{name:'另一张表',rows:[['这里是另一工作表']]}] }
      else if (id === 'pptx') value = { kind:'document',format:'pptx',slides:[{number:1,blocks:[{type:'heading',text:'第一页标题'}]},{number:2,blocks:[{type:'paragraph',text:'第二页正文'}]}] }
      else if (id === 'image') value = {kind:'image',url:image,name:'图片预览'}
      else if (id === 'bad') return route.fulfill({status:422,json:{detail:'文档已损坏，请重新生成。'}})
    } else if (p === '/api/agent/surface') value = appearance
    else if (p.includes('/surface/images/')) {
      appearance = { ...appearance, [p.endsWith('hero') ? 'hero_url' : 'personal_url']:request.method() === 'DELETE' ? '' : '/test-avatar.png' }
      value = appearance
    } else if (p === '/api/dependencies') value = {dependencies:[{id:'genie_runtime',label:'Genie 本地语音运行引擎',kind:'script',status:'missing',offline_only:!packageUrls.length,what:'本地语音引擎',how:'安装引擎数据包'}]}
    else if (p === '/api/dependencies/genie_runtime/package' || p.endsWith('/package-source')) {
      if (request.method() === 'PUT') packageUrls = request.postDataJSON().urls
      value = {id:'genie_runtime',file_name:'engine.zip',size_bytes:231310729,sha256:'a'.repeat(64),urls:packageUrls,message:packageUrls.length ? '已配置下载地址' : '此版本尚未提供公开数据包下载地址。',release_url:'https://github.com/ochiru520/Mio/releases',license_notice:'分发范围分别确认'}
    }
    await route.fulfill({json:value})
  })
  await context.route('**/test-avatar.png*', async route => route.fulfill({contentType:'image/png',body:await fs.readFile(path.join(__dirname,'../public/mio-avatar.png'))}))
  const page = await context.newPage()
  page.on('pageerror',error=>errors.push(error.message))
  page.on('download',event=>downloads.push(event.suggestedFilename()))
  try {
    await page.goto(base+'/scripts/file-preview-ui.html')
    await page.getByText('直接在右侧浏览的正文。', {exact:false}).waitFor()
    assert.equal(await page.locator('.agent-office-preview table td').count(),4)
    await page.locator('.agent-office-preview img').waitFor()
    assert.equal(await page.getByRole('link',{name:'下载此文件'}).count(),0)
    await page.screenshot({path:path.join(output,'docx.png')})
    checks.push('Word正文、表格、嵌入图片在侧栏显示')
    await page.goto(base+'/scripts/file-preview-ui.html?id=xlsx')
    await page.getByRole('button',{name:'另一张表',exact:true}).click()
    await page.getByText('这里是另一工作表',{exact:true}).waitFor()
    await page.getByRole('button',{name:'第一张表',exact:true}).click()
    assert.equal(await page.locator('.agent-office-preview th').last().textContent(),'3')
    checks.push('Excel表格和工作表切换、稀疏行号正确')
    await page.goto(base+'/scripts/file-preview-ui.html?id=pptx')
    await page.getByText('第二页正文',{exact:true}).waitFor()
    checks.push('PPT按幻灯片浏览')
    await page.goto(base+'/scripts/file-preview-ui.html?id=bad')
    await page.getByRole('alert').getByText('文档已损坏，请重新生成。',{exact:true}).waitFor()
    checks.push('损坏文档显示实际错误')
    await page.goto(base+'/scripts/file-preview-ui.html?id=image')
    await page.getByAltText('图片预览').waitFor()
    await page.waitForFunction(()=>document.querySelector('.agent-media-preview')?.naturalWidth>0)
    checks.push('图片文件直接预览')
    await page.goto(base+'/scripts/agent-settings-preview.html?section=agent-images')
    await page.getByAltText('首页问候图片当前预览').waitFor()
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('.appearance-preview img')).every(img=>img.naturalWidth>0))
    assert.equal(await page.locator('.appearance-preview img').count(),2)
    await page.screenshot({path:path.join(output,'appearance.png')})
    await page.setViewportSize({width:600,height:850})
    await page.locator('.agent-appearance-settings').evaluate(el=>{if(el.scrollWidth>el.clientWidth+1)throw new Error('图片设置横向溢出')})
    checks.push('实际图片预览和窄屏布局')
    await page.goto(base+'/scripts/agent-settings-preview.html?view=dependencies')
    await page.getByRole('button',{name:'获取与安装',exact:true}).click()
    await page.getByText('此版本尚未提供公开数据包下载地址。',{exact:true}).waitFor()
    await page.getByText('已有下载链接',{exact:true}).click()
    await page.getByLabel('HTTPS 下载地址（每行一个）').fill('https://example.com/engine.zip')
    await page.getByRole('button',{name:'保存下载地址',exact:true}).click()
    await page.getByRole('button',{name:'一键安装',exact:true}).waitFor()
    assert.deepEqual(packageUrls,['https://example.com/engine.zip'])
    checks.push('获取说明及下载源保存联动安装入口')
    await page.setViewportSize({width:1280,height:850})
    const appBase = process.env.MIO_PRODUCTION_BASE || base
    await page.goto(appBase+'/?workspace=agent&conversation_id=desktop_agent_preview')
    await page.getByRole('button',{name:/附件文档.docx/}).click()
    await page.locator('.agent-file-preview').getByText('直接在右侧浏览的正文。',{exact:false}).waitFor()
    await page.screenshot({path:path.join(output,'integrated-side-preview.png')})
    checks.push('完整Agent页面点击消息附件打开侧栏正文，不触发下载')
    assert.deepEqual(errors,[])
    assert.deepEqual(downloads,[])
    await fs.writeFile(path.join(output,'receipt.json'),JSON.stringify({ok:true,checks,errors,downloads,scope:'synthetic API with actual Vue components'},null,2))
    console.log(JSON.stringify({ok:true,checks:checks.length,downloads:downloads.length}))
  } finally { await browser.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
