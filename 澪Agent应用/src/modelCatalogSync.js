export const MODEL_CATALOG_KEY = 'mio_model_catalog_updated'

export function notifyModelCatalogChanged() {
  // Only an invalidation token is shared. Credentials never leave the backend.
  const value = `${Date.now()}-${Math.random()}`
  try { localStorage.setItem(MODEL_CATALOG_KEY, value) } catch { /* focus refresh is the fallback */ }
  window.dispatchEvent(new Event('mio-model-catalog-changed'))
}

export function mergeModelCatalog(current, catalog) {
  return { ...current, models: catalog.models || [], provider_presets: catalog.provider_presets || [],
    hidden_model_providers: catalog.hidden_model_providers || [] }
}
