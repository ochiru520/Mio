/* Actual Vue controls, synthetic settings and local assets only. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises'), path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.MIO_UI_BASE || 'http://127.0.0.1:18527'
const output = process.env.MIO_UI_EVIDENCE

async function main() {
  const proxyPage = await (await fetch(base + '/scripts/agent-experience-preview.html')).text()
  const proxy = proxyPage.match(/src="([^"]*html-proxy[^"]*)"/)?.[1]
  const source = proxy ? await (await fetch(base + proxy)).text() : proxyPage
  const vue = source.match(/from\s*['"]([^'"]*\/vue\.js[^'"]*)['"]/)[1]
  const html = `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body><div id="app"></div><script type="module">
  import {createApp,h,ref,reactive,provide} from '${vue}';
  import Settings from '/src/components/SettingsPage.vue';
  import Select from '/src/components/SelectField.vue';
  import Pet from '/src/components/PetAppearancePanel.vue';
  import References from '/src/components/ComposerReferences.vue';
  import '/src/styles/tokens.css';import '/src/styles/base.css';import '/src/styles.css';
  import '/src/styles/integrated.css';import '/src/styles/agent-surface.css';
  const settings=reactive({activeSettingsSection:'general',activeSettingsItem:{label:'基础与启动',description:'启动、后台与主动消息'},
    settingsFeedback:{section:'',type:''},runtimeSettingsReady:true,runtimeSettingsBusy:false,
    runtimeSettingsDraft:{},isSettingsSectionDirty:()=>true,mioDisplayName:'合成Mio',startupGreetingEnabled:false,
    desktopPreferencesDraft:{},desktopPreferencesReady:true,appPreferencesDraft:{default_open_page:'home'},
    desktopPreferencesBusy:false,qqStartupEnabled:false,qqStartupBusy:false,reloadRuntimeSettings:()=>{window.reloads++},
    resetActiveSettings:()=>{settings.appPreferencesDraft.default_open_page='home'},saveActiveSettings:()=>{window.saves++}});
  window.reloads=0;window.saves=0;window.fixture=settings;
  createApp({setup(){provide('mio-settings-page',settings);const number=ref(2),text=ref('a'),required=ref(''),draft=ref(''),old=ref(false),pet=ref({pet:{running:true,startup:{state:'failed',message:'桌宠模型加载失败：合成缺失'},log_path:'合成目录/desktop-pet.log',settings:{},live2d:{models:[]}}});
    window.setDraft=value=>draft.value=value;
    return()=>h('div',{class:'mio-settings-window settings-window-shell',style:'display:block;height:100vh;overflow:auto'},[
      h('main',{class:'mio-settings-content'},[h(Settings)]),
      h('form',{id:'required-form',onSubmit:e=>{e.preventDefault();window.formSubmitted=true}},[
        h(Select,{modelValue:required.value,'onUpdate:modelValue':v=>required.value=v,required:true,'aria-label':'必填选项'},()=>[h('option',{value:''},'请选择'),h('option',{value:'ok'},'有效选项')]),
        h('button',{type:'submit'},'提交合成表单')]),
      h('div',{class:'companion-workspace',style:'width:400px'},[h('div',{class:'observer-select-row'},[
        h(Select,{value:'window','aria-label':'观察窗口'},()=>[h('option',{value:'window'},'合成窗口选项')])])]),
      h('section',{id:'extra',style:'margin:20px;width:300px;height:200px;overflow:hidden'},[
        h(Select,{modelValue:number.value,'onUpdate:modelValue':v=>number.value=v,modelModifiers:{number:true},'aria-label':'数字选项',onChange:e=>window.numberChange={value:e.target.value,type:typeof number.value}},
          ()=>[h('option',{value:1},'一'),h('optgroup',{label:'分组'},[h('option',{value:2},'二'),h('option',{value:3,disabled:true},'不可用')])]),
        h('output',{id:'number'},String(number.value)),
        h(Select,{value:text.value,'aria-label':'事件选项',onChange:e=>text.value=e.target.value},()=>[h('option',{value:'a'},'甲'),h('option',{value:'b'},'乙')]),
        h('output',{id:'text'},text.value),
        h(References,{draft:draft.value,items:old.value?[{kind:'file',target:'old',name:'历史引用'}]:[]}),
        h('button',{onClick:()=>old.value=true},'显示历史引用')]),
      h(Pet,{status:pet.value,avatarUrl:'',controlMode:true}),
    ])}}).mount('#app');</script></body></html>`
  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1040, height: 760 } })
  const errors = [], checks = [], apiCalls = []
  page.on('pageerror', e => errors.push(e.message))
  await page.route('**/startup-settings-fixture', route => route.fulfill({ contentType: 'text/html', body: html }))
  await page.route('**/api/**', route => { apiCalls.push(route.request().url()); return route.fulfill({json:{}}) })
  try {
    await page.goto(base + '/startup-settings-fixture')
    await page.getByRole('heading', { name: '基础与启动' }).waitFor()
    for (const width of [1040, 760, 540]) {
      await page.setViewportSize({ width, height: 760 })
      await page.locator('.settings-header-actions').evaluate(async () => { await new Promise(requestAnimationFrame) })
      const boxes = await page.locator('.settings-header-actions button').evaluateAll(nodes => nodes.map(n => {const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}}))
      assert.equal(boxes.length, 3)
      for(let i=0;i<boxes.length;i++)for(let j=i+1;j<boxes.length;j++) {
        const a=boxes[i],b=boxes[j];assert(!(a.x<b.right && a.right>b.x && a.y<b.bottom && a.bottom>b.y),`overlap width ${width}`)
      }
      await page.getByRole('button',{name:'重新读取',exact:true}).click()
      await page.getByRole('button',{name:'保存',exact:true}).click()
      checks.push(`settings header ${width}: no overlaps and buttons clickable`)
    }
    await page.setViewportSize({width:1040,height:760})
    await fs.mkdir(output,{recursive:true})
    await page.screenshot({path:path.join(output,'settings-header.png')})
    const selector=page.getByRole('combobox',{name:'默认打开页面'})
    await selector.click()
    await page.screenshot({path:path.join(output,'settings-dropdown.png')})
    await page.getByRole('option',{name:'对话',exact:true}).click()
    assert.equal(await page.evaluate(()=>window.fixture.appPreferencesDraft.default_open_page),'chat')
    await page.getByRole('button',{name:'取消',exact:true}).click()
    await selector.getByText('首页',{exact:true}).waitFor()
    await selector.focus();await selector.press('ArrowDown');await selector.press('End');await selector.press('Enter')
    assert.equal(await page.evaluate(()=>window.fixture.appPreferencesDraft.default_open_page),'companion')
    checks.push('settings dropdown pointer, keyboard, reactive cancel')
    const numeric=page.getByRole('combobox',{name:'数字选项'})
    await numeric.click()
    await page.getByRole('option',{name:'一',exact:true}).click()
    assert.deepEqual(await page.evaluate(()=>window.numberChange),{value:'1',type:'number'})
    await numeric.click();await page.getByRole('option',{name:'分组 · 不可用'}).click({force:true});await page.keyboard.press('Escape')
    assert.equal(await page.locator('#number').textContent(),'1')
    checks.push('number modifier, grouped and disabled choices')
    await page.getByRole('combobox',{name:'事件选项'}).click()
    await page.getByRole('option',{name:'乙',exact:true}).click()
    assert.equal(await page.locator('#text').textContent(),'b')
    checks.push('value/change event retains real select target')
    await page.getByRole('button',{name:'提交合成表单'}).click()
    assert.equal(await page.evaluate(()=>Boolean(window.formSubmitted)),false)
    await page.getByRole('alert').filter({hasText:'请选择一个选项'}).waitFor()
    assert.equal(await page.getByRole('combobox',{name:'必填选项'}).getAttribute('aria-required'),'true')
    assert.equal(await page.getByRole('combobox',{name:'必填选项'}).getAttribute('aria-invalid'),'true')
    await page.getByRole('combobox',{name:'必填选项'}).click()
    await page.getByRole('option',{name:'有效选项',exact:true}).click()
    await page.getByRole('button',{name:'提交合成表单'}).click()
    assert.equal(await page.evaluate(()=>window.formSubmitted),true)
    checks.push('required select blocks empty submission and accepts a valid selection')
    const observer=page.getByRole('combobox',{name:'观察窗口'})
    assert((await observer.boundingBox()).width>150,'select trigger must not inherit the 38px action icon width')
    await observer.click();await page.getByRole('option',{name:'合成窗口选项',exact:true}).click()
    checks.push('companion window selector retains full width and opens its options')
    await page.evaluate(()=>window.setDraft('测试 @目录'))
    await page.waitForTimeout(350)
    assert.equal(await page.locator('.reference-trigger,.reference-popover').count(),0)
    assert.equal(apiCalls.length,0)
    await page.getByRole('button',{name:'显示历史引用'}).click()
    await page.getByRole('button',{name:'@历史引用',exact:true}).waitFor()
    checks.push('references disabled including @ auto-open, history retained')
    await page.getByText('桌宠模型加载失败：合成缺失',{exact:true}).waitFor()
    await page.getByText('启动异常',{exact:true}).waitFor()
    await page.getByText('显卡兼容模式',{exact:false}).waitFor()
    checks.push('pet startup failure, log path and compatibility setting visible')
    assert.deepEqual(errors,[])
    await fs.mkdir(output,{recursive:true})
    await page.screenshot({path:path.join(output,'settings-and-pet.png'),fullPage:true})
    await fs.writeFile(path.join(output,'report.json'),JSON.stringify({checks,pageErrors:errors,apiCalls},null,2))
    console.log(JSON.stringify({passed:checks.length,pageErrors:errors}))
  } catch (error) {
    await fs.mkdir(output,{recursive:true});await page.screenshot({path:path.join(output,'failure.png'),fullPage:true});
    await fs.writeFile(path.join(output,'failure.json'),JSON.stringify({errors,checks,html:await page.locator('#extra').innerHTML(),menus:await page.locator('.app-select-menu').count()},null,2));throw error
  } finally { await browser.close() }
}
main().catch(error=>{console.error(error);process.exitCode=1})
