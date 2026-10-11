import { apiRequest } from './api.js'

export const extensionsApi = {
  list: () => apiRequest('/api/extensions'),
  read: id => apiRequest(`/api/extensions/${encodeURIComponent(id)}`),
  save: body => apiRequest('/api/extensions', { method: 'POST', body: JSON.stringify(body) }),
  act: (id, action, body) => apiRequest(`/api/extensions/${encodeURIComponent(id)}/${action}`, { method: 'POST', body: JSON.stringify(body) }),
  panel: id => apiRequest(`/api/extensions/${encodeURIComponent(id)}/panel`),
}

export function extensionDraft(kind = 'panel') {
  return { manifest: { name: '', description: '', kind, version: '1.0.0', api_version: 1,
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    result_schema: { type: 'object' }, permissions: [], file_inputs: [],
    ...(kind === 'panel' ? { panel: { title: '我的面板', blocks: [{ type: 'text', text: '在这里展示成果。' }] } } : {}) },
    source: kind === 'python' ? 'def handle(arguments):\n    return {"ok": True}\n' : '',
    tests: kind === 'python' ? [{ arguments: {}, expected: { ok: true } }] : [], expected_revision: 0 }
}

export function renderablePanel(panel) {
  return { title: typeof panel?.title === 'string' ? panel.title : '', blocks: (panel?.blocks || []).filter(block => ['text', 'metric', 'table'].includes(block?.type)) }
}
