/* Real Vue boundaries with synthetic transport and a fresh browser context. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18476'
async function main() {
  const preview = await (await fetch(base + '/scripts/agent-experience-preview.html')).text()
  const proxy = preview.match(/src="([^"]*html-proxy[^"]*)"/)?.[1]
  const source = proxy ? await (await fetch(base + proxy)).text() : preview
  const vue = source.match(/from\s*['"]([^'"]*\/vue\.js[^'"]*)['"]/)[1]
  const html = `<!doctype html><html><meta charset="utf-8"><body><div id="app"></div><script type="module">
  import {createApp,h,ref} from '${vue}'
  import Exercise from '/src/components/LearningExercise.vue'
  import Panel from '/src/components/AgentLearningPanel.vue'
  import Notifications from '/src/components/AgentNotifications.vue'
  createApp({setup(){const project=ref('p'),visible=ref(true),session=ref(null),target=ref(null),count=ref(0);const mode=new URLSearchParams(location.search).get('mode')||'exercise';
  async function read(){session.value=await(await fetch('/api/workspace/projects/'+project.value+'/learning/s')).json()}; if(mode==='exercise')void read();
  return()=>h('main',[h('button',{onClick:()=>visible.value=!visible.value},visible.value?'关闭组件':'重开组件'),h('button',{onClick:async()=>{project.value=project.value==='p'?'p2':'p';if(mode==='exercise')await read()}},'切换项目'),
  visible.value&&mode==='exercise'&&session.value?h(Exercise,{projectId:project.value,session:session.value}):null,
  visible.value&&mode==='panel'?h(Panel,{projectId:project.value}):null,
  visible.value&&mode==='notifications'?h(Notifications,{onOpen:value=>target.value=value,onCount:value=>count.value=value}):null,
  h('pre',{'data-testid':'target'},JSON.stringify(target.value)),h('pre',{'data-testid':'count'},String(count.value))])}}).mount('#app')
  </script></body></html>`
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const context = await browser.newContext(), page = await context.newPage(), errors = [], writes = [], presented = [], dismissed = [], checks = []
  page.on('pageerror', error => errors.push(error.message))
  let fail = false, late = null, releaseLate, staleList = null, releaseList
  const drafts = { p: { revision: 0, answers: {}, flagged_item_ids: [], active_item_id: 'q1' }, p2: { revision: 0, answers: {}, flagged_item_ids: [], active_item_id: 'q1' } }
  const session = project => ({ id: 's', title: project + '学习', goal: '合成草稿', draft: drafts[project], items: [{ id: 'q1', prompt: project + '第一题' }, { id: 'q2', prompt: project + '第二题' }], progress: { answered: 0, total: 2 } })
  await context.route('**/async-boundary-fixture**', route => route.fulfill({ contentType: 'text/html', body: html }))
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), p = url.pathname
    const project = p.match(/projects\/(p2|p)\//)?.[1]
    if (p.endsWith('/draft')) {
      const body = request.postDataJSON(); writes.push({ project, body })
      if (fail || body.expected_revision !== drafts[project].revision) return route.fulfill({ status: 409, json: { detail: '合成草稿冲突' } })
      drafts[project] = { ...body, revision: drafts[project].revision + 1 }
      const accepted = drafts[project]
      if (late === project) { late = null; await new Promise(resolve => { releaseLate = resolve }) }
      return route.fulfill({ json: accepted })
    }
    if (p.endsWith('/learning/s')) return route.fulfill({ json: session(project) })
    if (p.endsWith('/learning')) {
      const value = { sessions: [session(project)] }
      if (staleList === project) { staleList = null; await new Promise(resolve => { releaseList = resolve }) }
      return route.fulfill({ json: value })
    }
    if (p.endsWith('/notifications')) return route.fulfill({ json: { notifications: [
      { id: 1, task_id: 't1', conversation_id: 'c1', title: '第一任务提醒', actionable: true, target: { task_id: 'wrong', conversation_id: 'wrong' } },
      { id: 1, task_id: 't1', conversation_id: 'c1', title: '第一任务提醒', actionable: true },
      { id: 1, task_id: 't2', conversation_id: 'c2', title: '第二任务提醒', actionable: true },
    ] } })
    if (p.endsWith('/present')) { presented.push(p); return route.fulfill({ json: { presented: true } }) }
    if (p.endsWith('/dismiss')) { dismissed.push(p); return route.fulfill({ json: { dismissed: true } }) }
    return route.fulfill({ json: {} })
  })
  try {
    await page.goto(base + '/async-boundary-fixture')
    fail = true; await page.getByLabel('本题草稿答案').fill('保存失败后必须恢复')
    await page.getByRole('alert').filter({ hasText: '合成草稿冲突' }).waitFor()
    await page.getByRole('button', { name: '关闭组件', exact: true }).click(); await page.getByRole('button', { name: '重开组件', exact: true }).click()
    assert.equal(await page.getByLabel('本题草稿答案').inputValue(), '保存失败后必须恢复')
    await page.reload(); assert.equal(await page.getByLabel('本题草稿答案').inputValue(), '保存失败后必须恢复')
    fail = false; await page.getByRole('button', { name: '保存恢复的草稿', exact: true }).click(); await page.getByRole('status').filter({ hasText: '草稿已保存' }).waitFor()
    assert.equal(await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith('mio:learning-draft:')).length), 0)
    checks.push('失败草稿卸载与整页重载恢复，成功清理临时副本')

    drafts.p = { ...drafts.p, answers: { ...drafts.p.answers, q2: '远端第二题' }, revision: drafts.p.revision + 1 }
    await page.getByLabel('本题草稿答案').fill('本地第一题修改'); await page.getByRole('alert').filter({ hasText: '合成草稿冲突' }).waitFor()
    await page.getByRole('button', { name: '读取最新版本并保留本地修改后保存', exact: true }).click()
    await page.getByRole('status').filter({ hasText: '草稿已保存' }).waitFor()
    assert.equal(drafts.p.answers.q1, '本地第一题修改'); assert.equal(drafts.p.answers.q2, '远端第二题')
    checks.push('冲突显式合并保留本地改题与远端其他答案')

    late = 'p'; await page.getByLabel('本题草稿答案').fill('旧项目保存中')
    await page.waitForFunction(() => document.body.textContent.includes('保存草稿…'))
    while (!releaseLate) await new Promise(resolve => setTimeout(resolve, 20))
    await page.getByRole('button', { name: '切换项目', exact: true }).click(); await page.getByText('p2第一题', { exact: true }).waitFor()
    await page.getByLabel('本题草稿答案').fill('新项目独立答案'); releaseLate()
    await page.getByRole('status').filter({ hasText: '草稿已保存' }).waitFor()
    assert.equal(drafts.p2.answers.q1, '新项目独立答案'); assert.equal(drafts.p2.answers.q2, undefined); assert.equal(drafts.p.answers.q1, '旧项目保存中')
    checks.push('项目切换时旧保存回执不覆盖新项目状态')

    staleList = 'p'; await page.goto(base + '/async-boundary-fixture?mode=panel')
    while (!releaseList) await new Promise(resolve => setTimeout(resolve, 20))
    await page.getByRole('button', { name: '切换项目', exact: true }).click(); await page.getByRole('button', { name: /p2学习/ }).waitFor(); releaseList()
    await page.getByRole('button', { name: /p2学习/ }).click(); await page.getByText('p2第一题', { exact: true }).waitFor()
    fail = true; await page.getByLabel('本题草稿答案').fill('切页失败保留'); await page.getByRole('alert').filter({ hasText: '合成草稿冲突' }).waitFor()
    await page.getByRole('button', { name: '复习', exact: true }).click(); await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent === '复习' && !button.disabled) && document.body.textContent.includes('保存失败')); assert.equal(await page.getByLabel('本题草稿答案').inputValue(), '切页失败保留')
    fail = false; await page.getByRole('button', { name: '重试保存', exact: true }).click(); await page.getByRole('status').filter({ hasText: '草稿已保存' }).waitFor()
    await page.getByRole('button', { name: '复习', exact: true }).click(); await page.getByRole('button', { name: '学习会话', exact: true }).click()
    assert.equal(await page.getByLabel('本题草稿答案').inputValue(), '切页失败保留')
    checks.push('迟到项目列表不覆盖，保存失败阻止学习页内切页，成功返回刷新草稿')

    await page.goto(base + '/async-boundary-fixture?mode=notifications')
    await page.getByTestId('count').filter({ hasText: '2' }).waitFor(); await page.getByRole('button', { name: '任务消息', exact: true }).click()
    assert.equal(await page.getByRole('button', { name: '第一任务提醒', exact: true }).count(), 1)
    await page.getByRole('button', { name: '第一任务提醒', exact: true }).click(); assert.deepEqual(JSON.parse(await page.getByTestId('target').textContent()), { task_id: 't1', conversation_id: 'c1' })
    await page.getByRole('button', { name: '任务消息', exact: true }).click(); await page.getByRole('button', { name: '隐藏消息', exact: true }).first().click()
    await page.getByTestId('count').filter({ hasText: '1' }).waitFor(); await page.waitForTimeout(6200)
    assert.equal(await page.getByRole('button', { name: '第一任务提醒', exact: true }).count(), 0)
    assert.equal(presented.length, 2); assert.equal(dismissed.length, 1)
    checks.push('通知同任务去重/跨任务同ID保留，展示一次、隐藏不复现、点击归属正确')
    assert.deepEqual(errors, [])
    const output = path.join(os.tmpdir(), 'mio-learning-notifications-ui-20261026'); await fs.mkdir(output, { recursive: true })
    await fs.writeFile(path.join(output, 'result.json'), JSON.stringify({ checks, errors }, null, 2)); console.log(JSON.stringify({ checks, errors, output }))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
