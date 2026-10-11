import test from 'node:test'
import assert from 'node:assert/strict'
import { isVisibleAttachment, useAttachments } from './composables/useAttachments.js'
import { applyAnalysisEvent } from './agentProcessState.js'
import { mergeModelCatalog } from './modelCatalogSync.js'
import { mergeLoadedItems } from './scrollCollections.js'

test('citation/protocol metadata never becomes an anonymous file, legitimate empty files remain', () => {
  const good={kind:'text',name:'empty.txt',size:0}, hidden={kind:'citation_manifest',citations:[]}
  assert.equal(isVisibleAttachment(hidden),false)
  assert.equal(isVisibleAttachment({}),false)
  assert.deepEqual(useAttachments({}).turnAttachments({parts:[{attachments:[hidden,{},good]}]}),[good])
})
test('catalog refresh preserves messages, drafts and unrelated settings', () => {
  const messages=[{content:'pending'}], settings={theme:'pink'}
  const data=mergeModelCatalog({messages,settings,models:[]},{models:[{id:'new'}]})
  assert.equal(data.messages,messages);assert.equal(data.settings,settings);assert.equal(data.models[0].id,'new')
})
test('analysis chunks remain isolated by provider stream, completion preserves content', () => {
  let blocks=[]
  for(const [id,text] of [['a','先'],['b','另'],['a','检查']]) blocks=applyAnalysisEvent(blocks,{event_type:'analysis_text_delta',payload:{stream_id:id,text}})
  blocks=applyAnalysisEvent(blocks,{event_type:'analysis_stream_finished',payload:{stream_id:'a',status:'generated'}})
  assert.equal(blocks[0].content,'先检查');assert.equal(blocks[1].content,'另');assert.equal(blocks[0].status,'completed')
})
test('scroll loading retains earlier entries, updates repeats, resets with new filter', () => {
  assert.deepEqual(mergeLoadedItems([{id:1,v:1}],[{id:1,v:2},{id:2}],50),[{id:1,v:2},{id:2}])
  assert.deepEqual(mergeLoadedItems([{id:1}],[{id:3}],0),[{id:3}])
})
