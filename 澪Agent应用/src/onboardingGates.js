export function canContinueOnboardingStep({
  step,
  coreReady,
  environmentBusy,
  assistantName,
  userAddress,
  modelVerified,
  providerBusy,
}) {
  if (step === 0) return coreReady === true && !environmentBusy
  if (step === 2) return Boolean(String(assistantName || '').trim() && String(userAddress || '').trim())
  if (step === 3) return !providerBusy
  return true
}

export function onboardingCompletion(form = {}, tutorialSkipped = false) {
  return {
    assistant_name: String(form.assistant_name || '').trim() || 'Mio',
    user_address: String(form.user_address || '').trim() || '你',
    web_search_enabled: form.web_search_enabled === true,
    proactive_enabled: form.proactive_enabled === true,
    daily_diary_auto_enabled: form.daily_diary_auto_enabled === true,
    qq_enabled: form.qq_enabled === true,
    tutorial_skipped: tutorialSkipped === true,
  }
}
