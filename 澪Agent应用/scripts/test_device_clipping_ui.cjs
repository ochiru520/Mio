/* Full production style stack and real native selects, synthetic devices only. */
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { chromium } = require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
async function main() {
  const { createServer } = await import('vite')
  const output = path.resolve(process.env.MIO_DEVICE_EVIDENCE || '../验收与归档/语音故障与排版修复-20261101/device-clipping')
  await fs.mkdir(output,{recursive:true})
  const server = await createServer({server:{host:'127.0.0.1',port:18483,strictPort:true}})
  await server.listen()
  const browser = await chromium.launch({channel:'msedge',headless:true})
  const errors=[],rows=[],mutations=[]
  const before = process.env.MIO_DEVICE_BEFORE === '1'
  try {
    for (const theme of (before ? ['sakura'] : ['sakura','calm-day'])) for (const width of [390,1152]) for (const zoom of [1,1.25,1.5]) for (const font of ['medium','large']) {
      const context=await browser.newContext({viewport:{width,height:1000},deviceScaleFactor:zoom})
      const page=await context.newPage()
      page.on('pageerror',error=>errors.push(error.message))
      await page.addInitScript(()=>{
        window.deviceFixture={requests:0,events:[]}
        window.addEventListener('mio-audio-devices-changed',event=>window.deviceFixture.events.push(event.detail))
        Object.defineProperty(navigator,'mediaDevices',{configurable:true,value:{
          addEventListener(){},removeEventListener(){},
          getUserMedia:async()=>{window.deviceFixture.requests++;return{getTracks:()=>[{stop(){}}]}},
          enumerateDevices:async()=>[{kind:'audioinput',deviceId:'mic-1',label:'麦克风（测试中文 Åg 日语 あいう）'},{kind:'audioinput',deviceId:'mic-2',label:'USB 备用麦克风'},{kind:'audiooutput',deviceId:'out-1',label:'耳机回放'}],
        }})
      })
      await page.route('**/api/**',async route=>{
        assert.equal(new URL(route.request().url()).pathname,'/api/audio-devices')
        if(route.request().method()!=='GET')mutations.push(route.request().postDataJSON())
        await route.fulfill({contentType:'application/json',body:JSON.stringify({available:true,output_device_id:'default',outputs:[{id:'default',label:'系统默认'},{id:'speaker-1',label:'扬声器（测试中文 Åg 日语 あいう）'}]})})
      })
      await page.goto(`http://127.0.0.1:18483/scripts/device-clipping-preview.html?zoom=${zoom}&font=${font}&theme=${theme}`)
      await page.getByRole('combobox',{name:'Mio 语音输出（正式说话）'}).waitFor()
      assert.equal(await page.evaluate(()=>window.deviceFixture.requests),0)
      await page.getByRole('button',{name:'检查设备',exact:true}).click()
      const mic=page.getByRole('combobox',{name:'麦克风',exact:true})
      await mic.focus();await mic.press('End');await mic.press('ArrowUp')
      assert.equal(await mic.inputValue(),'mic-1')
      const native=page.getByRole('combobox',{name:'Mio 语音输出（正式说话）'})
      await native.focus();await native.press('End')
      assert.equal(await native.inputValue(),'speaker-1')
      await page.getByRole('button',{name:'保存 Mio 输出设备',exact:true}).click()
      await page.getByText('回放设备与回声设置',{exact:true}).click()
      const metrics=await page.locator('.device-check select').evaluateAll(elements=>elements.map(element=>{
        const css=getComputedStyle(element),rect=element.getBoundingClientRect(),z=Number(getComputedStyle(document.documentElement).zoom)||1
        const px=key=>parseFloat(css[key])||0
        return {label:element.closest('label').firstChild.textContent.trim(),height:rect.height/z,fontSize:px('fontSize'),lineHeight:px('lineHeight'),paddingTop:px('paddingTop'),paddingBottom:px('paddingBottom'),contentHeight:rect.height/z-px('borderTopWidth')-px('borderBottomWidth')-px('paddingTop')-px('paddingBottom'),appearance:css.appearance,fontFamily:css.fontFamily,text:element.selectedOptions[0].textContent,focused:document.activeElement===element}
      }))
      rows.push({theme,width,zoom,font,metrics})
      if(!before)for(const metric of metrics){assert.equal(metric.appearance,'none');assert.ok(metric.contentHeight>=metric.lineHeight-0.5,JSON.stringify({width,zoom,font,metric}));assert.ok(metric.height>=36)}
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true)
      if((theme==='sakura'&&(font==='large'||zoom===1))||(theme==='calm-day'&&font==='large'&&zoom===1.5))await page.screenshot({path:path.join(output,`${before?'before':'after'}-${theme}-${width}-${Math.round(zoom*100)}-${font}.png`),fullPage:true})
      await page.getByRole('button',{name:'保存设备选择',exact:true}).click()
      assert.equal(await page.evaluate(()=>localStorage.getItem('mio.audio.input')),'mic-1')
      assert.equal(await page.evaluate(()=>window.deviceFixture.events.at(-1).inputDeviceId),'mic-1')
      await context.close()
    }
    assert.deepEqual(errors,[])
    await fs.writeFile(path.join(output,`${before?'before':'after'}-result.json`),JSON.stringify({rows,errors,mutations,checks:['Full production style stack and settings-window-shell','390/1152 CSS widths','100/125/150% CSS zoom plus device scale factor','Medium/large in-app font','Native keyboard End/ArrowUp selection, label accessibility, saved input/output','No arrow or horizontal overflow','Content box fits line box after fix']},null,2))
    console.log(JSON.stringify({cases:rows.length,errors,output},null,2))
  }finally{
    await browser.close();await server.close()
    await fs.writeFile(path.join(output,'owned-preview-cleanup.json'),JSON.stringify({pid:process.pid,port:18483,browserConnected:browser.isConnected(),httpListening:server.httpServer?.listening===true,closedAt:new Date().toISOString()},null,2))
  }
}
main().catch(error=>{console.error(error);process.exitCode=1})
