/* Real Vue selection, asynchronous responses and pagination; synthetic API only. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18526'
const output = process.env.MIO_UI_EVIDENCE || path.join(os.tmpdir(), 'mio-reference-fixes-ui-20261026')
async function main() {
  const preview = await (await fetch(base + '/scripts/agent-experience-preview.html')).text()
  const proxy = preview.match(/src="([^"]*html-proxy[^"]*)"/)?.[1]
  const source = proxy ? await (await fetch(base + proxy)).text() : preview
  const vue = source.match(/from\s*['"]([^'"]*\/vue\.js[^'"]*)['"]/)[1]
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="app"></div><script type="module">
  import {createApp,h,ref} from '${vue}';import References from '/src/components/ComposerReferences.vue';
  createApp({setup(){const project=ref('p1'),conversation=ref('c1'),draft=ref('旧项目 @file'),items=ref([]),visible=ref(true);
  return()=>h('main',{style:'margin-top:600px'},[h('div',{style:'position:fixed;top:20px;right:20px;z-index:1000'},[
    h('button',{onClick:()=>{project.value='p2';draft.value='新项目 @新草稿'}},'切换项目'),
    h('button',{onClick:()=>{conversation.value='c2'}},'切换同项目会话'),
    h('button',{onClick:()=>draft.value='选择时继续编辑的内容'},'修改草稿'),h('button',{onClick:()=>visible.value=false},'卸载引用组件')]),
    visible.value?h(References,{enabled:true,projectId:project.value,conversationId:conversation.value,draft:draft.value,items:items.value,
      onAdd:item=>items.value.push(item),onReplaceMention:()=>draft.value=draft.value.replace(/(^|\\s)@([^@\\n]*)$/,'$1')}):null,
    h('pre',{'data-testid':'state'},JSON.stringify({project:project.value,conversation:conversation.value,draft:draft.value,items:items.value}))])}}).mount('#app')
  </script></body></html>`
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext({viewport:{width:1100,height:850}}), page = await context.newPage()
  const errors = [], checks = [], pageOffsets = []
  let release, deferred = true
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/reference-fix-fixture', route => route.fulfill({ contentType: 'text/html', body: html }))
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url())
    if (url.pathname.endsWith('/references/candidates')) {
      const offset = Number(url.searchParams.get('offset') || 0), query = url.searchParams.get('query') || ''
      pageOffsets.push({offset,query,kind:url.searchParams.get('kind'),project:url.searchParams.get('project_id')})
      const all = Array.from({length:42},(_,i)=>({kind:'file',target:`p1/file${i}.txt`,name:i===0?'旧项目资料':i===41?'末页资料':`分页资料${i}`})).filter(item=>!query || item.name.includes(query))
      const candidates = all.slice(offset,offset+40), has_more = all.length>offset+40
      return route.fulfill({json:{candidates,offset,limit:40,has_more,next_offset:has_more?offset+candidates.length:null}})
    }
    if (url.pathname.endsWith('/references/resolve')) {
      if (deferred) await new Promise(resolve => { release = resolve })
      const selected = request.postDataJSON().references[0]
      return route.fulfill({json:{resolved:[{...selected,version:'v1',excerpt:'合成资料'}],blocked:[]}})
    }
    return route.fulfill({json:{}})
  })
  async function startSelection() {
    release = null; deferred = true
    await page.goto(base+'/reference-fix-fixture')
    await page.getByRole('button',{name:'@ 引用文件、目录或会话',exact:true}).click()
    await page.getByRole('button',{name:'旧项目资料',exact:true}).click()
    await page.getByText('正在读取…',{exact:true}).waitFor()
    assert.equal(typeof release,'function')
  }
  async function finishSelection() {
    const response = page.waitForResponse(value=>value.url().endsWith('/references/resolve'))
    release(); await response
    await page.evaluate(()=>new Promise(requestAnimationFrame))
    return JSON.parse(await page.getByTestId('state').textContent())
  }
  try {
    await startSelection();await page.getByRole('button',{name:'切换项目',exact:true}).click()
    let state = await finishSelection()
    assert.deepEqual(state.items,[]);assert.equal(state.draft,'新项目 @新草稿')
    checks.push('切项目时迟到引用不添加、不改新草稿')
    await startSelection();await page.getByRole('button',{name:'切换同项目会话',exact:true}).click()
    state = await finishSelection();assert.deepEqual(state.items,[]);assert.equal(state.draft,'旧项目 @file')
    checks.push('同项目切会话也使旧选择失效')
    await startSelection();await page.getByRole('button',{name:'修改草稿',exact:true}).click()
    state = await finishSelection();assert.deepEqual(state.items,[]);assert.equal(state.draft,'选择时继续编辑的内容')
    checks.push('选择期间继续输入时，旧结果不改当前草稿')
    await startSelection();await page.getByRole('button',{name:'关闭',exact:true}).click()
    state = await finishSelection();assert.deepEqual(state.items,[])
    checks.push('关闭选择器后迟到解析不回写')
    await startSelection();await page.getByRole('button',{name:'卸载引用组件',exact:true}).click()
    state = await finishSelection();assert.deepEqual(state.items,[])
    checks.push('卸载组件后迟到解析不回写')
    deferred = false
    await page.goto(base+'/reference-fix-fixture')
    await page.getByRole('button',{name:'@ 引用文件、目录或会话',exact:true}).click()
    await page.getByRole('button',{name:'加载更多引用',exact:true}).click()
    await page.getByRole('button',{name:'末页资料',exact:true}).waitFor()
    assert.equal(await page.locator('.reference-candidates>div').count(),42)
    assert.equal(await page.getByRole('button',{name:'加载更多引用',exact:true}).count(),0)
    await page.getByRole('searchbox',{name:'搜索引用'}).fill('末页')
    await page.getByRole('button',{name:'末页资料',exact:true}).waitFor()
    assert.equal(await page.locator('.reference-candidates>div').count(),1)
    assert.equal(pageOffsets.at(-1).offset,0)
    await page.getByRole('button',{name:'末页资料',exact:true}).click()
    await page.getByRole('button',{name:'@末页资料',exact:true}).waitFor()
    state = JSON.parse(await page.getByTestId('state').textContent())
    assert.equal(state.items[0].target,'p1/file41.txt');assert.equal(state.draft,'旧项目 ')
    checks.push('候选加载第二页、搜索重置分页、正常选择保留版本')
    assert.deepEqual(errors,[])
    await fs.mkdir(output,{recursive:true})
    await page.screenshot({path:path.join(output,'reference-fixed.png')})
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({checks,errors,pageOffsets},null,2))
    console.log(JSON.stringify({checks,errors,output}))
  } finally { await browser.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
