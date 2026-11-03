/* Real Vue UI with synthetic model metadata: no model, microphone or API access. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')

async function main() {
  const { createServer } = await import('vite')
  const server = await createServer({ server: { host: '127.0.0.1', port: 18476, strictPort: true } })
  await server.listen()
  const output = path.resolve(process.env.MIO_UI_EVIDENCE || '../验收与归档/普通用户完整方案-20261101/voice-ui')
  await fs.mkdir(output, { recursive: true })
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1152, height: 900 } })
  page.setDefaultTimeout(10000)
  const pageErrors = [], apiCalls = [], checks = [], screenshots = [], typography = []
  page.on('pageerror', e => pageErrors.push(e.message))
  await page.route('**/api/**', route => { apiCalls.push(route.request().url()); return route.abort() })
  try {
    await page.goto('http://127.0.0.1:18476/scripts/voice-engines-preview.html?settings-shell=1')
    await page.getByRole('heading', { name: '角色语音', exact: true }).waitFor()
    assert.equal(await page.locator('.connection-label').innerText(),'服务在线')
    await page.getByRole('button',{name:'试听',exact:true}).click()
    assert.equal(await page.evaluate(()=>window.voiceEngineFixture.tests.at(-1).profileId),'mio')
    checks.push('普通 Genie 音色可选择和试听，保留原有入口')
    await page.getByRole('combobox', { name: '语音模型', exact: true }).click()
    await page.getByRole('option', { name: 'GPT-SoVITS V2（流式，支持自训音色）', exact: true }).click()
    assert.equal(await page.evaluate(() => window.voiceEngineFixture.status.pet.settings.local_voice_runtime), 'mio_v2')
    assert.equal(await page.locator('.connection-label').innerText(), '待保存并验证')
    assert.equal(await page.getByRole('link', { name: 'Hugging Face 模型下载' }).getAttribute('href'), 'https://huggingface.co/lj1995/GPT-SoVITS')
    assert.equal(await page.getByRole('textbox', { name: 'GPT 权重完整路径（Mio 所在电脑）', exact: true }).isVisible(), false)
    checks.push('选择 V2 后不会借用旧 Genie 在线状态；官方模型来源可见，路径高级项默认收起')
    for (const width of [1152,1252,390]) {
      await page.setViewportSize({width,height:900})
      const sizes=await page.locator('.voice-profile-layout').evaluate(el=>({parent:el.getBoundingClientRect().width,editor:el.querySelector('.voice-profile-editor').getBoundingClientRect().width}))
      assert.ok(sizes.editor >= sizes.parent - 2, JSON.stringify(sizes))
      assert.equal(await page.locator('.voice-profile-list').count(),0)
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false)
      if(width>700){const rights=await page.locator('.voice-file-picker').evaluateAll(els=>els.map(el=>el.getBoundingClientRect().right));assert.ok(Math.max(...rights)-Math.min(...rights)<2)}
      assert.equal(await page.getByRole('combobox',{name:'当前音色',exact:true}).count(),1)
      const name=`original-mio-${width}.png`;await page.screenshot({path:path.join(output,name),fullPage:true});screenshots.push(name)
    }
    await page.getByRole('button',{name:'试听',exact:true}).click()
    assert.equal(await page.evaluate(()=>window.voiceEngineFixture.tests.at(-1).profileId),'mio')
    await page.getByText('添加自训音色',{exact:true}).click()
    await page.getByRole('textbox', { name: '新音色名称', exact: true }).fill('我的自训音色')
    await page.getByRole('button', { name: '添加自训 V2 音色', exact: true }).click()
    await page.getByText('待补充：GPT 权重、SoVITS 权重、参考音频、参考原文', { exact: true }).waitFor()
    assert.equal(await page.getByRole('button', { name: '保存并加载所选音色', exact: true }).isDisabled(), true)
    for (const [label, name] of [['GPT 训练文件（.ckpt）', 'my-voice.ckpt'], ['SoVITS 训练文件（.pth）', 'my-voice.pth'], ['参考音频（最多 40 MB）', 'reference.wav']]) {
      const file={ name, mimeType: 'application/octet-stream', buffer: Buffer.from('synthetic metadata only') }
      if(name==='my-voice.ckpt') {
        const chooser=page.waitForEvent('filechooser')
        await page.locator('.voice-file-picker').first().click()
        await (await chooser).setFiles(file)
      } else await page.getByLabel(label,{exact:true}).setInputFiles(file)
    }
    assert.deepEqual(await page.locator('.voice-current-file').allTextContents(),['my-voice.ckpt','my-voice.pth','reference.wav'])
    await page.getByRole('textbox', { name: '参考音频原文', exact: true }).fill('你好，这是我的参考音频。')
    assert.equal(await page.getByRole('button', { name: '保存并加载所选音色', exact: true }).isEnabled(), true)
    await page.getByRole('button', { name: '保存并加载所选音色', exact: true }).click()
    const id = await page.evaluate(() => window.voiceEngineFixture.status.pet.settings.default_voice_profile_id)
    assert.notEqual(id, 'mio')
    assert.deepEqual(await page.evaluate(() => window.voiceEngineFixture.uploads.map(x => x.kind)), ['gpt', 'sovits', 'reference'])
    assert.deepEqual(await page.evaluate(() => window.voiceEngineFixture.controls.at(-1)), { action: 'start', engine: 'gpt_sovits' })
    checks.push('普通流程新增自训音色、导入两份权重和参考音频、填写原文并保存加载；旧音色仍保留')
    await page.getByText('音色详情与高级编辑', { exact: true }).click()
    assert.equal(await page.getByRole('textbox', { name: 'GPT 权重完整路径（Mio 所在电脑）', exact: true }).inputValue(), 'D:/synthetic/my-voice.ckpt')
    await page.getByText('音色详情与高级编辑', { exact: true }).click()
    await page.evaluate(() => {
      const f=window.voiceEngineFixture,s=f.status.pet.settings,p=s.voice_profiles[s.default_voice_profile_id]
      f.status.voice_runtime={engine:'gpt_sovits',local_voice_runtime:'mio_v2',service_loading:true,active_profile_id:s.default_voice_profile_id,last_successful_profile_id:'mio'}
    })
    await page.getByText('正在加载与预热', { exact: true }).waitFor()
    await page.evaluate(() => {
      const f=window.voiceEngineFixture,s=f.status.pet.settings,p=s.voice_profiles[s.default_voice_profile_id]
      f.status.voice_runtime={engine:'gpt_sovits',local_voice_runtime:'mio_v2',service_running:true,model_version:'v2',active_profile_id:s.default_voice_profile_id,active_weights:{gpt:p.gpt_sovits_gpt_weights,sovits:p.gpt_sovits_sovits_weights},active_reference_audio:p.gpt_sovits_ref_audio,verified_reference_text:p.gpt_sovits_prompt_text,active_reference_language:p.gpt_sovits_prompt_language,last_successful_profile_id:s.default_voice_profile_id}
    })
    await page.getByText('服务在线', { exact: true }).waitFor()
    await page.getByText('正在使用：我的自训音色（v2）', { exact: true }).waitFor()
    await page.getByRole('button', { name: '保存并试听', exact: true }).click()
    assert.equal(await page.evaluate(() => window.voiceEngineFixture.tests.at(-1).profileId), id)
    checks.push('高级路径反映导入文件，加载和当前音色身份独立显示，试听使用已选音色')
    for (const width of [1152,1252,390]) {
      await page.setViewportSize({ width, height: 900 })
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth+2)
      assert.equal(overflow, false, `page overflows ${width}`)
      typography.push(await page.evaluate(() => ({ width: innerWidth, sizes: [...new Set([...document.querySelectorAll('.voice-settings-panel *')].filter(el => el.getClientRects().length && [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())).map(el => Number.parseFloat(getComputedStyle(el).fontSize)))].sort((a,b)=>a-b) })))
      const name=`custom-v2-${width}.png`;await page.screenshot({path:path.join(output,name),fullPage:true});screenshots.push(name)
    }
    await page.evaluate(() => {
      const s=window.voiceEngineFixture.status.pet.settings,base=s.voice_profiles.mio
      for(let i=1;i<=8;i++)s.voice_profiles[`choice-${i}`]={...base,name:`测试音色 ${i}`}
    })
    await page.getByRole('combobox',{name:'当前音色',exact:true}).click()
    await page.getByRole('option',{name:'测试音色 8',exact:true}).click()
    assert.equal(await page.getByRole('textbox',{name:'音色名称',exact:true}).inputValue(),'测试音色 8')
    await page.getByRole('button',{name:'设为默认',exact:true}).click()
    assert.equal(await page.evaluate(()=>window.voiceEngineFixture.status.pet.settings.default_voice_profile_id),'choice-8')
    for(const width of [1252,390]){
      await page.setViewportSize({width,height:900})
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2),false)
      const name=`multiple-voices-${width}.png`;await page.screenshot({path:path.join(output,name),fullPage:true});screenshots.push(name)
    }
    await page.getByRole('button',{name:'删除音色',exact:true}).click()
    assert.equal(await page.evaluate(()=>Boolean(window.voiceEngineFixture.status.pet.settings.voice_profiles['choice-8'])),false)
    checks.push('单音色表单全宽；多音色下拉可选、设默认与删除，不占固定侧栏')
    await page.evaluate(id => { window.voiceEngineFixture.status.pet.settings.default_voice_profile_id=id },id)
    await page.evaluate(() => Object.assign(window.voiceEngineFixture.status.voice_runtime,{service_running:false,last_successful_profile_id:'mio',managed_last_error:'模型版本不兼容，请选择 V2 权重'}))
    await page.getByText('模型版本不兼容，请选择 V2 权重', { exact: true }).waitFor()
    await page.getByRole('button', { name:'重新使用上次成功的音色', exact:true }).click()
    assert.equal(await page.evaluate(() => window.voiceEngineFixture.status.pet.settings.default_voice_profile_id),'mio')
    checks.push('1152/1252/390 无横向溢出；失败可查看原因并重新加载上次成功音色')
    await page.locator('.voice-engine-choice > button').filter({hasText:'AnifLive-TTS'}).click()
    assert.ok((await page.locator('.voice-settings-panel').innerText()).includes('正式精度检查未通过'))
    assert.equal(await page.getByRole('link',{name:'AnifLive-TTS 项目与模型说明'}).getAttribute('href'),'https://github.com/Hiruynk/AnifLive-TTS')
    await page.getByText('高级连接与音色设置',{exact:true}).click()
    await page.getByRole('textbox',{name:'模型包 ID',exact:true}).waitFor()
    const name='anif-experimental-390.png';await page.screenshot({path:path.join(output,name),fullPage:true});screenshots.push(name)
    checks.push('Anif 仍标实验，下载来源与高级连接可用')
    assert.deepEqual(pageErrors,[]);assert.deepEqual(apiCalls,[])
    for (const row of typography) { assert.ok(row.sizes.every(size => size >= 12), JSON.stringify(row)); assert.ok(row.sizes.length <= 4, JSON.stringify(row)) }
    checks.push('正文和辅助文字至少12px，最多四种字号，无横向溢出')
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({ok:true,checks,screenshots,pageErrors,apiCalls,typography,synthetic:true},null,2))
    console.log(`Custom V2 UI passed: ${checks.length} groups, ${screenshots.length} screenshots, no APIs or models.`)
  } finally {await browser.close();await server.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1})
