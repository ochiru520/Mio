import test from 'node:test'
import assert from 'node:assert/strict'
import { artifactPreviewTarget } from './artifactPreviewTarget.js'
test('generated attachments open the registered artifact even if the download URL is legacy', () => {
  const known = { id:'artifact_a', name:'report.docx', url:'/api/artifacts/artifact_a/download' }
  assert.equal(artifactPreviewTarget({artifact_id:'artifact_a',url:'/api/agent/work/files/task_x/report.docx'},[known]),known)
  assert.equal(artifactPreviewTarget({artifact_url:'/api/artifacts/artifact_a/download'},[known]),known)
})
test('archive content URLs resolve while arbitrary links and input attachment ids do not', () => {
  assert.equal(artifactPreviewTarget({url:'/api/artifacts/artifact_b/content'}).id,'artifact_b')
  assert.equal(artifactPreviewTarget({id:'upload_a',url:'/api/uploads/a'}),null)
  assert.equal(artifactPreviewTarget({url:'https://example.com/api/artifacts/a/download'}),null)
})
