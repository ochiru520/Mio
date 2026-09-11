const assert=require('node:assert/strict')
const fs=require('node:fs/promises')
const {chromium}=require(process.env.MIO_PLAYWRIGHT_MODULE || 'playwright')
async function main(){
 const browser=await chromium.launch({channel:'msedge',headless:true})
 const page=await browser.newPage({viewport:{width:1280,height:900}})
 const errors=[];page.on('pageerror',e=>errors.push(e.message))
 const prompt={'1':{class_type:'TextImage',inputs:{text:'test'}},'2':{class_type:'SaveImage',inputs:{images:['1',0],filename_prefix:'test'}}}
 let environment={comfyui_root:'D:\\OldComfy',comfyui_base_url:'http://127.0.0.1:8188'},roots=[],imports=[]
 await page.addInitScript(()=>{window.pywebview={api:{select_agent_path:async kind=>window.cancelPicker?{ok:false,canceled:true}:kind==='workflow'?{ok:true,name:'自动识别.json',prompt:{'1':{class_type:'TextImage',inputs:{text:'test'}}}}:{ok:true,path:kind==='comfyui'?'E:\\工具\\ComfyUI':'E:\\素材与工作流'}}}})
 await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()),p=url.pathname;let data={}
   if(p.endsWith('/configuration')) data={environment,defaults:{image_workflow_id:'image',video_workflow_id:'video'}}
   else if(p.endsWith('/configuration/environment')){environment=request.postDataJSON();data={environment}}
   else if(p.endsWith('/bootstrap'))data={comfyui:{reachable:false},workflows:[],assets:[],jobs:[],presets:[],remote_providers:[]}
   else if(p.endsWith('/discover'))data={candidates:[]}
   else if(p.endsWith('/file-roots')){if(request.method()==='PUT')roots=request.postDataJSON().paths;data={roots:['D:\\Output',...roots],output_root:'D:\\Output'}}
   else if(p.endsWith('/work'))data={limits:{enabled:false,model_calls:8,tool_calls:20,seconds:180,cost_yuan:2}}
   else if(p.endsWith('/workflows/local'))data={files:[{name:'人物抠图.json',path:'E:\\素材与工作流\\人物抠图.json'}],truncated:false}
   else if(p.endsWith('/workflows/local/read'))data={name:'人物抠图.json',prompt}
   else if(p.endsWith('/workflows/preview'))data={prompt,media_type:'image',bindings:{prompt:[{node_id:'1',input:'text'}]},source_inputs:[],notes:[]}
   else if(p.endsWith('/workflows')&&request.method()==='POST'){const payload=request.postDataJSON();imports.push(payload);data={workflow:{id:'custom_'+imports.length,label:payload.label,media_type:payload.media_type,custom:true,available:true}}}
   await route.fulfill({json:data})
 })
 const evidence=process.env.MIO_UI_EVIDENCE||require('node:path').join(require('node:os').tmpdir(),'mio-local-picker-evidence')
 await fs.mkdir(evidence,{recursive:true})
 const category=async name=>page.getByRole('navigation',{name:'Agent 设置分类'}).getByRole('button',{name,exact:true}).click()
 try{
   await page.goto('http://127.0.0.1:1422/scripts/agent-settings-preview.html?view=settings')
   await category('创作环境')
   await page.getByRole('button',{name:'选择文件夹',exact:true}).click()
   assert.equal(await page.getByLabel('本机目录',{exact:true}).inputValue(),'E:\\工具\\ComfyUI')
   await page.getByRole('button',{name:'保存设置',exact:true}).click()
   await page.getByText('连接配置已保存。',{exact:true}).waitFor()
   assert.equal(environment.comfyui_root,'E:\\工具\\ComfyUI')
   await page.evaluate(()=>window.cancelPicker=true)
   await page.getByRole('button',{name:'选择文件夹',exact:true}).click()
   assert.equal(await page.getByLabel('本机目录',{exact:true}).inputValue(),environment.comfyui_root)
   await page.evaluate(()=>window.cancelPicker=false)
   await page.screenshot({path:evidence+'/picker-environment.png'})
   await category('工作流')
   await page.getByRole('button',{name:'搜索本机工作流',exact:true}).click()
   await page.getByRole('button',{name:'打开并添加',exact:true}).click()
   assert.equal(await page.getByLabel('名称',{exact:true}).inputValue(),'人物抠图')
   await page.getByRole('button',{name:'保存工作流',exact:true}).click()
   await page.getByText('工作流已导入。',{exact:true}).waitFor()
   assert.equal(imports[0].label,'人物抠图')
   assert.deepEqual(imports[0].bindings.prompt,[{node_id:'1',input:'text'}])
   await page.getByRole('button',{name:'选择文件导入',exact:true}).click()
   assert.equal(await page.getByLabel('名称',{exact:true}).inputValue(),'自动识别')
   await page.getByRole('button',{name:'取消导入',exact:true}).click()
   await page.getByRole('button',{name:'选择并授权搜索文件夹',exact:true}).click()
   await page.getByText('该目录已加入文件权限，澪可以搜索和读取其中的资料。',{exact:true}).waitFor()
   assert.deepEqual(roots,['E:\\素材与工作流'])
   await page.screenshot({path:evidence+'/picker-workflows.png'})
   for(const width of [800,390]){
     await page.setViewportSize({width,height:900})
     assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1))
     await page.screenshot({path:evidence+'/picker-workflows-'+width+'.png'})
   }
   await page.setViewportSize({width:1280,height:900})
   await category('文件权限')
   roots.push('F:\\其他已授权资料')
   await page.getByRole('button',{name:'选择文件夹并授权读取',exact:true}).click()
   await page.getByText('文件权限已保存。',{exact:true}).waitFor()
   assert.deepEqual(roots,['E:\\素材与工作流','F:\\其他已授权资料'])
   assert.deepEqual(errors,[])
   console.log('Native bridge integration, cancel, search, auto-bind import, folder grants and responsive layout passed')
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exit(1)})
