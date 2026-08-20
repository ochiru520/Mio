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
