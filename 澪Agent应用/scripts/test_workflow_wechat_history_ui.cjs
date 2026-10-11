const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:1439'
const output = path.resolve(process.env.MIO_UI_EVIDENCE || 'ui-evidence')
const wxId = 'wechat_private_' + 'a'.repeat(24)
const agentId = 'desktop_agent_fixture'
const deferred = process.env.MIO_UI_DEFER_QWEN === '1'
const workflows = [
  {id:'anima-2.9b-image',label:'Anima 2.9B 图片',filename:'无敌图片.json',media_type:'image',available:true},
  {id:'qwen-2.1-image',label:'千问 Qwen 2.1 图片',filename:'千问生图.json',media_type:'image',available:!deferred,pending_reason:deferred?'千问工作流正在调整，待定稿并重新验收后启用。':''},
  {id:'minimax-h3-video',label:'MINIMAX H3 双采样 · 8G 视频',filename:'MINIMAX-H3双采样_8G显存版.json',media_type:'video',available:true},
]
const defaults = {image_workflow_id:workflows[0].id,video_workflow_id:workflows[2].id}
const video = {id:'job_fixture',status:'completed',progress:1,media_type:'video',backend:'comfyui',spec:{prompt:'The cat gently blinks.',frame_count:124,fps:24,upscale_factor:1.5,workflow_snapshot:{source_file:workflows[2].filename,source_sha256:'a'.repeat(64),samplers:[{node_type:'SamplerCustomAdvanced'},{node_type:'SamplerCustomAdvanced'}]}}}
function message(id,role,content,extra={}) {return {id,role,content,source:'wechat',created_at:`2026-09-25T18:0${id}:00+08:00`,attachments:[],...extra}}
const history = [
  message(1,'user','/任务 帮助'),
  message(2,'assistant','可以在微信里交给我一个任务。',{delivery_status:'sent'}),
  message(3,'user','微信文件：需求.txt',{attachments:[{kind:'document',name:'需求.txt',size:50,url:'/api/wechat/attachments/fixture'}]}),
  message(4,'assistant','任务结果正在等待发送。',{delivery_status:'pending'}),
  message(5,'assistant','这次发送尚未确认。',{delivery_status:'unknown'}),
  message(6,'user','在电脑上查看同一段记录。',{source:'desktop'}),
]
const agentMessages = [message(1,'user','把这张猫的图片做成视频。',{source:'desktop'}),message(2,'assistant','视频已生成。',{source:'creation',tool_receipts:[{tool_name:'comfyui_generate_video',status:'completed',result:{job:video}}],attachments:[{kind:'video',url:'/fixture.mp4',name:'猫咪.mp4'}]})]
async function main() {
  await fs.mkdir(output,{recursive:true})
  const browser=await chromium.launch({channel:'msedge',headless:true})
  const errors=[]
  try {
    for(const [theme,width] of [['xuan',1100],['ink',760]]) {
      const context=await browser.newContext({viewport:{width,height:920}})
      await context.addInitScript(theme=>{localStorage.setItem('mio_app_preferences',JSON.stringify({theme,font_size:'large'}));localStorage.setItem('mio_agent_settings_section','workflows');localStorage.setItem('mio_agent_active_view',new URLSearchParams(location.search).get('view') || 'chat')},theme)
      const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message))
      await context.route('**/*',async route=>{
        const url=new URL(route.request().url()), p=url.pathname
        if(url.origin!==new URL(base).origin)return route.abort()
        if(p==='/fixture.mp4')return route.fulfill({contentType:'video/mp4',body:await fs.readFile(process.env.MIO_UI_VIDEO)})
        if(!/^\/(api|diaries|reviews|weekly|stats|onebot)(\/|$)/.test(p))return route.continue()
        let value={}
        const conversations=[{id:wxId,title:'微信对话',kind:'wechat',preview:'最近的微信任务记录'},{id:agentId,title:'Agent 创作对话',kind:'agent'}]
        if(p==='/api/onboarding/status')value={completed:true}
        else if(p==='/api/agent/bootstrap')value={onboarding:{completed:true},conversation_id:wxId,conversations,messages:history,models:[],diaries:[],qq:{},settings:{}}
        else if(p==='/api/agent/conversations')value=conversations
        else if(p==='/api/agent/messages')value=url.searchParams.get('conversation_id')===agentId?agentMessages:history
        else if(p==='/api/creation/bootstrap')value={comfyui:{reachable:true},workflows,defaults,presets:[],assets:[],jobs:[],remote_providers:[]}
        else if(p==='/api/creation/configuration')value={environment:{comfyui_root:'D:\\AI\\ComfyUI',comfyui_base_url:'http://127.0.0.1:8188'},defaults}
        else if(p==='/api/creation/preflight')value={ok:!deferred,workflows:workflows.map(w=>({...w,status:w.pending_reason?'pending_review':'ready',errors:w.pending_reason?[w.pending_reason]:[]}))}
        else if(p==='/api/companion/status')value={pet:{settings:{}},screen:{},settings:{}}
        else if(p.includes('startup-greeting'))value={enabled:false}
        else if(p.includes('/profile'))value={profile:{},settings:{}}
        else if(/^\/(diaries|reviews|weekly)$/.test(p))value=[]
        else if(p.endsWith('/tasks'))value={tasks:[]}
        await route.fulfill({json:value})
      })
      await page.goto(`${base}/?view=chat&conversation_id=${wxId}`)
      await page.getByText('微信送达结果待核对',{exact:true}).waitFor()
      assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme)
      assert.equal(await page.getByText('等待发送到微信',{exact:true}).count(),1)
      assert.equal(await page.getByText('微信已接收',{exact:true}).count(),1)
      assert.ok(await page.locator('.message-heading small').filter({hasText:/^微信$/}).count()>0)
      assert.equal(await page.locator('.message-heading small').filter({hasText:/^电脑$/}).count(),1)
      assert.equal(await page.getByRole('link',{name:/需求.txt/}).getAttribute('href'),'/api/wechat/attachments/fixture')
      await page.getByRole('button',{name:'微信对话',exact:true}).click()
      await page.locator('.conversation-drawer').getByRole('button',{name:/微信对话/}).waitFor()
      await page.screenshot({path:path.join(output,`history-list-${theme}.png`)})
      await page.locator('.conversation-drawer').getByRole('button',{name:/微信对话/}).click()
      await page.locator('.conversation-drawer').waitFor({state:'hidden'})
      await page.screenshot({path:path.join(output,`history-${theme}.png`)})
      await page.goto(`${base}/?workspace=agent&view=chat&conversation_id=${agentId}`)
      await page.locator('video').waitFor()
      await page.waitForFunction(()=>document.querySelector('video').readyState>=1)
      await page.locator('video').evaluate(v=>{v.currentTime=2})
      await page.waitForFunction(()=>document.querySelector('video').readyState>=2)
      assert.equal(await page.locator('video').getAttribute('controls'),'')
      const metadata=await page.locator('video').evaluate(v=>({duration:v.duration,width:v.videoWidth,height:v.videoHeight}))
      assert.ok(metadata.duration>5 && metadata.width>0)
      await page.getByText(/MINIMAX-H3双采样_8G显存版.json/).waitFor()
      await page.getByText(/双采样.*124 帧/).waitFor()
      await page.screenshot({path:path.join(output,`video-${theme}.png`)})
      await page.goto(`${base}/?workspace=agent&view=settings`)
      await page.getByRole('navigation',{name:'设置分类'}).getByRole('button',{name:'工作流',exact:true}).click()
      assert.equal(await page.getByLabel('图片生成',{exact:true}).locator('option').count(),2)
      assert.equal(await page.getByLabel('视频生成',{exact:true}).locator('option').count(),1)
      assert.equal(await page.locator('option[value="qwen-2.1-image"]').evaluate(el=>el.disabled),deferred)
      await page.getByRole('button',{name:'检查依赖',exact:true}).click()
      await page.getByRole('status').waitFor()
      if(deferred)await page.getByText(workflows[1].pending_reason,{exact:true}).waitFor()
      assert.equal(await page.getByText('检查通过',{exact:true}).count(),deferred?2:3)
      assert.equal(await page.evaluate(()=>[...document.querySelectorAll('.agent-settings-body,.settings-window-navigation')].some(e=>e.scrollWidth>e.clientWidth+2)),false)
      await page.screenshot({path:path.join(output,`workflows-${theme}.png`)})
      await page.getByText(workflows[1].label,{exact:true}).last().scrollIntoViewIfNeeded()
      await page.screenshot({path:path.join(output,`workflow-status-${theme}.png`)})
      await context.close()
    }
    assert.deepEqual(errors,[])
    const report={history:true,deliveryStates:true,sourceLabels:true,attachmentLink:true,videoMetadata:true,twoImagesOneVideo:true,qwenDeferred:deferred,themes:2,errors}
    await fs.writeFile(path.join(output,'workflow-history-ui.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report))
  } finally {await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1})
