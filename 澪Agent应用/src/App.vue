<script setup>
import { monthlyCatalog } from './monthlyCatalog.js'
import UpdateNotice from './components/UpdateNotice.vue'
// Domain composition; factories do not eagerly access their dependencies.
import { useMessagePresentation } from './composables/useMessagePresentation.js'
const { formatRealTime, formatShortTime, formatSidebarTime, weekStartFor, weekEndFor, requestCostDetails, formatCost, costSourceLabel, pricingSourceLabel, sourceLabel, reasoningLabel, compactModelLabel, messageModelLabel, compactReasoningLabel, turnId, turnToolReceipts, turnCreationJob, turnCreationPrompt, turnCreationLoras, turnCreationSampler, turnCreationWorkflow, agentExecutionStatus, agentExecutionProgress, agentExecutionElapsed, agentCreationCost, turnTokenCount, toolReceiptLabel, toolReceiptStatus, toolReceiptTitle, modelRequestError, cleanDisplayContent, statusLabel, renderedMarkdown } = useMessagePresentation({
  get activeModel() { return activeModel },
  get compactActiveModelLabel() { return compactActiveModelLabel },
  get modelOptions() { return modelOptions },
  get selectedModel() { return selectedModel },
  get toolReceiptLabels() { return toolReceiptLabels },
})

import { useDataPrivacy } from './composables/useDataPrivacy.js'
const { completeOnboarding, refreshOnboardingEnvironment, refreshDataPrivacy, createDataBackup, importDataBackup, restoreDataBackup, deleteDataBackup, cleanupDataBackups, togglePrivacyPause } = useDataPrivacy({
  get dataPrivacyBusy() { return dataPrivacyBusy },
  get dataPrivacyLoading() { return dataPrivacyLoading },
  get dataPrivacyState() { return dataPrivacyState },
  get onboardingBusy() { return onboardingBusy },
  get onboardingEnvironment() { return onboardingEnvironment },
  get onboardingEnvironmentBusy() { return onboardingEnvironmentBusy },
  get onboardingError() { return onboardingError },
  get settingsFeedback() { return settingsFeedback },
  get showAppConfirm() { return showAppConfirm },
})

import { useBootstrap } from './composables/useBootstrap.js'
const { refreshContextUsage, refreshDayDashboard, openTokenUsagePanel, formatTokenCount, analyzeTodayState, refreshQqStatus, loadBootstrap, loadStartupGreetingSetting, loadDesktopPreferences, saveDesktopPreferences, loadQqStartupSetting, saveStartupGreetingSetting, saveQqStartupSetting } = useBootstrap({
  get activeSettingsSection() { return activeSettingsSection },
  get activeView() { return activeView },
  get bootstrap() { return bootstrap },
  get chatSettingsDraft() { return chatSettingsDraft },
  get chatVoiceLanguage() { return chatVoiceLanguage },
  get clearQqQrImage() { return clearQqQrImage },
  get contextUsage() { return contextUsage },
  get conversationStorageKey() { return conversationStorageKey },
  get conversations() { return conversations },
  get desktopPreferencesBusy() { return desktopPreferencesBusy },
  get desktopPreferencesDraft() { return desktopPreferencesDraft },
  get desktopPreferencesReady() { return desktopPreferencesReady },
  get diaries() { return diaries },
  get diarySearch() { return diarySearch },
  get ensureReasoningForActiveModel() { return ensureReasoningForActiveModel },
  get errorMessage() { return errorMessage },
  get groupChatSettings() { return groupChatSettings },
  get groupIdsDraft() { return groupIdsDraft },
  get isAgentWorkspace() { return isAgentWorkspace },
  get isSettingsSectionDirty() { return isSettingsSectionDirty },
  get loadQqQrCode() { return loadQqQrCode },
  get loading() { return loading },
  get messages() { return messages },
  get normalizedGroupChatSnapshot() { return normalizedGroupChatSnapshot },
  get qqAccountDraft() { return qqAccountDraft },
  get qqQrImageUrl() { return qqQrImageUrl },
  get qqQrLoading() { return qqQrLoading },
  get qqStartupEnabled() { return qqStartupEnabled },
  get qqTestTargetDraft() { return qqTestTargetDraft },
  get reasoningLevel() { return reasoningLevel },
  get request() { return request },
  get savedChatSettings() { return savedChatSettings },
  get savedDesktopPreferences() { return savedDesktopPreferences },
  get savedGroupChatSettings() { return savedGroupChatSettings },
  get savedQqStartupEnabled() { return savedQqStartupEnabled },
  get savedStartupGreeting() { return savedStartupGreeting },
  get selectedConversationId() { return selectedConversationId },
  get selectedDiary() { return selectedDiary },
  get selectedModel() { return selectedModel },
  get serializeSettings() { return serializeSettings },
  get settleChatScrollToBottom() { return settleChatScrollToBottom },
  get showSettingsFeedback() { return showSettingsFeedback },
  get showTokenUsagePanel() { return showTokenUsagePanel },
  get startupGreetingEnabled() { return startupGreetingEnabled },
  get stateAnalyzeBusy() { return stateAnalyzeBusy },
  get tokenUsageData() { return tokenUsageData },
  get tokenUsageLoading() { return tokenUsageLoading },
  get workspaceMode() { return workspaceMode },
})

import { useConversations } from './composables/useConversations.js'
const { refreshMessages, requestStartupGreeting, createClientRequestId, openAgentHandoff, sendMessage, requestChatCancellation, cancelActiveChat, refreshConversations, selectConversation, createNewConversation, renameConversation, deleteConversation, deleteConversationMessages } = useConversations({
  get activeChatCancelPayload() { return activeChatCancelPayload },
  set activeChatCancelPayload(value) { activeChatCancelPayload = value },
  get activeView() { return activeView },
  get attachments() { return attachments },
  get chatAbortController() { return chatAbortController },
  set chatAbortController(value) { chatAbortController = value },
  get contextUsage() { return contextUsage },
  get conversationLoadVersion() { return conversationLoadVersion },
  set conversationLoadVersion(value) { conversationLoadVersion = value },
  get conversationStorageKey() { return conversationStorageKey },
  get conversations() { return conversations },
  get draft() { return draft },
  get errorMessage() { return errorMessage },
  get handoffLoadVersion() { return handoffLoadVersion },
  set handoffLoadVersion(value) { handoffLoadVersion = value },
  get isAgentWorkspace() { return isAgentWorkspace },
  get latestTaskHandoff() { return latestTaskHandoff },
  get loading() { return loading },
  get messageRefreshInFlight() { return messageRefreshInFlight },
  set messageRefreshInFlight(value) { messageRefreshInFlight = value },
  get messages() { return messages },
  get modelRequestError() { return modelRequestError },
  get reasoningLevel() { return reasoningLevel },
  get refreshContextUsage() { return refreshContextUsage },
  get request() { return request },
  get scrollToBottom() { return scrollToBottom },
  get selectedConversationId() { return selectedConversationId },
  get selectedModel() { return selectedModel },
  get sending() { return sending },
  get settleChatScrollToBottom() { return settleChatScrollToBottom },
  get showAppConfirm() { return showAppConfirm },
  get showAppPrompt() { return showAppPrompt },
  get workspaceMode() { return workspaceMode },
})

import { useChatInteraction } from './composables/useChatInteraction.js'
const { copyTurn, stopMessageVoice, playMessageVoice, turnVoiceLanguageLabel, handleComposerKeydown, scrollToBottom, settleChatScrollToBottom, persistReasoning, persistSelectedModel, toggleModelMenu, closeModelMenu, loadSharedChatSettings, syncSharedChatSettings, chooseModel, chooseReasoning, handleDocumentPointerDown, handleDocumentKeydown } = useChatInteraction({
  get activeMessageAudio() { return activeMessageAudio },
  set activeMessageAudio(value) { activeMessageAudio = value },
  get activeMessageAudioUrl() { return activeMessageAudioUrl },
  set activeMessageAudioUrl(value) { activeMessageAudioUrl = value },
  get chatScroll() { return chatScroll },
  get chatSettingsDraft() { return chatSettingsDraft },
  get chatVoiceLanguage() { return chatVoiceLanguage },
  get cleanDisplayContent() { return cleanDisplayContent },
  get companionStatus() { return companionStatus },
  get copiedTurnId() { return copiedTurnId },
  get ensureReasoningForActiveModel() { return ensureReasoningForActiveModel },
  get errorMessage() { return errorMessage },
  get isAgentWorkspace() { return isAgentWorkspace },
  get modelMenuSection() { return modelMenuSection },
  get modelOptions() { return modelOptions },
  get modelPicker() { return modelPicker },
  get modelStorageKey() { return modelStorageKey },
  get reasoningLevel() { return reasoningLevel },
  get reasoningStorageKey() { return reasoningStorageKey },
  get request() { return request },
  get savedChatSettings() { return savedChatSettings },
  get selectedModel() { return selectedModel },
  get sendMessage() { return sendMessage },
  get serializeSettings() { return serializeSettings },
  get showModelMenu() { return showModelMenu },
  get speakingPartId() { return speakingPartId },
  get turnId() { return turnId },
  get voiceAbortController() { return voiceAbortController },
  set voiceAbortController(value) { voiceAbortController = value },
  get voiceLoadingPartId() { return voiceLoadingPartId },
})

import { useAttachments } from './composables/useAttachments.js'
const { readFileAsDataUrl, isTextAttachment, fileExtension, isImageAttachment, isDocumentAttachment, addSelectedFiles, handleFileSelection, handleComposerPaste, dragContainsFiles, handleFileDragEnter, handleFileDragOver, handleFileDragLeave, handleFileDrop, removeAttachment, formatFileSize, turnAttachments } = useAttachments({
  get attachmentLimits() { return attachmentLimits },
  get attachments() { return attachments },
  get errorMessage() { return errorMessage },
  get fileDragDepth() { return fileDragDepth },
  set fileDragDepth(value) { fileDragDepth = value },
  get isFileDragging() { return isFileDragging },
})

import { useModelProviders } from './composables/useModelProviders.js'
const { resetProviderForm, selectProviderKind, selectProviderPreset, selectOfficialProvider, openProviderPanel, saveProvider, discoverProviderModels, testWebSearch, deleteProvider, deleteProviderGroup, restoreProvider, testModel } = useModelProviders({
  get activeSettingsSection() { return activeSettingsSection },
  get activeView() { return activeView },
  get chatSettingsDraft() { return chatSettingsDraft },
  get discoveredModels() { return discoveredModels },
  get errorMessage() { return errorMessage },
  get loadBootstrap() { return loadBootstrap },
  get modelRequestError() { return modelRequestError },
  get modelTestBusy() { return modelTestBusy },
  get modelTestStatus() { return modelTestStatus },
  get persistSelectedModel() { return persistSelectedModel },
  get providerBusy() { return providerBusy },
  get providerDiscoveryBusy() { return providerDiscoveryBusy },
  get providerDiscoveryMeta() { return providerDiscoveryMeta },
  get providerDiscoveryWarning() { return providerDiscoveryWarning },
  get providerForm() { return providerForm },
  get providerPresets() { return providerPresets },
  get request() { return request },
  get selectedModel() { return selectedModel },
  get showAppConfirm() { return showAppConfirm },
  get showProviderPanel() { return showProviderPanel },
  get showSettingsFeedback() { return showSettingsFeedback },
  get webSearchTestBusy() { return webSearchTestBusy },
  get webSearchTestQuery() { return webSearchTestQuery },
  get webSearchTestResult() { return webSearchTestResult },
})

import { useDiaries } from './composables/useDiaries.js'
const { loadStats, changeStatsMonth, openStatsDiary, loadDiaries, scheduleDiarySearch, openDiary, generateTodayDiary, confirmDiary, updateDiary, openDailyReview, generateDailyReview, generateWeekly, generateMonthly } = useDiaries({
  get activeView() { return activeView },
  get dailyReviews() { return dailyReviews },
  get diaries() { return diaries },
  get diaryBusy() { return diaryBusy },
  get diarySearch() { return diarySearch },
  get diarySearchRequestId() { return diarySearchRequestId },
  set diarySearchRequestId(value) { diarySearchRequestId = value },
  get diarySearchTimer() { return diarySearchTimer },
  set diarySearchTimer(value) { diarySearchTimer = value },
  get errorMessage() { return errorMessage },
  get loadMemoryHub() { return loadMemoryHub },
  get logicalDate() { return logicalDate },
  get memoryLoaded() { return memoryLoaded },
  get memoryTab() { return memoryTab },
  get monthlyReviews() { return monthlyReviews },
  get refreshDayDashboard() { return refreshDayDashboard },
  get request() { return request },
  get reviewBusy() { return reviewBusy },
  get selectedDailyDate() { return selectedDailyDate },
  get selectedDiary() { return selectedDiary },
  get selectedMonthlyMonth() { return selectedMonthlyMonth },
  get selectedWeeklyStart() { return selectedWeeklyStart },
  get statsData() { return statsData },
  get statsLoaded() { return statsLoaded },
  get statsLoading() { return statsLoading },
  get weekStartFor() { return weekStartFor },
  get weeklyReviews() { return weeklyReviews },
})

import { useTasks } from './composables/useTasks.js'
const { loadAgentTasks, loadAutonomy, saveAutonomyPolicy, setAutonomyCapabilityOverride, createAutonomyGoal, updateAutonomyGoal, approveAutonomyBehavior, cancelAutonomyBehavior, autonomyStatusLabel, autonomyDeliveryLabel, taskStatusLabel, taskPayloadSummary, approveAgentTask, cancelAgentTask } = useTasks({
  get agentTasks() { return agentTasks },
  get autonomyBusy() { return autonomyBusy },
  get autonomyData() { return autonomyData },
  get autonomyLoaded() { return autonomyLoaded },
  get autonomyLoading() { return autonomyLoading },
  get errorMessage() { return errorMessage },
  get newAutonomyGoalCapability() { return newAutonomyGoalCapability },
  get newAutonomyGoalTitle() { return newAutonomyGoalTitle },
  get refreshMessages() { return refreshMessages },
  get request() { return request },
  get selectedConversationId() { return selectedConversationId },
  get taskBusy() { return taskBusy },
  get tasksLoaded() { return tasksLoaded },
  get tasksLoading() { return tasksLoading },
})

import { useMemory } from './composables/useMemory.js'
const { loadMemoryHub, resolveThread, recordFollowUpResult, saveRuntimeSummary, memoryLayerLabel, memoryCategoryLabel, addStructuredMemory, archiveStructuredMemory, restoreStructuredMemory, confirmMemoryCandidate, rejectMemoryCandidate, editStructuredMemory, addMemoryThread, saveMemoryThread, deleteMemoryThread, saveConversationSummary, addConversationSummary, deleteConversationSummary, addProfileNote, saveProfileNote, deleteProfileNote } = useMemory({
  get dailyReviewItems() { return dailyReviewItems },
  get dailyReviews() { return dailyReviews },
  get errorMessage() { return errorMessage },
  get logicalDate() { return logicalDate },
  get memoryBusy() { return memoryBusy },
  get memoryData() { return memoryData },
  get memoryLoaded() { return memoryLoaded },
  get memoryLoading() { return memoryLoading },
  get monthlyReviewItems() { return monthlyReviewItems },
  get monthlyReviews() { return monthlyReviews },
  get newConversationSummary() { return newConversationSummary },
  get newProfileNote() { return newProfileNote },
  get newStructuredMemory() { return newStructuredMemory },
  get newThreadContent() { return newThreadContent },
  get newThreadFollowUp() { return newThreadFollowUp },
  get request() { return request },
  get runtimeSummaryDraft() { return runtimeSummaryDraft },
  get selectedConversationId() { return selectedConversationId },
  get selectedDailyDate() { return selectedDailyDate },
  get selectedMonthlyMonth() { return selectedMonthlyMonth },
  get selectedWeeklyStart() { return selectedWeeklyStart },
  get showAppConfirm() { return showAppConfirm },
  get showAppPrompt() { return showAppPrompt },
  get weekStartFor() { return weekStartFor },
  get weeklyReviewItems() { return weeklyReviewItems },
  get weeklyReviews() { return weeklyReviews },
})

import { useQq } from './composables/useQq.js'
const { applyQqStatus, qqStartupMessage, waitForQqStartup, controlQq, setupQqChannel, testQqDelivery, clearQqQrImage, loadQqQrCode, saveGroupChatSettings, clearGroupChatContext } = useQq({
  get bootstrap() { return bootstrap },
  get errorMessage() { return errorMessage },
  get groupChatSettings() { return groupChatSettings },
  get groupIdsDraft() { return groupIdsDraft },
  get loadBootstrap() { return loadBootstrap },
  get normalizedGroupChatSnapshot() { return normalizedGroupChatSnapshot },
  get qqAccountDraft() { return qqAccountDraft },
  get qqBusy() { return qqBusy },
  get qqQrError() { return qqQrError },
  get qqQrImageUrl() { return qqQrImageUrl },
  get qqQrLoadVersion() { return qqQrLoadVersion },
  set qqQrLoadVersion(value) { qqQrLoadVersion = value },
  get qqQrLoading() { return qqQrLoading },
  get qqSetupResult() { return qqSetupResult },
  get qqTestTargetDraft() { return qqTestTargetDraft },
  get request() { return request },
  get savedGroupChatSettings() { return savedGroupChatSettings },
  get showSettingsFeedback() { return showSettingsFeedback },
})

import { useCompanion } from './composables/useCompanion.js'
const { loadCompanionStatus, openScreenPreviewWindow, saveCompanionSize, controlCompanion, previewLive2DMotion, previewLive2DExpression, saveCompanionSettings, testCompanionVoice, testCompanionVoiceProfile, uploadVoiceReference, exportVoicePackage, importVoicePackage, controlVoiceRuntime, controlLocalVision, pullLocalVisionModel, controlVoiceTraining, importLive2DModel, deleteLive2DModel, replaceLive2DPreview, uploadCompanionSpriteSheet, loadGameWindows, controlObservation, letMioSeeObservation } = useCompanion({
  get activeSettingsItem() { return activeSettingsItem },
  get companionAvatarNonce() { return companionAvatarNonce },
  get companionBusy() { return companionBusy },
  get companionSettingKeys() { return companionSettingKeys },
  get companionSettingsSnapshot() { return companionSettingsSnapshot },
  get companionStatus() { return companionStatus },
  get companionStatusLoading() { return companionStatusLoading },
  set companionStatusLoading(value) { companionStatusLoading = value },
  get companionStatusReady() { return companionStatusReady },
  get errorMessage() { return errorMessage },
  get gameWindows() { return gameWindows },
  get gameWindowsLoading() { return gameWindowsLoading },
  get isImageAttachment() { return isImageAttachment },
  get observationInterval() { return observationInterval },
  get observationMode() { return observationMode },
  get readFileAsDataUrl() { return readFileAsDataUrl },
  get request() { return request },
  get savedCompanionSettings() { return savedCompanionSettings },
  get screenScope() { return screenScope },
  get selectedGameHwnd() { return selectedGameHwnd },
  get showAppConfirm() { return showAppConfirm },
  get showAppPrompt() { return showAppPrompt },
  get showSettingsFeedback() { return showSettingsFeedback },
})

import { usePetCall } from './composables/usePetCall.js'
const { handlePetChatKeydown, beginPetChatWindowDrag, resetPetCallCapture, submitPetCallTurn, processPetCallAudio, startPetCall, stopPetCall, togglePetCall, sendPetChat, handlePetChatPaste } = usePetCall({
  get attachmentLimits() { return attachmentLimits },
  get createClientRequestId() { return createClientRequestId },
  get errorMessage() { return errorMessage },
  get petCallActive() { return petCallActive },
  get petCallAudioContext() { return petCallAudioContext },
  set petCallAudioContext(value) { petCallAudioContext = value },
  get petCallAwaitingVoiceSince() { return petCallAwaitingVoiceSince },
  set petCallAwaitingVoiceSince(value) { petCallAwaitingVoiceSince = value },
  get petCallBargeInStartedAt() { return petCallBargeInStartedAt },
  set petCallBargeInStartedAt(value) { petCallBargeInStartedAt = value },
  get petCallError() { return petCallError },
  get petCallFrames() { return petCallFrames },
  set petCallFrames(value) { petCallFrames = value },
  get petCallInterruptSent() { return petCallInterruptSent },
  set petCallInterruptSent(value) { petCallInterruptSent = value },
  get petCallLastVoiceAt() { return petCallLastVoiceAt },
  set petCallLastVoiceAt(value) { petCallLastVoiceAt = value },
  get petCallMonitorTimer() { return petCallMonitorTimer },
  set petCallMonitorTimer(value) { petCallMonitorTimer = value },
  get petCallNextTurnId() { return petCallNextTurnId },
  set petCallNextTurnId(value) { petCallNextTurnId = value },
  get petCallPreRoll() { return petCallPreRoll },
  set petCallPreRoll(value) { petCallPreRoll = value },
  get petCallProcessor() { return petCallProcessor },
  set petCallProcessor(value) { petCallProcessor = value },
  get petCallResponseId() { return petCallResponseId },
  set petCallResponseId(value) { petCallResponseId = value },
  get petCallSessionId() { return petCallSessionId },
  set petCallSessionId(value) { petCallSessionId = value },
  get petCallSettings() { return petCallSettings },
  get petCallSource() { return petCallSource },
  set petCallSource(value) { petCallSource = value },
  get petCallSpeechCandidateStartedAt() { return petCallSpeechCandidateStartedAt },
  set petCallSpeechCandidateStartedAt(value) { petCallSpeechCandidateStartedAt = value },
  get petCallSpeechStartedAt() { return petCallSpeechStartedAt },
  set petCallSpeechStartedAt(value) { petCallSpeechStartedAt = value },
  get petCallState() { return petCallState },
  get petCallStream() { return petCallStream },
  set petCallStream(value) { petCallStream = value },
  get petCallTurnPending() { return petCallTurnPending },
  set petCallTurnPending(value) { petCallTurnPending = value },
  get petChatDraft() { return petChatDraft },
  get petChatImages() { return petChatImages },
  get petChatSending() { return petChatSending },
  get readFileAsDataUrl() { return readFileAsDataUrl },
  get request() { return request },
})

import { useSettings } from './composables/useSettings.js'
const { focusComposer, openMemoryTab, handleSettingsModelChange, handlePetModelChange, applyDisplayMode, saveAppearanceSettings, loadRuntimeSettings, saveRuntimeSettings, loadMioProfileSettings, saveMioProfileSettings, uploadProfileAvatar, resetProfileAvatar, uploadAppearanceImage, resetAppearanceImage, saveConversationSettings, saveActiveSettings, resetActiveSettings, openSettingsSection } = useSettings({
  get DISPLAY_MODE_PRESETS() { return DISPLAY_MODE_PRESETS },
  get activeSettingsItem() { return activeSettingsItem },
  get activeSettingsSection() { return activeSettingsSection },
  get activeView() { return activeView },
  get appPreferencesDraft() { return appPreferencesDraft },
  get chatBackgroundCustom() { return chatBackgroundCustom },
  get chatBackgroundNonce() { return chatBackgroundNonce },
  get chatSettingsDraft() { return chatSettingsDraft },
  get cloneAppPreferences() { return cloneAppPreferences },
  get companionStatus() { return companionStatus },
  get desktopPreferencesDraft() { return desktopPreferencesDraft },
  get desktopPreferencesReady() { return desktopPreferencesReady },
  get ensureReasoningForActiveModel() { return ensureReasoningForActiveModel },
  get errorMessage() { return errorMessage },
  get groupChatSettings() { return groupChatSettings },
  get groupIdsDraft() { return groupIdsDraft },
  get isImageAttachment() { return isImageAttachment },
  get isSettingsSectionDirty() { return isSettingsSectionDirty },
  get leftSidebarVisible() { return leftSidebarVisible },
  get loadBootstrap() { return loadBootstrap },
  get loadCompanionStatus() { return loadCompanionStatus },
  get memoryData() { return memoryData },
  get memoryTab() { return memoryTab },
  get mioProfileAvoidDraft() { return mioProfileAvoidDraft },
  get mioProfileBusy() { return mioProfileBusy },
  get mioProfileDraft() { return mioProfileDraft },
  get mioProfileNotesDraft() { return mioProfileNotesDraft },
  get mioProfileReady() { return mioProfileReady },
  get modelOptions() { return modelOptions },
  get persistReasoning() { return persistReasoning },
  get persistSelectedModel() { return persistSelectedModel },
  get petReasoningOptions() { return petReasoningOptions },
  get privateRuntimePathKeys() { return privateRuntimePathKeys },
  get profileAvatarCustom() { return profileAvatarCustom },
  get profileAvatarNonce() { return profileAvatarNonce },
  get qqStartupEnabled() { return qqStartupEnabled },
  get readFileAsDataUrl() { return readFileAsDataUrl },
  get reasoningLevel() { return reasoningLevel },
  get refreshDataPrivacy() { return refreshDataPrivacy },
  get request() { return request },
  get rightSidebarVisible() { return rightSidebarVisible },
  get runtimeSettingKeysBySection() { return runtimeSettingKeysBySection },
  get runtimeSettingsBusy() { return runtimeSettingsBusy },
  get runtimeSettingsDraft() { return runtimeSettingsDraft },
  get runtimeSettingsReady() { return runtimeSettingsReady },
  get runtimeSettingsRevision() { return runtimeSettingsRevision },
  get saveCompanionSettings() { return saveCompanionSettings },
  get saveDesktopPreferences() { return saveDesktopPreferences },
  get saveGroupChatSettings() { return saveGroupChatSettings },
  get saveQqStartupSetting() { return saveQqStartupSetting },
  get saveStartupGreetingSetting() { return saveStartupGreetingSetting },
  get savedAppPreferences() { return savedAppPreferences },
  get savedChatSettings() { return savedChatSettings },
  get savedDesktopPreferences() { return savedDesktopPreferences },
  get savedGroupChatSettings() { return savedGroupChatSettings },
  get savedMioProfile() { return savedMioProfile },
  get savedQqStartupEnabled() { return savedQqStartupEnabled },
  get savedRuntimeSettings() { return savedRuntimeSettings },
  get savedRuntimeSettingsSource() { return savedRuntimeSettingsSource },
  get savedStartupGreeting() { return savedStartupGreeting },
  get selectedModel() { return selectedModel },
  get serializeSettings() { return serializeSettings },
  get settingsFeedback() { return settingsFeedback },
  get settingsReasoningOptions() { return settingsReasoningOptions },
  get settingsReturnView() { return settingsReturnView },
  get showAppConfirm() { return showAppConfirm },
  get showSettingsFeedback() { return showSettingsFeedback },
  get startupGreetingEnabled() { return startupGreetingEnabled },
  get syncSharedChatSettings() { return syncSharedChatSettings },
  get userAvatarCustom() { return userAvatarCustom },
  get userAvatarNonce() { return userAvatarNonce },
})

import { useDesktopNavigation } from './composables/useDesktopNavigation.js'
const { navigatePrimaryView, returnToApp, openCompanionSection, focusDesktopPetChat, toggleLeftSidebar, toggleRightSidebar, toggleLeftSidebarPinned, toggleRightSidebarPinned, applyAgentPreferences, controlDesktopWindow, switchDesktopWorkspace, resizeDesktopWindow } = useDesktopNavigation({
  get activeCompanionSection() { return activeCompanionSection },
  get activeSettingsSection() { return activeSettingsSection },
  get activeView() { return activeView },
  get agentRightSidebarHoverExpand() { return agentRightSidebarHoverExpand },
  get appPreferencesDraft() { return appPreferencesDraft },
  get diaries() { return diaries },
  get errorMessage() { return errorMessage },
  get isAgentWorkspace() { return isAgentWorkspace },
  get isSettingsSectionDirty() { return isSettingsSectionDirty },
  get leftSidebarPinned() { return leftSidebarPinned },
  get leftSidebarVisible() { return leftSidebarVisible },
  get openDiary() { return openDiary },
  get openSettingsSection() { return openSettingsSection },
  get refreshConversations() { return refreshConversations },
  get resetActiveSettings() { return resetActiveSettings },
  get rightSidebarPinned() { return rightSidebarPinned },
  get rightSidebarVisible() { return rightSidebarVisible },
  get savedAppPreferences() { return savedAppPreferences },
  get selectConversation() { return selectConversation },
  get selectedDiary() { return selectedDiary },
  get settingsReturnView() { return settingsReturnView },
  get showAppConfirm() { return showAppConfirm },
  get validInitialViews() { return validInitialViews },
})


import AgentTasksPage from './components/AgentTasksPage.vue'
import { executionStatus } from './agentTaskState.js'
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, provide, reactive, ref, watch } from 'vue'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import AppDialog from './components/AppDialog.vue'
import OnboardingPage from './components/OnboardingPage.vue'
// Load large work surfaces on first use so the initial desktop bundle stays
// small and the cold-start UI can become interactive sooner.
const ChatPage = defineAsyncComponent(() => import('./components/ChatPage.vue'))
const CompanionPage = defineAsyncComponent(() => import('./components/CompanionPage.vue'))
const HomePage = defineAsyncComponent(() => import('./components/HomePage.vue'))
const AgentHomePage = defineAsyncComponent(() => import('./components/AgentHomePage.vue'))
const AgentRightRail = defineAsyncComponent(() => import('./components/AgentRightRail.vue'))
const AgentSettingsPage = defineAsyncComponent(() => import('./components/AgentSettingsPage.vue'))
const RecordsPage = defineAsyncComponent(() => import('./components/RecordsPage.vue'))
const SettingsPage = defineAsyncComponent(() => import('./components/SettingsPage.vue'))
const TasksPage = defineAsyncComponent(() => import('./components/TasksPage.vue'))
import { buildTurnVoicePayload, voiceLanguageLabel } from './chatVoice.js'
import { normalizeLoadedChatSettings } from './chatSettingsState.js'
import { ACTIVE_VIEW_HEARTBEAT_MS, buildActiveViewReport } from './activeViewState.js'
import {
  creationCostLabel,
  creationLoras,
  creationProgressPercent,
  creationPrompt,
  creationSamplerLabel,
  creationStatusLabel,
  creationWorkflowSnapshot,
  findCreationJob,
  formatCreationElapsed,
} from './agentCreationState.js'
import { bargeInThreshold, bytesToBase64, encodePcmWav } from './petCallAudio.js'
import { apiRequest } from './services/api.js'
import { focusModal, restoreModalFocus, trapModalFocus } from './modalFocus.js'
import * as chatApi from './services/chatApi.js'
import { filterConversationsForWorkspace, normalizeAgentView } from './workspaceState.js'
import * as autonomyApi from './services/autonomyApi.js'
import * as diaryApi from './services/diaryApi.js'
import * as memoryApi from './services/memoryApi.js'
import * as observationApi from './services/observationApi.js'
import * as qqApi from './services/qqApi.js'
import * as settingsApi from './services/settingsApi.js'
import * as selfStateApi from './services/selfStateApi.js'
import * as voiceApi from './services/voiceApi.js'
import {
  completeOnboarding as completeOnboardingRequest,
  loadOnboardingEnvironment,
  loadOnboardingStatus,
} from './services/onboardingApi.js'
import {
  cleanupAutomaticBackups,
  createCompleteBackup,
  deleteCompleteBackup,
  importCompleteBackup,
  loadDataPrivacy,
  restoreCompleteBackup,
  setPrivacyPaused,
} from './services/dataPrivacyApi.js'
import {
  Activity,
  Archive,
  ArrowLeft,
  BookOpen,
  Bot,
  CalendarDays,
  Check,
  ChartNoAxesCombined,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  Clock3,
  Copy,
  Download,
  Feather,
  FileText,
  FilePenLine,
  Film,
  FolderOpen,
  Gamepad2,
  Heart,
  History,
  LogIn,
  MessageSquareText,
  Maximize2,
  Minus,
  Monitor,
  MoreHorizontal,
  Paperclip,
  Pin,
  PinOff,
  Phone,
  PhoneOff,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Plus,
  Power,
  RefreshCw,
  RotateCw,
  Search,
  Send,
  Settings,
  Sparkles,
  Square,
  SquarePen,
  Target,
  ListChecks,
  Trash2,
  Volume2,
  Wifi,
  WifiOff,
  Wrench,
  X,
} from '@lucide/vue'

const workspaceSearchParams = new URLSearchParams(window.location.search)
const workspaceMode = workspaceSearchParams.get('workspace') === 'agent' ? 'agent' : 'main'
const isAgentWorkspace = workspaceMode === 'agent'
const conversationStorageKey = isAgentWorkspace ? 'mio_agent_conversation_id' : 'mio_conversation_id'

const THEME_OPTIONS = [
  { id: 'mist', label: '柔雾', description: '淡粉与雾灰', colors: ['#f2f0f1', '#c7849c', '#4f927b'] },
  { id: 'clear', label: '清晨', description: '清蓝与浅灰', colors: ['#edf4f5', '#5b8c98', '#d6a26f'] },
  { id: 'rose', label: '蔷薇', description: '柔粉与葡萄灰', colors: ['#f4edef', '#a66f82', '#658e83'] },
  { id: 'forest', label: '森雨', description: '苔绿与暖白', colors: ['#edf1ed', '#668978', '#b48468'] },
  { id: 'sakura', label: '晚樱', description: '樱粉与暮蓝', colors: ['#f3eef1', '#b7798f', '#687f9b'] },
]
const VALID_THEME_IDS = new Set(THEME_OPTIONS.map((item) => item.id))

const DEFAULT_APP_PREFERENCES = {
  theme: 'mist',
  font_size: 'medium',
  default_open_page: 'home',
  left_sidebar_visible: true,
  right_sidebar_visible: true,
  left_sidebar_hover_expand: true,
  right_sidebar_hover_expand: true,
  remember_sidebar_state: true,
  light_animations: true,
  focus_mode: false,
  display_mode: 'full',
  visibility: {
    global_statusbar: true,
    status_mood: true,
    status_relationship: true,
    status_date: true,
    chat_statusbar: true,
    message_time: true,
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    message_copy: true,
    message_play: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: false,
    settings_character: true,
  },
  home_widgets: {
    daily_thirty: true,
    mood: true,
    diary: true,
    memory: true,
    agent_status: true,
    screen_status: true,
    api_info: false,
    token_stats: false,
  },
}

const DISPLAY_MODE_PRESETS = {
  companion: {
    response_latency: false,
    message_cost: false,
    message_model: false,
    message_tokens: false,
    memory_status: false,
    ai_analysis: false,
    developer_details: false,
  },
  full: {
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: false,
  },
  developer: {
    response_latency: true,
    message_cost: true,
    message_model: true,
    message_tokens: true,
    memory_status: true,
    ai_analysis: true,
    developer_details: true,
  },
}

const displayModeOptions = [
  { id: 'companion', label: '陪伴', description: '只保留关系、状态和对话本身' },
  { id: 'full', label: '完整', description: '展示费用、模型、记忆和分析依据' },
  { id: 'developer', label: '开发者', description: '补充 Token、请求 ID 和运行细节' },
]

const visibilityOptions = [
  { id: 'global_statusbar', label: '顶部 Mio 状态', description: '显示头像、心情、关系和日期' },
  { id: 'status_mood', label: '顶部心情', description: '在顶部状态中显示今天的心情' },
  { id: 'status_relationship', label: '顶部关系', description: '在顶部状态中显示关系状态' },
  { id: 'status_date', label: '顶部日期', description: '在顶部状态中显示逻辑日期' },
  { id: 'chat_statusbar', label: '聊天状态条', description: '显示陪伴、记忆和 Agent 状态' },
  { id: 'message_time', label: '消息时间', description: '在每轮对话结束后显示真实时间' },
  { id: 'response_latency', label: '响应耗时', description: '显示模型真实返回的总耗时' },
  { id: 'message_cost', label: '回复费用', description: '只在 Mio 的回复后显示实扣或待确认费用' },
  { id: 'message_model', label: '模型信息', description: '显示本轮实际使用的模型与思考方式' },
  { id: 'message_tokens', label: 'Token 信息', description: '显示本轮输入、缓存、推理与输出 Token' },
  { id: 'message_copy', label: '复制按钮', description: '悬停回复时显示复制按钮' },
  { id: 'message_play', label: '播放按钮', description: '悬停 Mio 回复时显示语音播放按钮' },
  { id: 'memory_status', label: '记忆连接', description: '显示当前接入的长期记忆数量' },
  { id: 'ai_analysis', label: '分析依据', description: '允许展开 Mio 的观察和状态判断依据' },
  { id: 'developer_details', label: '开发细节', description: '显示请求 ID、来源和完整技术字段' },
  { id: 'settings_character', label: '设置页 Mio 形象', description: '宽屏设置页右侧显示 Mio 的全身形象' },
]

const homeWidgetOptions = [
  { id: 'daily_thirty', label: '今日成长', description: '今天是否完成自我提升' },
  { id: 'mood', label: '心情分析', description: '今天的情绪和评分' },
  { id: 'diary', label: '今日日记', description: '今天是否已经整理' },
  { id: 'memory', label: '最近记忆', description: '当前长期记忆数量' },
  { id: 'agent_status', label: 'Agent 状态', description: '空闲、思考或执行中' },
  { id: 'screen_status', label: '屏幕状态', description: '桌宠和屏幕观察是否运行' },
  { id: 'api_info', label: 'API 信息', description: '当前模型与连接方式' },
  { id: 'token_stats', label: 'Token 统计', description: '当前上下文使用量' },
]

const profileBehaviorLabels = {
  initiative: '主动联系',
  diary: '日记行为',
  web_search: '联网原则',
  time_awareness: '时间意识',
  daily_thirty_awareness: '每日三十判断',
  autonomous_actions: '主动执行',
  pending_threads: '待跟进话题',
  curiosity: '好奇心',
  mood_quirks: '小情绪',
  scenario: '角色场景',
  character_rules: '角色规则与系统提示',
  reply_rules: '回复后置规则',
  dialogue_examples: '示例对话',
  worldbook_rules: '世界书规则',
}

function readAppPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem('mio_app_preferences') || '{}')
    const source = saved && typeof saved === 'object' ? saved : {}
    const merged = {
      ...DEFAULT_APP_PREFERENCES,
      ...source,
      visibility: { ...DEFAULT_APP_PREFERENCES.visibility, ...(source.visibility || {}) },
      home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets, ...(source.home_widgets || {}) },
    }
    if (!VALID_THEME_IDS.has(merged.theme)) merged.theme = 'mist'
    if (!['home', 'chat', 'diaries', 'memory', 'tasks', 'companion', 'creation', 'creation-image', 'creation-video'].includes(merged.default_open_page)) {
      merged.default_open_page = 'home'
    }
    if (!Object.prototype.hasOwnProperty.call(DISPLAY_MODE_PRESETS, merged.display_mode)) merged.display_mode = 'full'
    if (!Object.prototype.hasOwnProperty.call(saved || {}, 'left_sidebar_visible')) {
      merged.left_sidebar_visible = localStorage.getItem('mio_left_sidebar_visible') !== 'false'
    }
    if (!Object.prototype.hasOwnProperty.call(saved || {}, 'right_sidebar_visible')) {
      merged.right_sidebar_visible = localStorage.getItem('mio_right_sidebar_visible') !== 'false'
    }
    return merged
  } catch {
    return {
      ...DEFAULT_APP_PREFERENCES,
      visibility: { ...DEFAULT_APP_PREFERENCES.visibility },
      home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets },
    }
  }
}

function cloneAppPreferences(source) {
  return {
    ...DEFAULT_APP_PREFERENCES,
    ...(source || {}),
    visibility: { ...DEFAULT_APP_PREFERENCES.visibility, ...(source?.visibility || {}) },
    home_widgets: { ...DEFAULT_APP_PREFERENCES.home_widgets, ...(source?.home_widgets || {}) },
  }
}

function applyStoredAppPreferences() {
  const next = cloneAppPreferences(readAppPreferences())
  savedAppPreferences.value = next
  appPreferencesDraft.value = cloneAppPreferences(next)
  leftSidebarVisible.value = next.focus_mode ? false : next.left_sidebar_visible !== false
  if (!isAgentWorkspace) rightSidebarVisible.value = next.focus_mode ? false : next.right_sidebar_visible !== false
}

function handleAppPreferencesStorage(event) {
  if (event.key === 'mio_app_preferences') applyStoredAppPreferences()
}

const initialAppPreferences = readAppPreferences()
const validInitialViews = new Set(['home', 'chat', 'diaries', 'memory', 'tasks', 'companion'])
const storedAgentView = localStorage.getItem('mio_agent_active_view') || 'home'
const activeView = ref(isAgentWorkspace
  ? normalizeAgentView(storedAgentView)
  : (validInitialViews.has(initialAppPreferences.default_open_page) ? initialAppPreferences.default_open_page : 'home'))
const settingsReturnView = ref(activeView.value)
const activeCompanionSection = ref('pet-panel')
const settingsSectionFromUrl = workspaceSearchParams.get('settings-section')
const activeSettingsSection = ref(settingsSectionFromUrl || localStorage.getItem('mio_settings_section') || 'general')
const settingsSearch = ref('')
const settingsFeedback = ref({ section: '', type: '', message: '' })
const savedAppPreferences = ref(cloneAppPreferences(initialAppPreferences))
const appPreferencesDraft = ref(cloneAppPreferences(savedAppPreferences.value))
const chatSettingsDraft = ref({ model_id: '', reasoning_level: '', voice_language: 'auto' })
const savedChatSettings = ref('')
const latestTaskHandoff = ref(null)
let handoffLoadVersion = 0
const savedStartupGreeting = ref(true)
const desktopPreferencesDraft = ref({ close_to_background: true, background_notifications: true, windows_startup: false })
const savedDesktopPreferences = ref('')
const desktopPreferencesReady = ref(false)
const desktopPreferencesBusy = ref(false)
const qqStartupEnabled = ref(false)
const savedQqStartupEnabled = ref(false)
const savedGroupChatSettings = ref('')
const savedCompanionSettings = ref({ pet: '', voice: '', observation: '' })
const runtimeSettingsDraft = ref({})
const savedRuntimeSettings = ref('')
const runtimeSettingsRevision = ref('')
const runtimeSettingsReady = ref(false)
const runtimeSettingsBusy = ref(false)
const webSearchTestQuery = ref('帮我查一下 DeepSeek 最新消息')
const webSearchTestBusy = ref(false)
const webSearchTestResult = ref(null)
const mioProfileDraft = ref(null)
const mioProfileNotesDraft = ref('')
const mioProfileAvoidDraft = ref('')
const savedMioProfile = ref('')
const mioProfileReady = ref(false)
const mioProfileBusy = ref(false)
const profileAvatarNonce = ref(Date.now())
const profileAvatarCustom = ref(false)
const userAvatarNonce = ref(Date.now())
const userAvatarCustom = ref(false)
const chatBackgroundNonce = ref(Date.now())
const chatBackgroundCustom = ref(false)
const standalonePetChat = window.location.hash === '#pet-chat-window'
const leftSidebarVisible = ref(savedAppPreferences.value.left_sidebar_visible !== false)
const rightSidebarVisible = ref(isAgentWorkspace
  ? localStorage.getItem('mio_agent_right_sidebar_visible') !== 'false'
  : savedAppPreferences.value.right_sidebar_visible !== false)
const leftSidebarPinned = ref(localStorage.getItem('mio_left_sidebar_pinned') === 'true')
const rightSidebarPinned = ref(localStorage.getItem(isAgentWorkspace ? 'mio_agent_right_sidebar_pinned' : 'mio_right_sidebar_pinned') === 'true')
const agentRightSidebarHoverExpand = ref(localStorage.getItem('mio_agent_right_sidebar_hover_expand') !== 'false')
const leftSidebarHovered = ref(false)
const rightSidebarHovered = ref(false)
const bootstrap = ref(null)
const providerPresets = computed(() => bootstrap.value?.provider_presets || [])
const onboardingBusy = ref(false)
const onboardingError = ref('')
const onboardingEnvironment = ref(null)
const onboardingEnvironmentBusy = ref(false)
const dataPrivacyState = ref({ backups: [], backupStorage: {}, privacy: { paused: false, capabilities: [] }, migrations: {} })
const dataPrivacyLoading = ref(false)
const dataPrivacyBusy = ref('')
const appDialog = reactive({
  open: false,
  mode: 'confirm',
  title: '请确认',
  message: '',
  value: '',
  confirmText: '确定',
  cancelText: '取消',
  danger: false,
  multiline: false,
  resolve: null,
})

function settleAppDialog(result) {
  const resolve = appDialog.resolve
  appDialog.open = false
  appDialog.resolve = null
  if (resolve) resolve(result)
}

function showAppConfirm({ title = '请确认', message = '', confirmText = '确定', cancelText = '取消', danger = false } = {}) {
  return new Promise((resolve) => {
    Object.assign(appDialog, {
      open: true,
      mode: 'confirm',
      title,
      message,
      value: '',
      confirmText,
      cancelText,
      danger,
      multiline: false,
      resolve,
    })
  })
}

function showAppPrompt({ title, message = '', value = '', confirmText = '保存', multiline = false } = {}) {
  return new Promise((resolve) => {
    Object.assign(appDialog, {
      open: true,
      mode: 'prompt',
      title,
      message,
      value,
      confirmText,
      cancelText: '取消',
      danger: false,
      multiline,
      resolve,
    })
  })
}

function confirmAppDialog() {
  settleAppDialog(appDialog.mode === 'prompt' ? appDialog.value : true)
}

function cancelAppDialog() {
  settleAppDialog(appDialog.mode === 'prompt' ? null : false)
}

const messages = ref([])
const conversations = ref([])
const selectedConversationId = ref(workspaceSearchParams.get('conversation_id') || localStorage.getItem(conversationStorageKey) || '')
watch(selectedConversationId, async (conversationId) => {
  const version = ++handoffLoadVersion
  latestTaskHandoff.value = null
  if (!conversationId || isAgentWorkspace) return
  try {
    const result = await request(`/api/agent/work/handoff/source/${encodeURIComponent(conversationId)}`)
    if (version === handoffLoadVersion) latestTaskHandoff.value = result.handoff || null
  } catch {
    // A missing historical notice must not prevent loading the conversation.
  }
}, { immediate: true })
const diaries = ref([])
const selectedDiary = ref(null)
const diarySearch = ref('')
let diarySearchTimer = null
let diarySearchRequestId = 0
const statsData = ref({ summary: {}, calendar: [], mood_trend: [], year: 0, month: 0, logical_date: '' })
const statsLoading = ref(false)
const statsLoaded = ref(false)
const agentTasks = ref([])
const tasksLoading = ref(false)
const tasksLoaded = ref(false)
const taskBusy = ref('')
const taskStatusFilter = ref('all')
const taskCenterTab = ref('actions')
const autonomyData = ref({
  policy: {
    paused: false,
    autonomy_level: 'suggest',
    quiet_start_hour: 22,
    quiet_end_hour: 8,
    minimum_interval_minutes: 120,
    daily_behavior_limit: 3,
    daily_budget_yuan: 0.05,
    capability_overrides: {},
  },
  goals: [],
  events: [],
  behaviors: [],
  usage: {},
})
const autonomyLoading = ref(false)
const autonomyLoaded = ref(false)
const autonomyBusy = ref('')
const newAutonomyGoalTitle = ref('')
const newAutonomyGoalCapability = ref('follow_up_reminder')
const draft = ref('')
const sending = ref(false)
const loading = ref(true)
const errorMessage = ref('')
const chatScroll = ref(null)
const fileInput = ref(null)
const modelPicker = ref(null)
const tokenUsageDialog = ref(null)
const providerDialog = ref(null)
const modelStorageKey = isAgentWorkspace ? 'mio_agent_model_id' : 'mio_model_id'
const reasoningStorageKey = isAgentWorkspace ? 'mio_agent_reasoning_level' : 'mio_reasoning_level'
if (!isAgentWorkspace && localStorage.getItem('mio_auto_router_version') !== '1') {
  localStorage.setItem('mio_model_id', 'auto')
  localStorage.setItem('mio_reasoning_level', 'auto')
  localStorage.setItem('mio_auto_router_version', '1')
}
const reasoningLevel = ref(localStorage.getItem(reasoningStorageKey) || 'auto')
const selectedModel = ref(localStorage.getItem(modelStorageKey) || 'auto')
const chatVoiceLanguage = ref('auto')
const showModelMenu = ref(false)
const modelMenuSection = ref('root')
const contextUsage = ref({ used_chars: 0, max_chars: 18000, percent: 0, has_summary: false })
const attachments = ref([])
const isFileDragging = ref(false)
const qqBusy = ref('')
const qqQrImageUrl = ref('')
const qqQrLoading = ref(false)
const qqQrError = ref('')
const qqAccountDraft = ref('')
const qqTestTargetDraft = ref('')
const qqSetupResult = ref(null)
const startupGreetingEnabled = ref(true)
const groupChatSettings = ref({
  enabled: false,
  group_ids: [],
  mention_required: true,
  context_message_count: 0,
  active_group_count: 0,
})
const groupIdsDraft = ref('')
const companionBusy = ref('')
const petChatDraft = ref('')
const petChatSending = ref(false)
const petChatImages = ref([])
const petCallActive = ref(false)
const petCallState = ref('idle')
const petCallError = ref('')
const petCallSettings = ref({
  pet_call_asr_engine: 'auto',
  pet_call_input_language: 'zh',
  pet_call_silence_ms: 650,
  pet_call_voice_threshold: 0.018,
  pet_call_min_speech_ms: 280,
  pet_call_max_turn_seconds: 18,
})
let petCallStream = null
let petCallAudioContext = null
let petCallSource = null
let petCallProcessor = null
let petCallMonitorTimer = null
let petCallFrames = []
let petCallPreRoll = []
let petCallSpeechStartedAt = 0
let petCallSpeechCandidateStartedAt = 0
let petCallLastVoiceAt = 0
let petCallTurnPending = false
let petCallInterruptSent = false
let petCallBargeInStartedAt = 0
let petCallSessionId = ''
let petCallNextTurnId = 1
let petCallResponseId = ''
let petCallAwaitingVoiceSince = 0
const companionAvatarNonce = ref(Date.now())
const observationMode = ref('game')
const screenScope = ref('primary')
const observationInterval = ref(1000)
const gameWindows = ref([])
const selectedGameHwnd = ref('')
const gameWindowsLoading = ref(false)
const companionStatus = ref({
  pet: {
    running: false,
    avatar_available: false,
    sprite_states: [],
    sprite_set_ready: false,
    sprite_expected_count: 6,
    activity: { state: 'idle', label: '安静待机', emotion: 'neutral', emotion_label: '自然' },
    settings: {
      voice_enabled: true,
      voice_startup_enabled: false,
      voice_idle_timeout_seconds: 180,
      voice_engine: 'gpt_sovits',
      local_voice_runtime: 'genie',
      default_voice_profile_id: 'mio',
      voice_profiles: {
        mio: {
          name: 'Mio 专属音色',
          engine: 'gpt_sovits',
          gpt_sovits_ref_audio: '',
          gpt_sovits_prompt_text: '',
          gpt_sovits_prompt_language: 'ja',
          gpt_sovits_text_language: 'auto',
          gpt_sovits_translate_to_japanese: false,
          gpt_sovits_gpt_weights: '',
          gpt_sovits_sovits_weights: '',
          use_emotion_references: true,
        },
      },
      chat_model_id: 'auto',
      chat_reasoning_level: 'auto',
      pet_chat_model_id: 'auto',
      pet_chat_reasoning_level: 'auto',
      pet_call_asr_engine: 'auto',
      pet_call_input_language: 'zh',
      speech_translation_model_id: 'deepseek-v4-flash',
      pet_call_silence_ms: 650,
      pet_call_voice_threshold: 0.018,
      pet_call_min_speech_ms: 280,
      pet_call_max_turn_seconds: 18,
      voice_volume: 85,
      voice_streaming_enabled: true,
      pet_speech_language: 'zh',
      speak_proactive: false,
      speak_screen_observations: false,
      speak_game_observations: true,
      qq_voice_mode: 'adaptive',
      gpt_sovits_url: 'http://127.0.0.1:9880',
      local_voice_runtime: 'genie',
      gpt_sovits_ref_audio: '',
      gpt_sovits_prompt_text: '',
      gpt_sovits_prompt_language: 'ja',
      gpt_sovits_text_language: 'auto',
      gpt_sovits_translate_to_japanese: false,
      gpt_sovits_gpt_weights: '',
      gpt_sovits_sovits_weights: '',
      screen_ai_enabled: true,
      screen_audio_enabled: true,
      screen_audio_model: 'base',
      screen_audio_language: 'auto',
      screen_audio_chunk_seconds: 5,
      screen_vision_route: 'local',
      screen_vision_model_id: 'auto-fast',
      screen_direct_voice_enabled: true,
      screen_change_threshold: 4,
      screen_analysis_interval_seconds: 5,
      screen_request_timeout_seconds: 25,
      screen_voice_cooldown_seconds: 5,
      screen_minimum_importance: 0.62,
      screen_daily_cost_limit_yuan: 5,
      bubble_seconds: 9,
      pet_size_percent: 150,
      live2d_click_through_locked: false,
      live2d_speech_bubble_enabled: true,
      live2d_keep_visible: false,
    },
  },
  screen: { running: false, preview_available: false, change_percent: 0, title: '主屏幕', error: '', capture_backend: 'imagegrab', capture_backend_error: '', interval_ms: 1000, screen_scope: 'primary' },
  screen_analysis: {
    enabled: true,
    capture_only: false,
    vision_available: false,
    vision_model_options: [],
    selected_vision_model_id: '',
    in_progress: false,
    last_analyzed_at: '',
    last_reply: '',
    last_error: '',
    last_model: '',
    last_cost_yuan: null,
    change_threshold: 4,
    minimum_interval_seconds: 30,
    budget: {
      paused: false,
      paused_reason: '',
      session_request_count: 0,
      session_cost_yuan: 0,
      session_unknown_cost_count: 0,
      daily: { request_count: 0, total_cost_yuan: 0, unknown_cost_count: 0 },
      daily_cost_limit_yuan: 5,
    },
    pipeline_timings: {},
  },
  voice_runtime: { engine: 'gpt_sovits', engine_label: 'GPT-SoVITS', service_running: false, service_loading: false, managed_running: false, reference_ready: false, reference_name: '', prompt_ready: false, emotion_reference_ready: false, emotion_reference_count: 0, active_weights: { gpt: '', sovits: '' }, last_error: '', weights: { gpt: [], sovits: [] } },
  voice_training: {
    source_ready: false,
    environment_ready: false,
    pretrained_ready: false,
    trained_ready: false,
    model_count: 0,
    expected_model_count: 9,
    material_count: 0,
    stage: '',
    message: '',
  },
})
const companionStatusReady = ref(false)
const diaryBusy = ref('')
const memoryTab = ref('notebook')
const memoryData = ref({
  threads: [],
  summaries: [],
  structured: [],
  structured_candidates: [],
  structured_history: [],
  follow_up_results: [],
  profile: null,
  runtime_summary: null,
})
const runtimeSummaryDraft = ref('')
const newThreadContent = ref('')
const newThreadFollowUp = ref('')
const newProfileNote = ref('')
const newConversationSummary = ref('')
const newStructuredMemory = ref({ layer: 'L0', category: 'preference', memory_key: '', content: '' })
const memoryBusy = ref('')
const dailyReviews = ref([])
const weeklyReviews = ref([])
const monthlyReviews = ref([])
const selectedDailyDate = ref('')
const selectedWeeklyStart = ref('')
const selectedMonthlyMonth = ref('')
const memoryLoading = ref(false)
const memoryLoaded = ref(false)
const reviewBusy = ref('')
const copiedTurnId = ref('')
const speakingPartId = ref('')
const voiceLoadingPartId = ref('')
const modelTestBusy = ref('')
const modelTestStatus = ref({})
const showProviderPanel = ref(false)
const showTokenUsagePanel = ref(false)
const tokenUsageLoading = ref(false)
const tokenUsageData = ref({ today: {}, total: {}, days: [], logical_day_boundary_hour: 4 })
const providerBusy = ref(false)
const providerDiscoveryBusy = ref(false)
const providerDiscoveryWarning = ref('')
const providerDiscoveryMeta = ref(null)
const discoveredModels = ref([])
const providerForm = ref({
  preset_id: '',
  provider_kind: 'relay',
  provider_protocol: 'openai',
  default_api_mode: 'auto',
  provider_name: '',
  model: '',
  base_url: '',
  api_key: '',
  supports_vision: false,
  cached_input_price_cny_per_million: 0,
  input_price_cny_per_million: 0,
  output_price_cny_per_million: 0,
})
let pollTimer = null
let dashboardPollTimer = null
let qqStatusPollTimer = null
let companionPollTimer = null
let activeViewHeartbeatTimer = null
let fileDragDepth = 0
let activeMessageAudio = null
let activeMessageAudioUrl = ''
let voiceAbortController = null
let chatAbortController = null
let activeChatCancelPayload = null
let companionStatusLoading = false
let messageRefreshInFlight = false
let conversationLoadVersion = 0
let qqQrLoadVersion = 0
let tokenUsageFocusBeforeOpen = null
let providerFocusBeforeOpen = null

watch(showTokenUsagePanel, async (open) => {
  if (open) {
    tokenUsageFocusBeforeOpen = document.activeElement
    await nextTick()
    focusModal(tokenUsageDialog.value)
    return
  }
  const target = tokenUsageFocusBeforeOpen
  tokenUsageFocusBeforeOpen = null
  await nextTick()
  restoreModalFocus(target)
})

watch(showProviderPanel, async (open) => {
  if (open) {
    providerFocusBeforeOpen = document.activeElement
    await nextTick()
    focusModal(providerDialog.value)
    return
  }
  const target = providerFocusBeforeOpen
  providerFocusBeforeOpen = null
  await nextTick()
  restoreModalFocus(target)
})

function closeTokenUsagePanel() {
  showTokenUsagePanel.value = false
}

function closeProviderPanel() {
  showProviderPanel.value = false
}

function onTokenUsageDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeTokenUsagePanel()
    return
  }
  trapModalFocus(event, tokenUsageDialog.value)
}

function onProviderDialogKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault()
    closeProviderPanel()
    return
  }
  trapModalFocus(event, providerDialog.value)
}

const mainNavItems = [
  { id: 'home', label: '首页', icon: Sparkles },
  { id: 'chat', label: '对话', icon: MessageSquareText },
  { id: 'diaries', label: '日记', icon: BookOpen },
  { id: 'memory', label: '记忆', icon: Archive },
  { id: 'tasks', label: '任务', icon: ListChecks },
  { id: 'companion', label: '桌宠', icon: Gamepad2 },
  { id: 'settings', label: '设置', icon: Settings },
]
const agentNavItems = [
  { id: 'home', label: '首页', icon: Sparkles },
  { id: 'chat', label: '对话', icon: MessageSquareText },
  { id: 'tasks', label: '任务', icon: ListChecks },
  { id: 'settings', label: '设置', icon: Settings },
]
const navGroups = [{
  label: isAgentWorkspace ? 'Agent 工作台' : 'Mio 的空间',
  items: isAgentWorkspace ? agentNavItems : mainNavItems,
}]
const navItems = [
  ...navGroups.flatMap((group) => group.items),
  { id: 'stats', label: '统计', icon: ChartNoAxesCombined },
]
function isPrimaryNavActive(itemId) {
  return activeView.value === itemId
}
const settingsNavigation = [{
  label: '设置',
  items: [
    { id: 'general', label: '基础与启动', description: '启动、后台与主动消息', icon: Settings, keywords: '启动 后台 问候 主动 通知 默认页面 更新' },
    { id: 'appearance', label: '外观与界面', description: '主题、字体、侧栏与消息信息', icon: Feather, keywords: '主题 字体 侧栏 动画 专注 时间 模型 Token 费用' },
    { id: 'profile', label: '人格与关系', description: '身份、关系、表达与行为边界', icon: Heart, keywords: '人格 身份 称呼 亲密 性格 语气 情绪 行为 属性 角色卡' },
    { id: 'conversation', label: '对话与记忆', description: '上下文、联网、附件与长期记忆', icon: MessageSquareText, keywords: '对话 记忆 上下文 压缩 联网 附件 图片 PDF Word OCR' },
    { id: 'diary', label: '日记与成长', description: '自动日记、周记与成长判断', icon: CalendarDays, keywords: '日记 周记 月记 成长 每日三十 确认 备份' },
    { id: 'models', label: '模型与 API', description: '模型、供应商与自动选择', icon: Bot, keywords: '模型 API 供应商 密钥 价格 思考 自动选择' },
    { id: 'dependencies', label: '环境与模型', description: '检查本机依赖并一键安装', icon: Wrench, keywords: '环境 依赖 模型 安装 下载 GPT-SoVITS NapCat Ollama 视觉 whisper 语音 检查' },
    { id: 'qq', label: 'QQ', description: '登录、NapCat、OneBot与群聊', icon: Wifi, keywords: 'QQ NapCat OneBot 登录 二维码 私聊 群聊 语音' },
    { id: 'pet', label: '桌宠', description: '形象、语音、观察与行为', icon: Gamepad2, keywords: '桌宠 Live2D 语音 屏幕 观察 置顶 穿透 气泡 音色' },
    { id: 'data', label: '数据与隐私', description: '本地数据、导出与隐私边界', icon: Archive, keywords: '数据 隐私 导出 清理 数据库 日记 记忆' },
    { id: 'advanced', label: '高级设置', description: '服务、路径、日志与诊断', icon: Activity, keywords: '高级 服务 端口 日志 路径 诊断 恢复默认' },
  ],
}]
const recordViews = ['diaries', 'stats', 'memory']
const taskStatusFilters = [
  { id: 'all', label: '全部' },
  { id: 'running', label: '执行中' },
  { id: 'needs_confirmation', label: '待确认' },
  { id: 'executed', label: '已完成' },
  { id: 'failed', label: '异常' },
]
const autonomyLevelOptions = [
  { id: 'observe', label: '只观察' },
  { id: 'suggest', label: '主动建议' },
  { id: 'auto_low_risk', label: '低风险自动' },
  { id: 'confirm_high_risk', label: '高风险确认' },
]
const autonomyCapabilityOptions = [
  { id: 'follow_up_reminder', label: '到期跟进' },
  { id: 'daily_state', label: '今日状态' },
  { id: 'service_health', label: '服务健康' },
  { id: 'application_activity', label: '应用活动' },
  { id: 'task_result', label: '任务结果' },
  { id: 'proactive_checkin', label: '主动联系' },
  { id: 'night_close', label: '夜间收尾' },
  { id: 'screen_event', label: '重要屏幕变化' },
]
const autonomyOverrideOptions = [
  { id: '', label: '继承全局' },
  ...autonomyLevelOptions,
  { id: 'disabled', label: '关闭' },
]
const avatarUrl = computed(() => `/api/settings/avatar?v=${profileAvatarNonce.value}`)
const userAvatarUrl = computed(() => `/api/settings/user-avatar?v=${userAvatarNonce.value}`)
const chatBackgroundUrl = computed(() => `/api/settings/chat-background?v=${chatBackgroundNonce.value}`)
const chatBackgroundStyle = computed(() => chatBackgroundCustom.value
  ? { '--chat-background-image': `url("${chatBackgroundUrl.value}")` }
  : {})
const mioDisplayName = computed(() => {
  const name = mioProfileDraft.value?.identity?.name
    || memoryData.value?.profile?.identity?.name
    || 'Mio'
  return String(name).trim() || 'Mio'
})
const companionAvatarUrl = computed(() => {
  const hasIdleSprite = companionStatus.value.pet?.sprite_states?.includes('idle')
  const path = hasIdleSprite ? '/api/companion/sprite/idle' : '/api/companion/avatar'
  return `${path}?v=${companionAvatarNonce.value}`
})

const fallbackReasoningOptions = [
  { id: 'default', label: '模型默认', description: '由模型自行决定' },
]

const currentModel = computed(() => bootstrap.value?.model?.display_name || '正在读取')
const modelOptions = computed(() => bootstrap.value?.models || [])
const runtimeIdentity = computed(() => bootstrap.value?.runtime_identity || {})
const hiddenProviderNames = computed(() => bootstrap.value?.hidden_model_providers || [])
const modelGroups = computed(() => {
  const groups = new Map()
  for (const model of modelOptions.value) {
    const providerId = model.provider_id || `legacy:${model.provider_name || '其他供应商'}`
    if (!groups.has(providerId)) {
      groups.set(providerId, {
        provider_id: providerId,
        provider: model.provider_name || '其他供应商',
        models: [],
      })
    }
    groups.get(providerId).models.push(model)
  }
  return [...groups.values()]
})
const activeModel = computed(() => {
  if (selectedModel.value === 'auto') {
    return modelOptions.value.find((item) => item.id === bootstrap.value?.model?.id) || bootstrap.value?.model || null
  }
  return modelOptions.value.find((item) => item.id === selectedModel.value) || bootstrap.value?.model || null
})
const isAutoRouting = computed(() => selectedModel.value === 'auto')
const activeModelSupportsVision = computed(() => isAutoRouting.value
  ? modelOptions.value.some((item) => item.supports_vision)
  : Boolean(activeModel.value?.supports_vision))
const activeReasoningOptions = computed(() => {
  if (isAutoRouting.value) {
    return [{ id: 'auto', label: '自动', description: '根据当前话题难度选择模型原生思考方式' }]
  }
  return activeModel.value?.reasoning_options?.length
    ? activeModel.value.reasoning_options
    : fallbackReasoningOptions
})
const settingsReasoningOptions = computed(() => {
  if (chatSettingsDraft.value.model_id === 'auto') {
    return [{ id: 'auto', label: '自动', description: '根据话题难度选择模型原生思考方式' }]
  }
  const profile = modelOptions.value.find((item) => item.id === chatSettingsDraft.value.model_id)
  return profile?.reasoning_options?.length ? profile.reasoning_options : fallbackReasoningOptions
})
const petReasoningOptions = computed(() => {
  const modelId = companionStatus.value.pet?.settings?.pet_chat_model_id || 'auto'
  if (modelId === 'auto') {
    return [{ id: 'auto', label: '自动', description: '根据桌宠对话难度选择模型原生思考方式' }]
  }
  const profile = modelOptions.value.find((item) => item.id === modelId)
  return profile?.reasoning_options?.length ? profile.reasoning_options : fallbackReasoningOptions
})
const filteredSettingsNavigation = computed(() => {
  const query = settingsSearch.value.trim().toLowerCase()
  if (!query) return settingsNavigation
  return settingsNavigation
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => `${item.label} ${item.description} ${item.keywords}`.toLowerCase().includes(query)),
    }))
    .filter((group) => group.items.length)
})
const activeSettingsItem = computed(() => settingsNavigation
  .flatMap((group) => group.items)
  .find((item) => item.id === activeSettingsSection.value) || settingsNavigation[0].items[0])
const appThemeClass = computed(() => {
  const previewTheme = activeView.value === 'settings' && activeSettingsSection.value === 'appearance'
    ? appPreferencesDraft.value.theme
    : savedAppPreferences.value.theme
  return `theme-${VALID_THEME_IDS.has(previewTheme) ? previewTheme : 'mist'}`
})
const displayVisibility = computed(() => savedAppPreferences.value.visibility || DEFAULT_APP_PREFERENCES.visibility)
const homeWidgetPreferences = computed(() => savedAppPreferences.value.home_widgets || DEFAULT_APP_PREFERENCES.home_widgets)
const displayMode = computed(() => savedAppPreferences.value.display_mode || 'full')
const structuredMemoryCount = computed(() => (
  (memoryData.value?.structured?.length || 0) + (memoryData.value?.summaries?.length || 0)
))
const agentRuntimeLabel = computed(() => {
  if (sending.value) return '正在思考'
  if (agentTasks.value.some((item) => ['queued', 'running'].includes(item.status))) return '正在执行'
  return '空闲'
})

const companionSettingKeys = {
  pet: [
    'bubble_seconds', 'pet_size_percent', 'pet_renderer', 'live2d_model_id', 'live2d_scale',
    'live2d_vertical_offset', 'live2d_follow_cursor', 'live2d_idle_motion', 'live2d_click_motion',
    'live2d_smart_passthrough', 'live2d_click_through_locked', 'live2d_speech_bubble_enabled',
    'live2d_keep_visible',
    'live2d_always_on_top', 'live2d_disable_gpu', 'live2d_motion_slots', 'live2d_expression_slots',
    'pet_chat_model_id', 'pet_chat_reasoning_level',
    'pet_call_asr_engine', 'pet_call_input_language', 'speech_translation_model_id', 'pet_call_silence_ms', 'pet_call_voice_threshold',
    'pet_call_min_speech_ms', 'pet_call_max_turn_seconds',
    'voice_enabled', 'voice_startup_enabled', 'voice_idle_timeout_seconds', 'voice_engine', 'local_voice_runtime', 'voice_volume', 'voice_streaming_enabled', 'speak_proactive',
    'default_voice_profile_id', 'voice_profiles',
    'pet_speech_language', 'qq_voice_mode', 'gpt_sovits_url', 'gpt_sovits_text_language',
    'gpt_sovits_translate_to_japanese', 'screen_ai_enabled', 'screen_audio_enabled',
    'screen_audio_model', 'screen_audio_language', 'screen_audio_chunk_seconds', 'screen_vision_route',
    'screen_vision_model_id', 'screen_direct_voice_enabled', 'screen_change_threshold',
    'screen_analysis_interval_seconds', 'screen_request_timeout_seconds', 'screen_voice_cooldown_seconds',
    'screen_minimum_importance', 'screen_daily_cost_limit_yuan', 'speak_screen_observations',
    'speak_game_observations',
  ],
  voice: [
    'voice_enabled', 'voice_startup_enabled', 'voice_idle_timeout_seconds', 'voice_engine', 'local_voice_runtime', 'voice_volume', 'voice_streaming_enabled', 'speak_proactive',
    'default_voice_profile_id', 'voice_profiles',
    'pet_speech_language', 'speech_translation_model_id',
    'qq_voice_mode', 'gpt_sovits_url', 'gpt_sovits_text_language', 'gpt_sovits_translate_to_japanese',
    'cloud_tts_api_key', 'cloud_tts_app_id', 'cloud_tts_speaker', 'cloud_tts_speech_rate',
  ],
  observation: [
    'screen_ai_enabled', 'screen_audio_enabled', 'screen_audio_model', 'screen_audio_language',
    'screen_audio_chunk_seconds', 'screen_vision_route', 'screen_vision_model_id', 'screen_direct_voice_enabled',
    'screen_change_threshold', 'screen_analysis_interval_seconds', 'screen_request_timeout_seconds',
    'screen_voice_cooldown_seconds',
    'screen_minimum_importance', 'screen_daily_cost_limit_yuan', 'speak_screen_observations',
    'speak_game_observations',
  ],
}

const runtimeSettingKeysBySection = {
  general: [
    'qq_proactive_enabled', 'qq_proactive_min_idle_minutes', 'qq_proactive_max_idle_minutes',
    'qq_proactive_day_start_hour', 'qq_proactive_day_end_hour', 'qq_proactive_check_seconds',
  ],
  conversation: [
    'chat_history_limit', 'chat_raw_history_limit', 'chat_context_max_chars', 'chat_recent_keep_messages',
    'memory_context_days', 'memory_context_max_chars', 'memory_context_messages_per_day',
    'chat_follow_up_capture_seconds', 'chat_follow_up_max_capture_count',
    'agent_attachment_max_count', 'agent_text_attachment_max_chars', 'agent_document_attachment_max_bytes',
    'agent_pdf_max_pages', 'agent_document_vision_max_pages', 'photo_archive_enabled',
    'web_search_enabled', 'web_search_max_results', 'web_search_timeout_seconds', 'web_page_max_chars',
  ],
  diary: [
    'daily_diary_auto_enabled', 'daily_diary_check_seconds', 'daily_diary_model_id', 'weekly_review_enabled',
    'weekly_review_hour', 'weekly_review_notify_qq', 'weekly_review_check_seconds',
    'monthly_review_enabled', 'monthly_review_hour', 'monthly_review_notify_qq', 'monthly_review_check_seconds',
    'daily_review_auto_enabled', 'daily_review_auto_hour', 'daily_review_auto_minute', 'daily_review_auto_notify_qq', 'daily_review_check_seconds',
    'backup_enabled', 'backup_keep_count', 'backup_max_total_mb', 'backup_check_seconds', 'day_boundary_hour',
    'night_close_enabled', 'night_close_start_hour', 'night_close_end_hour', 'night_close_min_quiet_minutes',
  ],
  qq: [
    'qq_bot_enabled', 'qq_allowed_user_ids', 'qq_image_enabled', 'qq_image_max_count',
    'qq_image_max_bytes', 'qq_image_detail', 'qq_image_send_to_model',
    'qq_message_debounce_seconds', 'qq_message_incomplete_debounce_seconds',
    'qq_delivery_ack_timeout_seconds', 'qq_delivery_max_retries',
    'qq_reply_initial_delay_seconds', 'qq_reply_delay_seconds',
  ],
  advanced: [
    'openai_timeout_seconds', 'openai_request_deadline_seconds', 'screen_reaction_timeout_seconds', 'screen_history_retention_days',
    'screen_history_max_rows', 'manual_max_chars', 'chat_temperature', 'action_planner_temperature',
    'timezone', 'napcat_dir', 'napcat_webui_url', 'voice_training_dir', 'local_vision_dir',
  ],
}

const privateRuntimePathKeys = [
  'persona_prompt_path',
  'runtime_summary_path',
  'personal_manual_path',
  'talent_manual_path',
]

function serializeSettings(value) {
  return JSON.stringify(value ?? null)
}

function rangeInputStyle(value, minimum, maximum) {
  const min = Number(minimum)
  const max = Number(maximum)
  const current = Number(value)
  const percent = max > min ? Math.max(0, Math.min(100, ((current - min) / (max - min)) * 100)) : 0
  return { '--range-progress': `${percent}%` }
}

function companionSettingsSnapshot(section, source = companionStatus.value.pet?.settings || {}) {
  return serializeSettings(Object.fromEntries((companionSettingKeys[section] || []).map((key) => [key, source[key]])))
}

function runtimeSettingsSnapshot(section, source = runtimeSettingsDraft.value) {
  return serializeSettings(Object.fromEntries(
    (runtimeSettingKeysBySection[section] || []).map((key) => [key, source?.[key]]),
  ))
}

function savedRuntimeSettingsSource() {
  try { return JSON.parse(savedRuntimeSettings.value || '{}') || {} } catch (_) { return {} }
}

function normalizedGroupChatSnapshot() {
  const groupIds = groupIdsDraft.value
    .replaceAll('，', ',')
    .replaceAll('；', ',')
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean)
  return serializeSettings({
    enabled: Boolean(groupChatSettings.value.enabled),
    mention_required: Boolean(groupChatSettings.value.mention_required),
    group_ids: [...new Set(groupIds)].sort(),
  })
}

function isSettingsSectionDirty(section) {
  if (section === 'general') {
    return startupGreetingEnabled.value !== savedStartupGreeting.value
      || (desktopPreferencesReady.value && serializeSettings(desktopPreferencesDraft.value) !== savedDesktopPreferences.value)
      || appPreferencesDraft.value.default_open_page !== savedAppPreferences.value.default_open_page
      || runtimeSettingsSnapshot('general') !== runtimeSettingsSnapshot('general', savedRuntimeSettingsSource())
  }
  if (section === 'appearance') {
    const keys = ['theme', 'font_size', 'left_sidebar_visible', 'right_sidebar_visible', 'left_sidebar_hover_expand', 'right_sidebar_hover_expand', 'remember_sidebar_state', 'light_animations', 'focus_mode']
    return serializeSettings({ values: Object.fromEntries(keys.map((key) => [key, appPreferencesDraft.value[key]])), visibility: appPreferencesDraft.value.visibility })
      !== serializeSettings({ values: Object.fromEntries(keys.map((key) => [key, savedAppPreferences.value[key]])), visibility: savedAppPreferences.value.visibility })
  }
  if (section === 'conversation') return serializeSettings(chatSettingsDraft.value) !== savedChatSettings.value
    || runtimeSettingsSnapshot('conversation') !== runtimeSettingsSnapshot('conversation', savedRuntimeSettingsSource())
  if (section === 'diary') return runtimeSettingsReady.value
    && runtimeSettingsSnapshot('diary') !== runtimeSettingsSnapshot('diary', savedRuntimeSettingsSource())
  if (section === 'profile') return mioProfileReady.value && serializeSettings({ profile: mioProfileDraft.value, notes: mioProfileNotesDraft.value, avoid: mioProfileAvoidDraft.value }) !== savedMioProfile.value
  if (section === 'qq') return normalizedGroupChatSnapshot() !== savedGroupChatSettings.value
    || qqStartupEnabled.value !== savedQqStartupEnabled.value
    || runtimeSettingsSnapshot('qq') !== runtimeSettingsSnapshot('qq', savedRuntimeSettingsSource())
  if (section === 'advanced') return runtimeSettingsReady.value
    && runtimeSettingsSnapshot('advanced') !== runtimeSettingsSnapshot('advanced', savedRuntimeSettingsSource())
  if (section === 'pet') {
    if (!companionStatusReady.value) return false
    return companionSettingsSnapshot(section) !== savedCompanionSettings.value[section]
  }
  return false
}
const compactActiveModelLabel = computed(() => isAutoRouting.value
  ? '自动'
  : compactModelLabel(activeModel.value || currentModel.value))
const compactCurrentReasoningLabel = computed(() => isAutoRouting.value
  ? '自动'
  : compactReasoningLabel(reasoningLevel.value))
const contextPercent = computed(() => Math.max(0, Math.min(100, Number(contextUsage.value?.percent || 0))))
const contextRingStyle = computed(() => ({ '--context-percent': `${contextPercent.value}%` }))
const contextUsageLabel = computed(() => {
  const used = Number(contextUsage.value?.used_chars || 0).toLocaleString('zh-CN')
  const limit = Number(contextUsage.value?.max_chars || 18000).toLocaleString('zh-CN')
  return `当前上下文：${used} / ${limit} 字（${contextPercent.value.toFixed(1)}%）`
})
const selectedConversation = computed(() => conversations.value.find((item) => item.id === selectedConversationId.value) || null)
const chatSubtitle = computed(() => {
  if (isAgentWorkspace) return '独立 Agent 工作对话 · 模型负责规划，运行层负责验证'
  if (selectedConversation.value?.kind === 'qq') return `${logicalDate.value} · 与QQ共享上下文`
  if (selectedConversation.value?.kind === 'pet') return `${logicalDate.value} · 桌宠独立上下文 · 自动语音回复`
  return `${logicalDate.value} · 独立桌面对话`
})
const qqStatus = computed(() => bootstrap.value?.qq || {})
const qqConnected = computed(() => Boolean(bootstrap.value?.qq?.connected))
const qqStatusLabel = computed(() => {
  if (qqConnected.value) return '在线'
  if (qqStatus.value.diagnostic_code === 'account_mismatch') return '账号不一致'
  if (qqStatus.value.diagnostic_code === 'login_required') return 'QQ未登录'
  if (qqStatus.value.diagnostic_code === 'onebot_disconnected') return '消息通道未连接'
  if (qqStatus.value.websocket_connected) return '仅桥接'
  if (qqStatus.value.webui_reachable) return 'NapCat已启动'
  return '离线'
})
const qqStatusCopy = computed(() => {
  if (qqConnected.value) return 'QQ消息正在同步到当前会话。'
  const runtime = qqStatus.value.proactive_runtime || {}
  const nextPrompt = runtime.next_prompt_at ? `主动消息计划：${formatShortTime(runtime.next_prompt_at)}` : ''
  const reason = runtime.app_active ? 'QQ离线不影响应用内主动消息。' : '应用关闭时会暂停主动消息，重新打开后立即检查。'
  return [qqStatus.value.diagnostic_message || 'QQ通道未连接，桌面聊天不受影响。', nextPrompt, reason].filter(Boolean).join(' ')
})
const qqDiagnosticItems = computed(() => [
  { label: '控制脚本', ok: Boolean(qqStatus.value.control_scripts_ready) },
  { label: 'NapCat文件', ok: Boolean(qqStatus.value.napcat_executable_exists) },
  { label: 'NapCat进程', ok: Boolean(qqStatus.value.napcat_process_running) },
  { label: '机器人QQ', ok: Boolean(qqStatus.value.qq_process_running) },
  { label: 'WebUI配置', ok: Boolean(qqStatus.value.webui_config_ready) },
  { label: 'NapCat在线', ok: Boolean(qqStatus.value.webui_reachable) },
  { label: 'QQ登录', ok: Boolean(qqStatus.value.logged_in) },
  { label: 'OneBot连接', ok: Boolean(qqStatus.value.websocket_connected) },
])
const logicalDate = computed(() => bootstrap.value?.logical_date || '')
const attachmentLimits = computed(() => ({
  maxCount: Number(bootstrap.value?.attachment_limits?.max_count || 5),
  imageMaxBytes: Number(bootstrap.value?.attachment_limits?.image_max_bytes || 8 * 1024 * 1024),
  documentMaxBytes: Number(bootstrap.value?.attachment_limits?.document_max_bytes || 20 * 1024 * 1024),
  textMaxBytes: Number(bootstrap.value?.attachment_limits?.text_max_bytes || 512 * 1024),
  textMaxChars: Number(bootstrap.value?.attachment_limits?.text_max_chars || 200000),
}))
const todayState = computed(() => bootstrap.value?.today_state || {})
const timeGreeting = computed(() => {
  const hour = new Date().getHours()
  if (hour < 4) return '夜深了'
  if (hour < 11) return '早上好'
  if (hour < 14) return '中午好'
  if (hour < 18) return '下午好'
  if (hour < 23) return '晚上好'
  return '夜深了'
})
const statusMoodLabel = computed(() => String(todayState.value.mood || '安静陪伴').trim())
function compactProfileText(value, fallback, maxLength) {
  const firstPhrase = String(value || '').trim().split(/[；;，,。\n]/)[0]?.trim() || fallback
  return firstPhrase.length > maxLength ? `${firstPhrase.slice(0, maxLength)}…` : firstPhrase
}
const preferredUserAddress = computed(() => compactProfileText(
  memoryData.value?.profile?.preferences?.user_address,
  '你',
  8,
))
const relationshipDistanceLabel = computed(() => {
  const source = String(memoryData.value?.profile?.preferences?.relationship_distance || '').trim()
  const knownRelations = ['青梅竹马', '小女友', '女朋友', '恋人', '搭档', '朋友', '家人']
  const matched = knownRelations.filter((label) => source.includes(label))
  return [...new Set(matched)].slice(0, 2).join(' · ') || compactProfileText(source, '自然亲近', 12)
})
const activeViewSubtitle = computed(() => ({
  home: '回到我们今天的生活',
  chat: chatSubtitle.value,
  diaries: '把今天认真地留住',
  stats: '看看最近走过的路',
  memory: 'Mio 记得的事情都在这里',
  tasks: '一起把想法变成行动',
    companion: '让 Mio 来到你的桌面',
  settings: activeSettingsItem.value.description,
}[activeView.value] || '今天也陪你一起生活'))
const stateHistory = computed(() => bootstrap.value?.state_history || [])
const autoDiaryStatus = computed(() => bootstrap.value?.auto_diary || {})
const stateAnalyzeBusy = ref(false)
const moodScore = computed(() => Math.max(0, Math.min(5, Number(todayState.value.mood_score || 0))))
const moodTrend = computed(() => {
  if (!logicalDate.value) return []
  const states = new Map(stateHistory.value.map((item) => [item.date, item]))
  const current = new Date(`${logicalDate.value}T00:00:00Z`)
  return Array.from({ length: 7 }, (_, index) => {
    const target = new Date(current)
    target.setUTCDate(current.getUTCDate() - (6 - index))
    const date = target.toISOString().slice(0, 10)
    const state = states.get(date) || {}
    const score = Math.max(0, Math.min(5, Number(state.mood_score || 0)))
    return {
      date,
      label: `${target.getUTCMonth() + 1}/${target.getUTCDate()}`,
      score,
      mood: state.mood || '',
      isToday: date === logicalDate.value,
    }
  })
})
const todayStateDetails = computed(() => [
  { key: 'events', label: '今日主线', value: todayState.value.key_events, tone: 'main' },
  { key: 'adjustment', label: '可以调整', value: todayState.value.avoidance_signals, tone: 'warning' },
  { key: 'action', label: '下一步', value: todayState.value.next_min_action, tone: 'action' },
].filter((item) => String(item.value || '').trim()))
const totalTokenUsage = computed(() => Number(
  bootstrap.value?.total_token_usage
  || messages.value.reduce((total, item) => total + Number(item.prompt_tokens || 0) + Number(item.completion_tokens || 0), 0),
))
const todayTokenUsage = computed(() => Number(
  bootstrap.value?.today_token_usage?.total_tokens
  || tokenUsageData.value.today?.total_tokens
  || 0,
))
const latestAssistantNotice = computed(() => {
  const message = [...messages.value].reverse().find((item) => item.role === 'assistant' && String(item.content || '').trim())
  if (!message) return null
  return {
    content: cleanDisplayContent(message.content),
    created_at: message.created_at,
  }
})
const autoDiaryStatusLabel = computed(() => {
  const status = autoDiaryStatus.value
  if (!status.enabled) return '自动日记已关闭'
  const date = String(status.target_date || '').slice(5) || '上一记录日'
  return {
    generated: `${date} 已自动补生成`,
    already_exists: `${date} 已完成自动检查`,
    no_content: `${date} 没有可生成的内容`,
    skipped: `${date} 已跳过重复生成`,
    error: `${date} 自动生成失败`,
    generating: `正在补写 ${date} 的日记`,
    paused: '隐私暂停中，自动日记已停止',
    cancelled: '本次日记生成已取消，稍后重新检查',
    not_checked: '等待首次自动检查',
  }[status.result] || `${date} 自动检查完成`
})
const statsSummary = computed(() => statsData.value.summary || {})
const statsMonthLabel = computed(() => statsData.value.year && statsData.value.month
  ? `${statsData.value.year} 年 ${statsData.value.month} 月`
  : '正在读取')
const statsCalendarCells = computed(() => {
  const year = Number(statsData.value.year || 0)
  const month = Number(statsData.value.month || 0)
  if (!year || !month) return []
  const entryMap = new Map((statsData.value.calendar || []).map((item) => [item.date, item]))
  const firstDay = new Date(Date.UTC(year, month - 1, 1))
  const offset = (firstDay.getUTCDay() + 6) % 7
  const daysInMonth = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const cells = Array.from({ length: offset }, (_, index) => ({ key: `blank-${index}`, blank: true }))
  for (let day = 1; day <= daysInMonth; day += 1) {
    const date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    const entry = entryMap.get(date)
    cells.push({
      key: date,
      blank: false,
      day,
      date,
      title: entry?.title || '',
      status: date > statsData.value.logical_date ? 'future' : (entry?.daily_thirty_status || 'unknown'),
      hasDiary: Boolean(entry),
      isToday: date === statsData.value.logical_date,
    })
  }
  return cells
})
const statsDistribution = computed(() => {
  const byStatus = statsSummary.value.by_status || {}
  const config = [
    { key: 'done', label: '完成', color: '#388166' },
    { key: 'partial', label: '部分完成', color: '#b17a32' },
    { key: 'missed', label: '未完成', color: '#b24b4b' },
    { key: 'unknown', label: '未记录', color: '#94a0a9' },
  ]
  const total = config.reduce((sum, item) => sum + Number(byStatus[item.key] || 0), 0)
  let offset = 0
  return config.map((item) => {
    const value = Number(byStatus[item.key] || 0)
    const percent = total ? (value / total) * 100 : 0
    const segment = { ...item, value, percent, offset, total }
    offset += percent
    return segment
  })
})
const statsMoodSeries = computed(() => {
  const logical = statsData.value.logical_date
  if (!logical) return []
  const byDate = new Map((statsData.value.mood_trend || []).map((item) => [item.date, item]))
  const current = new Date(`${logical}T00:00:00Z`)
  return Array.from({ length: 30 }, (_, index) => {
    const target = new Date(current)
    target.setUTCDate(current.getUTCDate() - (29 - index))
    const date = target.toISOString().slice(0, 10)
    const item = byDate.get(date) || {}
    const score = Math.max(0, Math.min(5, Number(item.mood_score || 0)))
    return {
      date,
      mood: item.mood || '',
      score,
      x: 42 + (index / 29) * 638,
      y: score ? 18 + ((5 - score) / 4) * 150 : 0,
    }
  })
})
const statsMoodPoints = computed(() => statsMoodSeries.value.filter((item) => item.score))
const statsMoodPath = computed(() => statsMoodPoints.value
  .map((item, index) => `${index ? 'L' : 'M'}${item.x.toFixed(1)} ${item.y.toFixed(1)}`)
  .join(' '))
const visibleMessages = computed(() => messages.value
  .map((item) => ({ ...item, content: cleanDisplayContent(item.content) }))
  .filter((item) => item.content || item.attachments?.length))
const messageTurns = computed(() => {
  const turns = []
  for (const message of visibleMessages.value) {
    const previous = turns.at(-1)
    const currentTime = new Date(message.created_at).getTime()
    const previousTime = new Date(previous?.created_at).getTime()
    const isNearbyLegacyMessage = !message.request_id
      && !previous?.request_id
      && message.source === previous?.source
      && Number.isFinite(currentTime)
      && Number.isFinite(previousTime)
      && currentTime - previousTime >= 0
      && currentTime - previousTime <= 60_000
    const sameAssistantTurn = message.role === 'assistant'
      && previous?.role === 'assistant'
      && (
        (message.request_id && message.request_id === previous.request_id)
        || isNearbyLegacyMessage
      )
    if (sameAssistantTurn) {
      previous.parts.push(message)
      previous.created_at = message.created_at
      previous.model_id = message.model_id || previous.model_id
      previous.reasoning_level = message.reasoning_level || previous.reasoning_level
      previous.prompt_tokens = message.prompt_tokens || previous.prompt_tokens
      previous.cached_prompt_tokens = message.cached_prompt_tokens || previous.cached_prompt_tokens
      previous.completion_tokens = message.completion_tokens || previous.completion_tokens
      previous.reasoning_tokens = message.reasoning_tokens || previous.reasoning_tokens
      if (
        message.request_cost_yuan !== null
        && message.request_cost_yuan !== undefined
        && message.request_cost_source !== 'shared_request'
      ) {
        previous.request_cost_yuan = message.request_cost_yuan
      }
      if (message.request_cost_source && message.request_cost_source !== 'shared_request') {
        previous.request_cost_source = message.request_cost_source
      }
      continue
    }
    turns.push({ ...message, parts: [message] })
  }
  return turns
})
const filteredAgentTasks = computed(() => {
  if (taskStatusFilter.value === 'all') return agentTasks.value
  if (taskStatusFilter.value === 'running') {
    return agentTasks.value.filter((item) => ['queued', 'running'].includes(item.status))
  }
  if (taskStatusFilter.value === 'failed') {
    return agentTasks.value.filter((item) => ['failed', 'skipped', 'cancelled'].includes(item.status))
  }
  return agentTasks.value.filter((item) => item.status === taskStatusFilter.value)
})
const taskSummary = computed(() => ({
  total: agentTasks.value.length,
  running: agentTasks.value.filter((item) => ['queued', 'running'].includes(item.status)).length,
  pending: agentTasks.value.filter((item) => item.status === 'needs_confirmation').length,
  completed: agentTasks.value.filter((item) => item.status === 'executed').length,
  failed: agentTasks.value.filter((item) => ['failed', 'skipped', 'cancelled'].includes(item.status)).length,
}))
const dailyReviewItems = computed(() => {
  const reviewMap = new Map(dailyReviews.value.map((item) => [item.date, item]))
  const items = diaries.value.map((diary) => ({
    date: diary.date,
    title: diary.title,
    review: reviewMap.get(diary.date) || null,
  }))
  for (const review of dailyReviews.value) {
    if (!items.some((item) => item.date === review.date)) {
      items.push({ date: review.date, title: `${review.date} 的回顾`, review })
    }
  }
  return items.sort((a, b) => b.date.localeCompare(a.date))
})
const selectedDailyReview = computed(() => dailyReviews.value.find((item) => item.date === selectedDailyDate.value) || null)
const weeklyReviewItems = computed(() => {
  const items = [...weeklyReviews.value]
  const currentWeek = weekStartFor(logicalDate.value)
  if (currentWeek && !items.some((item) => item.week_start === currentWeek)) {
    items.unshift({ week_start: currentWeek, week_end: weekEndFor(currentWeek), markdown_content: '' })
  }
  return items.sort((a, b) => b.week_start.localeCompare(a.week_start))
})
const selectedWeeklyReview = computed(() => weeklyReviewItems.value.find((item) => item.week_start === selectedWeeklyStart.value) || null)
const monthlyReviewItems = computed(() => monthlyCatalog(monthlyReviews.value, logicalDate.value))
const selectedMonthlyReview = computed(() => monthlyReviewItems.value.find((item) => item.month === selectedMonthlyMonth.value) || null)
const profileRows = computed(() => {
  const profile = memoryData.value.profile
  if (!profile) return []
  return [
    ['人格核心', profile.identity?.core],
    ['说话方式', profile.speaking_style?.tone],
    ['气泡习惯', profile.speaking_style?.bubble_style],
    ['对你的称呼', profile.preferences?.user_address],
    ['相处距离', profile.preferences?.relationship_distance],
  ].filter(([, value]) => value)
})


function ensureReasoningForActiveModel() {
  if (isAutoRouting.value) {
    reasoningLevel.value = 'auto'
    persistReasoning()
    return
  }
  const optionIds = new Set(activeReasoningOptions.value.map((item) => item.id))
  const legacy = { fast: 'low', standard: 'medium', deep: 'high' }
  const migrated = legacy[reasoningLevel.value]
  if (migrated && optionIds.has(migrated)) reasoningLevel.value = migrated
  if (!optionIds.has(reasoningLevel.value)) {
    reasoningLevel.value = activeModel.value?.default_reasoning_level || activeReasoningOptions.value[0]?.id || 'default'
  }
  persistReasoning()
}


const toolReceiptLabels = {
  get_self_state: '读取自我状态',
  list_capabilities: '读取能力清单',
  get_active_view: '读取当前页面',
  get_service_health: '检查服务状态',
  explain_last_route: '读取最近路由',
  get_today_state: '读取今日状态',
  search_web: '联网查证',
  search_memory: '检索记忆',
  get_diary: '读取日记',
  creation_list_presets: '读取创作预设',
  creation_list_loras: '读取 LoRA 用途',
  creation_list_assets: '读取参考素材',
  creation_check_workflow: '检查创作工作流',
  creation_inspect_workflow: '研究工作流与节点',
  creation_read_workflow_source: '读取节点说明与源码',
  creation_read_workflow_reference: '查阅工作流参考文档',
  creation_remember_workflow: '保存工作流理解',
  comfyui_generate_image: '创建图片任务',
  comfyui_generate_video: '创建视频任务',
  remote_generate_image: '创建远程图片任务',
  creation_get_job: '读取创作任务',
  creation_get_output: '读取创作结果',
  creation_cancel_job: '取消创作任务',
  add_diary_material: '添加日记素材',
  set_daily_thirty: '更新每日三十',
  set_daily_mood: '更新今日情绪',
  update_today_state: '更新今日状态',
  remember_thread: '创建待跟进事项',
  resolve_thread: '完成待跟进事项',
  remember_memory: '保存记忆',
  edit_today_diary: '修改今日日记',
  generate_today_diary: '生成今日日记',
  update_profile: '更新 Mio 属性',
}


async function request(url, options = {}) {
  return apiRequest(url, options)
}


const petCallStateLabel = computed(() => ({
  idle: '语音聊天',
  connecting: '正在接通',
  listening: '正在听',
  thinking: 'Mio 在想',
  waiting_voice: '准备声音',
  speaking: 'Mio 在说',
  error: '通话异常',
}[petCallState.value] || '语音聊天'))


function showSettingsFeedback(section, type, message) {
  settingsFeedback.value = { section, type, message }
  window.setTimeout(() => {
    if (settingsFeedback.value.section === section && settingsFeedback.value.message === message) {
      settingsFeedback.value = { section: '', type: '', message: '' }
    }
  }, 3200)
}


provide('mio-settings-page', reactive({
  activeSettingsItem,
  activeSettingsSection,
  filteredSettingsNavigation,
  avatarUrl,
  chatBackgroundCustom,
  chatBackgroundUrl,
  appPreferencesDraft,
  themeOptions: THEME_OPTIONS,
  applyDisplayMode,
  chatSettingsDraft,
  clearGroupChatContext,
  companionAvatarUrl,
  companionBusy,
  companionStatus,
  companionStatusReady,
  contextUsageLabel,
  controlCompanion,
  controlQq,
  controlVoiceRuntime,
  desktopPreferencesBusy,
  desktopPreferencesDraft,
  desktopPreferencesReady,
  exportVoicePackage,
  dataPrivacyBusy,
  dataPrivacyLoading,
  dataPrivacyState,
  createDataBackup,
  cleanupDataBackups,
  deleteDataBackup,
  importDataBackup,
  deleteProvider,
  deleteProviderGroup,
  displayModeOptions,
  groupChatSettings,
  groupIdsDraft,
  handleSettingsModelChange,
  handlePetModelChange,
  homeWidgetOptions,
  importLive2DModel,
  importVoicePackage,
  isSettingsSectionDirty,
  deleteLive2DModel,
  replaceLive2DPreview,
  modelGroups,
  hiddenProviderNames,
  modelOptions,
  petReasoningOptions,
  modelTestBusy,
  modelTestStatus,
  openProviderPanel,
  openSettingsSection,
  pricingSourceLabel,
  providerBusy,
  restoreProvider,
  runtimeIdentity,
  qqBusy,
  qqAccountDraft,
  qqConnected,
  qqDiagnosticItems,
  qqQrError,
  qqQrImageUrl,
  qqQrLoading,
  qqSetupResult,
  qqStartupEnabled,
  qqStatus,
  qqStatusCopy,
  qqStatusLabel,
  qqTestTargetDraft,
  runtimeSettingsBusy,
  runtimeSettingsDraft,
  runtimeSettingsReady,
  webSearchTestBusy,
  webSearchTestQuery,
  webSearchTestResult,
  testWebSearch,
  rangeInputStyle,
  resetActiveSettings,
  mioProfileBusy,
  mioProfileDraft,
  mioProfileAvoidDraft,
  mioProfileNotesDraft,
  mioProfileReady,
  mioDisplayName,
  profileAvatarCustom,
  profileBehaviorLabels,
  resetProfileAvatar,
  resetAppearanceImage,
  restoreDataBackup,
  saveActiveSettings,
  saveCompanionSettings,
  saveCompanionSize,
  selectedModel,
  settingsFeedback,
  settingsSearch,
  settingsReasoningOptions,
  startupGreetingEnabled,
  testCompanionVoice,
  testCompanionVoiceProfile,
  testModel,
  setupQqChannel,
  testQqDelivery,
  togglePrivacyPause,
  uploadCompanionSpriteSheet,
  uploadVoiceReference,
  uploadProfileAvatar,
  uploadAppearanceImage,
  userAvatarCustom,
  userAvatarUrl,
  visibilityOptions,
}))

provide('mio-companion-page', reactive({
  activeCompanionSection,
  companionAvatarUrl,
  companionBusy,
  companionStatus,
  controlCompanion,
  controlLocalVision,
  controlObservation,
  controlVoiceRuntime,
  controlVoiceTraining,
  formatRealTime,
  gameWindows,
  gameWindowsLoading,
  letMioSeeObservation,
  mioDisplayName,
  loadGameWindows,
  observationInterval,
  observationMode,
  openScreenPreviewWindow,
  openSettingsSection,
  rangeInputStyle,
  previewLive2DMotion,
  previewLive2DExpression,
  pullLocalVisionModel,
  saveCompanionSettings,
  saveCompanionSize,
  screenScope,
  selectedGameHwnd,
  testCompanionVoice,
  uploadCompanionSpriteSheet,
}))

provide('mio-chat-page', reactive({
  isAgentWorkspace,
  agentCreationCost,
  agentExecutionElapsed,
  agentExecutionProgress,
  agentExecutionStatus,
  activeModelSupportsVision,
  activeReasoningOptions,
  analyzeTodayState,
  activeView,
  attachments,
  autoDiaryStatus,
  autoDiaryStatusLabel,
  avatarUrl,
  cancelActiveChat,
  chatBackgroundStyle,
  chooseModel,
  chooseReasoning,
  compactActiveModelLabel,
  messageModelLabel,
  compactCurrentReasoningLabel,
  latestTaskHandoff,
  openAgentHandoff,
  conversations,
  createNewConversation,
  contextPercent,
  contextRingStyle,
  contextUsage,
  contextUsageLabel,
  controlQq,
  copiedTurnId,
  copyTurn,
  costSourceLabel,
  diaryBusy,
  diaries,
  diarySearch,
  displayMode,
  displayVisibility,
  draft,
  fileInput,
  formatCost,
  formatFileSize,
  formatRealTime,
  formatShortTime,
  generateTodayDiary,
  handleComposerKeydown,
  handleComposerPaste,
  handleFileDragEnter,
  handleFileDragLeave,
  handleFileDragOver,
  handleFileDrop,
  handleFileSelection,
  isAutoRouting,
  isFileDragging,
  isAgentWorkspace,
  loading,
  logicalDate,
  agentRuntimeLabel,
  messageTurns,
  mioDisplayName,
  modelGroups,
  openSettingsSection,
  modelMenuSection,
  modelPicker,
  moodScore,
  moodTrend,
  openDiary,
  playMessageVoice,
  qqBusy,
  qqConnected,
  qqStatusCopy,
  qqStatusLabel,
  reasoningLabel,
  reasoningLevel,
  removeAttachment,
  renameConversation,
  requestCostDetails,
  rightSidebarVisible,
  sendMessage,
  selectConversation,
  selectedModel,
  selectedConversationId,
  sending,
  showModelMenu,
  sourceLabel,
  speakingPartId,
  statusLabel,
  stateAnalyzeBusy,
  structuredMemoryCount,
  todayState,
  todayStateDetails,
  toggleModelMenu,
  turnAttachments,
  turnId,
  turnToolReceipts,
  toolReceiptLabel,
  toolReceiptStatus,
  toolReceiptTitle,
  turnCreationJob,
  turnCreationLoras,
  turnCreationPrompt,
  turnCreationSampler,
  turnCreationWorkflow,
  turnTokenCount,
  turnVoiceLanguageLabel,
  visibleMessages,
  voiceLoadingPartId,
  userAvatarCustom,
  userAvatarUrl,
  chatScroll,
  deleteConversation,
  deleteConversationMessages,
  formatSidebarTime,
}))

provide('mio-records-page', reactive({
  activeView,
  autoDiaryStatus,
  autoDiaryStatusLabel,
  async retryAutoDiary() {
    try {
      await request('/api/diary/automatic/check', { method: 'POST', body: '{}' })
      await refreshDayDashboard()
    } catch (error) { errorMessage.value = error.message }
  },
  openDiarySettings() {
    return openSettingsSection('diary')
  },
  addConversationSummary,
  addMemoryThread,
  addProfileNote,
  addStructuredMemory,
  approveAutonomyBehavior,
  approveAgentTask,
  autonomyBusy,
  autonomyCapabilityOptions,
  autonomyData,
  autonomyDeliveryLabel,
  autonomyLevelOptions,
  autonomyLoaded,
  autonomyLoading,
  autonomyOverrideOptions,
  autonomyStatusLabel,
  archiveStructuredMemory,
  confirmMemoryCandidate,
  cancelAgentTask,
  cancelAutonomyBehavior,
  changeStatsMonth,
  confirmDiary,
  dailyReviewItems,
  dailyReviews,
  deleteConversationSummary,
  deleteMemoryThread,
  deleteProfileNote,
  diaries,
  diaryBusy,
  diarySearch,
  filteredAgentTasks,
  formatRealTime,
  formatShortTime,
  generateDailyReview,
  generateTodayDiary,
  generateWeekly,
  generateMonthly,
  createAutonomyGoal,
  loadAutonomy,
  loadAgentTasks,
  loadDiaries,
  scheduleDiarySearch,
  loadMemoryHub,
  loadStats,
  logicalDate,
  memoryBusy,
  memoryCategoryLabel,
  memoryData,
  mioDisplayName,
  memoryLayerLabel,
  memoryLoaded,
  memoryLoading,
  editStructuredMemory,
  memoryTab,
  newConversationSummary,
  newAutonomyGoalCapability,
  newAutonomyGoalTitle,
  newProfileNote,
  newStructuredMemory,
  newThreadContent,
  newThreadFollowUp,
  openDailyReview,
  openDiary,
  openStatsDiary,
  profileRows,
  renderedMarkdown,
  rejectMemoryCandidate,
  recordFollowUpResult,
  resolveThread,
  reviewBusy,
  runtimeSummaryDraft,
  saveConversationSummary,
  saveMemoryThread,
  saveProfileNote,
  saveRuntimeSummary,
  saveAutonomyPolicy,
  setAutonomyCapabilityOverride,
  selectedDailyDate,
  selectedDailyReview,
  selectedDiary,
  restoreStructuredMemory,
  selectedWeeklyReview,
  selectedWeeklyStart,
  selectedMonthlyReview,
  selectedMonthlyMonth,
  statsCalendarCells,
  statsData,
  statsDistribution,
  statsLoaded,
  statsLoading,
  statsMonthLabel,
  statsMoodPath,
  statsMoodPoints,
  statsMoodSeries,
  statsSummary,
  statusLabel,
  taskBusy,
  taskCenterTab,
  taskPayloadSummary,
  taskStatusFilter,
  taskStatusFilters,
  taskStatusLabel,
  taskSummary,
  tasksLoaded,
  tasksLoading,
  updateAutonomyGoal,
  weeklyReviewItems,
  weeklyReviews,
  monthlyReviewItems,
  monthlyReviews,
  updateDiary,
}))

function markAppReady() {
  // The launcher waits for this concrete DOM signal instead of treating the
  // WebView loaded event alone as proof that the current screen is interactive.
  document.documentElement.dataset.mioReady = 'true'
  window.dispatchEvent(new CustomEvent('mio:app-ready'))
  if (isAgentWorkspace) return
  void request('/api/companion/app-ready', { method: 'POST' }).catch(() => {
    // UI readiness must not be blocked by optional local-voice warm-up.
  })
}

async function syncActiveViewState(
  view = activeView.value,
  section = activeSettingsSection.value,
  visible = document.visibilityState === 'visible',
) {
  if (standalonePetChat || isAgentWorkspace) return
  try {
    await selfStateApi.reportActiveView(buildActiveViewReport(view, section, visible))
  } catch {
    // SelfSnapshot reporting is diagnostic and must never block the primary UI.
  }
}

function handleVisibilityChange() {
  void syncActiveViewState()
}

onMounted(async () => {
  if (standalonePetChat) {
    document.documentElement.classList.add('standalone-pet-chat-document')
    document.body.classList.add('standalone-pet-chat-document')
    window.addEventListener('mio:pet-chat-hidden', handlePetChatHidden)
    void setPetChatWindowState(true)
    return
  }
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  document.addEventListener('keydown', handleDocumentKeydown)
  document.addEventListener('visibilitychange', handleVisibilityChange)
  window.addEventListener('storage', handleAppPreferencesStorage)
  window.addEventListener('mio:open-pet-chat', focusDesktopPetChat)
  try {
    const onboarding = await loadOnboardingStatus()
    if (onboarding?.completed === false) {
      bootstrap.value = { onboarding, models: [] }
      loading.value = false
      await refreshOnboardingEnvironment()
      await syncActiveViewState('onboarding')
      markAppReady()
      return
    }
  } catch (error) {
    errorMessage.value = error.message
  }
  await loadBootstrap()
  await syncActiveViewState()
  markAppReady()
  await loadSharedChatSettings()
  if (!isAgentWorkspace) {
    void loadMioProfileSettings({ quiet: true })
    await loadStartupGreetingSetting()
    await loadDesktopPreferences({ quiet: true })
    await loadQqStartupSetting()
    if (activeView.value === 'home') {
      void loadMemoryHub()
      void loadCompanionStatus({ quiet: true })
    }
    void requestStartupGreeting()
  }
  if (window.location.hash === '#desktop-pet-chat') focusDesktopPetChat()
  pollTimer = window.setInterval(refreshMessages, 5000)
  if (!isAgentWorkspace) {
    dashboardPollTimer = window.setInterval(refreshDayDashboard, 15000)
    qqStatusPollTimer = window.setInterval(refreshQqStatus, 10000)
    activeViewHeartbeatTimer = window.setInterval(() => {
      void syncActiveViewState()
    }, ACTIVE_VIEW_HEARTBEAT_MS)
    companionPollTimer = window.setInterval(() => {
      if (activeView.value === 'companion') loadCompanionStatus({ quiet: true, preserveSettings: true })
    }, 3000)
  }
})

watch(activeView, async (view) => {
  if (isAgentWorkspace) localStorage.setItem('mio_agent_active_view', normalizeAgentView(view))
  void syncActiveViewState(view)
  closeModelMenu()
  if (view !== 'settings') showProviderPanel.value = false
  if (view === 'home' && !isAgentWorkspace) {
    if (!memoryLoaded.value) loadMemoryHub()
    await loadCompanionStatus({ quiet: true })
  }
  if (view === 'memory' && !memoryLoaded.value) loadMemoryHub()
  if (view === 'stats' && !statsLoaded.value) loadStats()
  if (view === 'tasks') loadAgentTasks({ quiet: tasksLoaded.value })
  if (view === 'companion' || view === 'settings') {
    await loadCompanionStatus({ quiet: true })
    if (view === 'settings' && activeSettingsSection.value === 'advanced' && !runtimeSettingsReady.value) {
      await loadRuntimeSettings({ quiet: true })
    }
    if (view === 'settings' && activeSettingsSection.value === 'profile' && !mioProfileReady.value) {
      await loadMioProfileSettings({ quiet: true })
    }
    if (view === 'settings' && activeSettingsSection.value === 'data') {
      await refreshDataPrivacy({ quiet: dataPrivacyState.value.backups.length > 0 })
    }
    if (view === 'companion') await loadGameWindows({ quiet: true })
  }
  if (view === 'chat') {
    await settleChatScrollToBottom()
  }
})

watch(activeSettingsSection, (section) => {
  if (activeView.value === 'settings') void syncActiveViewState('settings', section)
})

onBeforeUnmount(() => {
  document.documentElement.classList.remove('standalone-pet-chat-document')
  document.body.classList.remove('standalone-pet-chat-document')
  if (standalonePetChat) void setPetChatWindowState(false)
  void stopPetCall()
  window.removeEventListener('mio:pet-chat-hidden', handlePetChatHidden)
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  document.removeEventListener('keydown', handleDocumentKeydown)
  document.removeEventListener('visibilitychange', handleVisibilityChange)
  window.removeEventListener('storage', handleAppPreferencesStorage)
  window.removeEventListener('mio:open-pet-chat', focusDesktopPetChat)
  if (pollTimer) window.clearInterval(pollTimer)
  if (dashboardPollTimer) window.clearInterval(dashboardPollTimer)
  if (qqStatusPollTimer) window.clearInterval(qqStatusPollTimer)
  if (companionPollTimer) window.clearInterval(companionPollTimer)
  if (activeViewHeartbeatTimer) window.clearInterval(activeViewHeartbeatTimer)
  if (diarySearchTimer) window.clearTimeout(diarySearchTimer)
  clearQqQrImage()
  stopMessageVoice()
  void syncActiveViewState(activeView.value, activeSettingsSection.value, false)
})

function handlePetChatHidden() {
  void setPetChatWindowState(false)
  void stopPetCall()
}

async function setPetChatWindowState(open) {
  try {
    await request('/api/companion/chat-window/state', {
      method: 'POST',
      body: JSON.stringify({ open: Boolean(open) }),
    })
  } catch (_) {}
}
</script>

<template>
  <main v-if="standalonePetChat" :class="['standalone-pet-chat', appThemeClass]">
    <div class="pet-chat-composer standalone-pet-chat-composer">
      <span class="standalone-pet-chat-drag" title="拖动对话框" aria-hidden="true" @mousedown="beginPetChatWindowDrag"></span>
      <textarea
        v-model="petChatDraft"
        rows="1"
        :placeholder="errorMessage || (petChatImages.length ? `已粘贴 ${petChatImages.length} 张图片` : `和${mioDisplayName}说话`)"
        @keydown="handlePetChatKeydown"
        @paste="handlePetChatPaste"
      />
      <button
        :class="['pet-call-button', { active: petCallActive }]"
        type="button"
        :title="petCallError || petCallStateLabel"
        @click="togglePetCall"
      >
        <PhoneOff v-if="petCallActive" :size="16" />
        <Phone v-else :size="16" />
      </button>
      <button type="button" title="发送" :disabled="(!petChatDraft.trim() && !petChatImages.length) || petChatSending" @click="sendPetChat">
        <RefreshCw v-if="petChatSending" class="spin" :size="16" />
        <Send v-else :size="16" />
      </button>
    </div>
  </main>

  <OnboardingPage
    v-else-if="bootstrap && bootstrap.onboarding?.completed === false"
    :onboarding="bootstrap.onboarding"
    :environment="onboardingEnvironment"
    :environment-busy="onboardingEnvironmentBusy"
    :busy="onboardingBusy"
    :error="onboardingError"
    @complete="completeOnboarding"
    @refresh-environment="refreshOnboardingEnvironment"
  />

  <div v-else :class="['app-shell', 'integrated-shell', appThemeClass, `font-${savedAppPreferences.font_size || 'medium'}`, `active-view-${activeView}`, `display-mode-${displayMode}`, { 'agent-workspace-shell': isAgentWorkspace, 'left-sidebar-hidden': !leftSidebarVisible, 'right-sidebar-hidden': !rightSidebarVisible, 'reduce-motion': savedAppPreferences.light_animations === false, 'focus-mode': savedAppPreferences.focus_mode }]">
    <UpdateNotice @open="openSettingsSection('general')" />
    <span v-for="direction in ['top', 'right', 'bottom', 'left', 'top-left', 'top-right', 'bottom-right', 'bottom-left']" :key="direction" :class="['window-resize-handle', direction]" aria-hidden="true" @pointerdown="resizeDesktopWindow(direction, $event)"></span>
    <header class="window-titlebar integrated-titlebar">
      <button v-if="activeView !== 'settings'" class="shell-toggle shell-toggle-left" type="button" :title="leftSidebarVisible ? '隐藏左侧栏' : '显示左侧栏'" @click="toggleLeftSidebar">
        <component :is="leftSidebarVisible ? PanelLeftClose : PanelLeftOpen" :size="17" />
      </button>
      <div class="window-drag-zone pywebview-drag-region" @dblclick="controlDesktopWindow('maximize')">
        <strong>{{ isAgentWorkspace ? `Mio Agent · ${agentNavItems.find((item) => item.id === activeView)?.label || '工作台'}` : activeView === 'settings' ? '设置' : navItems.find((item) => item.id === activeView)?.label }}</strong>
      </div>
      <button v-if="activeView !== 'settings'" class="workspace-title-switch" type="button" :title="isAgentWorkspace ? '返回 Mio 主窗口' : '打开 Mio Agent'" @click="switchDesktopWorkspace(isAgentWorkspace ? 'main' : 'agent')">
        <ArrowLeft v-if="isAgentWorkspace" :size="15" /><Bot v-else :size="15" />
        <span>{{ isAgentWorkspace ? '返回 Mio' : 'Mio Agent' }}</span>
      </button>
      <button v-if="activeView !== 'settings'" class="shell-toggle shell-toggle-right" type="button" :title="rightSidebarVisible ? '隐藏右侧栏' : '显示右侧栏'" @click="toggleRightSidebar">
        <component :is="rightSidebarVisible ? PanelRightClose : PanelRightOpen" :size="17" />
      </button>
      <div class="window-controls" aria-label="窗口控制">
        <button type="button" title="最小化" aria-label="最小化" @click="controlDesktopWindow('minimize')"><Minus :size="15" /></button>
        <button type="button" title="最大化或还原" aria-label="最大化或还原" @click="controlDesktopWindow('maximize')"><Maximize2 :size="13" /></button>
        <button class="close" type="button" title="关闭到后台" aria-label="关闭到后台" @click="controlDesktopWindow('close')"><X :size="15" /></button>
      </div>
    </header>

    <aside
      v-if="leftSidebarVisible"
      :class="['sidebar', 'integrated-sidebar', { expanded: leftSidebarPinned || (savedAppPreferences.left_sidebar_hover_expand && leftSidebarHovered), pinned: leftSidebarPinned }]"
      @mouseenter="leftSidebarHovered = true"
      @mouseleave="leftSidebarHovered = false"
    >
      <div class="brand-block">
        <img class="brand-avatar" :src="avatarUrl" :alt="mioDisplayName" />
        <div><strong>{{ isAgentWorkspace ? 'Mio Agent' : mioDisplayName }}</strong><span>{{ isAgentWorkspace ? '智能工作台' : '私人空间' }}</span></div>
        <button class="sidebar-pin" type="button" :title="leftSidebarPinned ? '取消固定展开' : '固定展开'" @click="toggleLeftSidebarPinned">
          <component :is="leftSidebarPinned ? PinOff : Pin" :size="14" />
        </button>
      </div>
      <nav class="main-nav" aria-label="主要导航">
        <button v-for="item in navGroups[0].items" :key="item.id" type="button" :class="['nav-item', { active: isPrimaryNavActive(item.id) }]" :title="item.label" @click="navigatePrimaryView(item.id)">
          <component :is="item.icon" :size="18" /><span>{{ item.label }}</span>
        </button>
      </nav>
      <div v-if="!isAgentWorkspace" class="sidebar-footer pet-launcher">
        <button type="button" :title="companionStatus.pet?.running ? '停止桌宠' : '启动桌宠'" :disabled="Boolean(companionBusy)" @click="controlCompanion(companionStatus.pet?.running ? 'stop' : 'start')">
          <component :is="companionStatus.pet?.running ? Power : Play" :size="17" />
          <span><strong>桌宠</strong><small>{{ companionStatus.pet?.running ? '运行中' : '未启动' }}</small></span>
          <i :class="{ online: companionStatus.pet?.running }" />
        </button>
      </div>
      <div v-else class="sidebar-footer workspace-launcher">
        <button type="button" title="返回 Mio 主窗口" @click="switchDesktopWorkspace('main')">
          <ArrowLeft :size="17" /><span><strong>返回 Mio</strong><small>私人空间</small></span>
        </button>
      </div>
    </aside>

    <main :class="['workspace', 'integrated-workspace', { 'has-error': errorMessage, 'view-settings': activeView === 'settings' }]">
      <div v-if="errorMessage" class="error-banner"><span>{{ errorMessage }}</span><button type="button" title="关闭" @click="errorMessage = ''"><X :size="16" /></button></div>
      <AgentHomePage v-if="isAgentWorkspace && activeView === 'home'" @navigate="navigatePrimaryView" />
      <AgentSettingsPage v-else-if="isAgentWorkspace && activeView === 'settings'" @preferences-change="applyAgentPreferences" @return="returnToApp" />
      <HomePage v-else-if="activeView === 'home'" :logical-date="logicalDate" :today-state="todayState" :today-state-details="todayStateDetails" :diaries="diaries" :memory-data="memoryData" :display-name="mioDisplayName" :user-address="preferredUserAddress" :companion-running="Boolean(companionStatus.pet?.running)" @navigate="activeView = $event" @open-diary="openDiary" />
      <ChatPage v-else-if="activeView === 'chat'" />
      <RecordsPage v-else-if="['diaries', 'stats', 'memory'].includes(activeView)" />
      <AgentTasksPage v-else-if="isAgentWorkspace && activeView === 'tasks'" @open-settings="activeView = 'settings'" />
      <TasksPage v-else-if="activeView === 'tasks'" />
      <section v-else-if="activeView === 'settings'" class="settings-window-shell embedded-settings-shell">
        <header class="settings-window-topbar">
          <button type="button" @click="returnToApp"><ArrowLeft :size="16" />返回应用</button>
          <strong>设置</strong>
        </header>
        <div class="settings-window-layout">
          <aside class="settings-window-navigation">
            <label class="settings-window-search"><Search :size="15" /><input v-model="settingsSearch" type="search" placeholder="搜索设置" /></label>
            <nav aria-label="设置分类">
              <template v-for="group in filteredSettingsNavigation" :key="group.label">
                <span class="settings-window-group-label">{{ group.label }}</span>
                <button v-for="item in group.items" :key="item.id" :class="{ active: activeSettingsSection === item.id }" type="button" :title="item.label" :aria-label="item.label" @click="openSettingsSection(item.id)">
                  <component :is="item.icon" :size="16" /><span>{{ item.label }}</span><i v-if="isSettingsSectionDirty(item.id)" />
                </button>
              </template>
            </nav>
          </aside>
          <main class="settings-window-content"><SettingsPage /></main>
        </div>
      </section>
      <CompanionPage v-else />
    </main>

    <aside
      v-if="rightSidebarVisible"
      :class="['integrated-right-rail', { 'agent-right-rail': isAgentWorkspace, expanded: rightSidebarPinned || ((isAgentWorkspace ? agentRightSidebarHoverExpand : savedAppPreferences.right_sidebar_hover_expand) && rightSidebarHovered), pinned: rightSidebarPinned }]"
      @mouseenter="rightSidebarHovered = true"
      @mouseleave="rightSidebarHovered = false"
    >
      <AgentRightRail v-if="isAgentWorkspace" :pinned="rightSidebarPinned" @toggle-pin="toggleRightSidebarPinned" @navigate="navigatePrimaryView" />
      <template v-else>
      <header class="rail-mood">
        <div><Heart :size="18" /><span><small>现在的心情</small><strong>{{ statusMoodLabel }}</strong></span></div>
        <button type="button" :title="rightSidebarPinned ? '取消固定展开' : '固定展开'" @click="toggleRightSidebarPinned"><component :is="rightSidebarPinned ? PinOff : Pin" :size="14" /></button>
      </header>
      <section class="rail-relationship"><Sparkles :size="17" /><span><small>关系</small><strong>{{ relationshipDistanceLabel }}</strong></span></section>
      <section class="rail-growth">
        <div><Target :size="17" /><span><small>今日成长</small><strong>{{ statusLabel(todayState.daily_thirty_status) }}</strong></span></div>
        <p>{{ todayState.daily_thirty_reason || '还在了解今天的进展' }}</p>
      </section>
      <section class="rail-state-list">
        <div v-for="item in todayStateDetails" :key="item.key"><span>{{ item.label }}</span><p>{{ item.value }}</p></div>
      </section>
      <section class="rail-services">
        <button type="button" title="QQ通道" @click="openSettingsSection('qq')"><component :is="qqConnected ? Wifi : WifiOff" :size="17" /><span><strong>QQ</strong><small>{{ qqConnected ? '在线' : '离线' }}</small></span><i :class="{ online: qqConnected }" /></button>
        <button type="button" title="语音服务" @click="openSettingsSection('pet')"><Volume2 :size="17" /><span><strong>语音</strong><small>{{ companionStatus.voice_runtime?.service_running ? '在线' : '未启动' }}</small></span><i :class="{ online: companionStatus.voice_runtime?.service_running }" /></button>
        <button type="button" title="桌宠状态" @click="activeView = 'companion'"><Gamepad2 :size="17" /><span><strong>桌宠</strong><small>{{ companionStatus.pet?.running ? '运行中' : '未启动' }}</small></span><i :class="{ online: companionStatus.pet?.running }" /></button>
        <button type="button" title="屏幕观察" @click="openCompanionSection('screen-panel')"><Monitor :size="17" /><span><strong>观察</strong><small>{{ companionStatus.screen?.running ? (companionStatus.screen.title || '进行中') : '未启动' }}</small></span><i :class="{ online: companionStatus.screen?.running }" /></button>
      </section>
      <button class="rail-token" type="button" title="查看 Token 统计" @click="openTokenUsagePanel"><Activity :size="17" /><span><small>今日 Token</small><strong>{{ todayTokenUsage.toLocaleString('zh-CN') }}</strong></span></button>
      <section v-if="latestAssistantNotice" class="rail-notice"><MessageSquareText :size="17" /><span><small>最近消息 · {{ formatShortTime(latestAssistantNotice.created_at) }}</small><p>{{ latestAssistantNotice.content }}</p></span></section>
      </template>
    </aside>

    <AppDialog
      v-model="appDialog.value"
      :open="appDialog.open"
      :mode="appDialog.mode"
      :title="appDialog.title"
      :message="appDialog.message"
      :confirm-text="appDialog.confirmText"
      :cancel-text="appDialog.cancelText"
      :danger="appDialog.danger"
      :multiline="appDialog.multiline"
      @confirm="confirmAppDialog"
      @cancel="cancelAppDialog"
    />

    <Teleport to="body">
      <div v-if="showTokenUsagePanel" class="modal-backdrop" @click.self="closeTokenUsagePanel">
        <section ref="tokenUsageDialog" class="token-usage-modal" role="dialog" aria-modal="true" aria-label="Token 统计" tabindex="-1" @keydown="onTokenUsageDialogKeydown">
        <header><div><h2>Token 统计</h2><p>按凌晨 {{ tokenUsageData.logical_day_boundary_hour || 4 }} 点划分每天的使用量</p></div><button class="icon-button" type="button" title="关闭" data-modal-initial-focus @click="closeTokenUsagePanel"><X :size="18" /></button></header>
        <div v-if="tokenUsageLoading" class="token-usage-loading">正在读取使用记录</div>
        <template v-else>
          <div class="token-summary-grid">
            <article><span>今日合计</span><strong>{{ formatTokenCount(tokenUsageData.today?.total_tokens) }}</strong><small>对话 {{ formatTokenCount(tokenUsageData.today?.chat_tokens) }} · 观察 {{ formatTokenCount(tokenUsageData.today?.screen_tokens) }}</small></article>
            <article><span>累计合计</span><strong>{{ formatTokenCount(tokenUsageData.total?.total_tokens || totalTokenUsage) }}</strong><small>对话 {{ formatTokenCount(tokenUsageData.total?.chat_tokens) }} · 观察 {{ formatTokenCount(tokenUsageData.total?.screen_tokens) }}</small></article>
          </div>
          <div class="token-breakdown">
            <span><small>今日输入</small><b>{{ formatTokenCount(tokenUsageData.today?.prompt_tokens) }}</b></span>
            <span><small>今日输出</small><b>{{ formatTokenCount(tokenUsageData.today?.completion_tokens) }}</b></span>
            <span><small>今日推理</small><b>{{ formatTokenCount(tokenUsageData.today?.reasoning_tokens) }}</b></span>
            <span><small>缓存输入</small><b>{{ formatTokenCount(tokenUsageData.today?.cached_prompt_tokens) }}</b></span>
          </div>
          <div class="token-day-list">
            <header><span>日期</span><span>对话</span><span>观察</span><span>合计</span></header>
            <div v-for="day in tokenUsageData.days" :key="day.date"><time>{{ day.date }}</time><span>{{ formatTokenCount(day.chat_tokens) }}</span><span>{{ formatTokenCount(day.screen_tokens) }}</span><strong>{{ formatTokenCount(day.total_tokens) }}</strong></div>
          </div>
        </template>
        </section>
      </div>
    </Teleport>

    <Teleport to="body">
      <div v-if="showProviderPanel && activeView === 'settings' && activeSettingsSection === 'models'" class="modal-backdrop" @click.self="closeProviderPanel">
        <section ref="providerDialog" class="provider-modal" role="dialog" aria-modal="true" aria-label="供应商设置" tabindex="-1" @keydown="onProviderDialogKeydown">
        <header><div><h2>新增API供应商</h2><p>连接一次供应商，再选择要使用的具体模型版本。</p></div><button class="icon-button" type="button" title="关闭" @click="closeProviderPanel"><X :size="18" /></button></header>
        <div class="provider-kind-picker">
          <button type="button" :class="{ active: providerForm.provider_kind === 'official' }" @click="selectProviderKind('official')">官方 API</button>
          <button type="button" :class="{ active: providerForm.provider_kind === 'relay' }" @click="selectProviderKind('relay')">中转站</button>
        </div>
        <div v-if="providerForm.provider_kind === 'official'" class="provider-official-picker">
          <select :value="providerForm.preset_id" @change="selectProviderPreset($event.target.value)">
            <option value="">选择常用供应商</option>
            <option v-for="preset in providerPresets" :key="preset.id" :value="preset.id">{{ preset.name }}</option>
          </select>
        </div>
        <label>供应商名称<input v-model.trim="providerForm.provider_name" :disabled="Boolean(providerForm.preset_id)" autofocus placeholder="例如 OpenAI 中转站" /></label>
        <label>API地址<input v-model.trim="providerForm.base_url" :disabled="Boolean(providerForm.preset_id)" placeholder="https://example.com/v1" /></label>
        <label>API Key<input v-model="providerForm.api_key" type="password" autocomplete="off" placeholder="sk-..." /></label>
        <label>接口模式<select v-model="providerForm.default_api_mode"><option value="auto">自动识别</option><option value="responses">Responses API（Codex）</option><option value="chat_completions">Chat Completions</option></select></label>
        <div class="provider-discovery-bar">
          <span>{{ providerForm.provider_kind === 'official' ? '按官方模型接口获取' : 'OpenAI 兼容 / New API 中转站' }}</span>
          <button type="button" :disabled="providerDiscoveryBusy" @click="discoverProviderModels">
            <Download :size="15" />{{ providerDiscoveryBusy ? '正在获取' : '获取模型列表' }}
          </button>
        </div>
        <p v-if="providerDiscoveryWarning" class="provider-discovery-warning">{{ providerDiscoveryWarning }}</p>
        <div v-if="providerDiscoveryMeta" class="provider-discovery-result">
          <strong>连接成功</strong>
          <span>API 根地址：{{ providerDiscoveryMeta.resolved_api_base_url }}</span>
          <span>模型目录：{{ providerDiscoveryMeta.models_endpoint }}</span>
          <span>鉴权：{{ providerDiscoveryMeta.auth_scheme }}</span>
        </div>
        <div v-if="discoveredModels.length" class="discovered-model-list">
          <div v-for="model in discoveredModels" :key="model.model" class="discovered-model">
            <input v-model="model.selected" type="checkbox" :disabled="model.api_supported === false" />
            <span><strong>{{ model.display_name }}</strong><small>{{ model.model }}{{ model.api_supported === false ? ' · /messages 暂不支持' : '' }}</small></span>
            <em>{{ pricingSourceLabel(model.pricing_source) }}</em>
            <div v-if="model.selected" class="discovered-pricing">
              <label>缓存<input v-model.number="model.cached_input_price_cny_per_million" type="number" min="0" step="0.001" /></label>
              <label>输入<input v-model.number="model.input_price_cny_per_million" type="number" min="0" step="0.01" /></label>
              <label>输出<input v-model.number="model.output_price_cny_per_million" type="number" min="0" step="0.01" /></label>
              <small>元 / 百万 Token；接口返回实扣时会自动优先使用</small>
            </div>
          </div>
        </div>
        <label>模型ID<input v-model.trim="providerForm.model" placeholder="不选择上方列表时，可在这里手动填写模型ID" /></label>
        <template v-if="!discoveredModels.some((item) => item.selected)">
          <div class="form-grid price-grid">
            <label>缓存价格（元/百万Token）<input v-model.number="providerForm.cached_input_price_cny_per_million" type="number" min="0" step="0.001" /></label>
            <label>输入价格（元/百万Token）<input v-model.number="providerForm.input_price_cny_per_million" type="number" min="0" step="0.01" /></label>
            <label>输出价格（元/百万Token）<input v-model.number="providerForm.output_price_cny_per_million" type="number" min="0" step="0.01" /></label>
          </div>
        </template>
        <div class="provider-privacy-note">
          API Key 只保存在本机 D 盘数据目录，不会在页面中回显。
        </div>
        <label class="checkbox-field"><input v-model="providerForm.supports_vision" type="checkbox" />支持图片识别</label>
        <footer>
          <button type="button" @click="closeProviderPanel">取消</button>
          <button class="primary-button" type="button" :disabled="providerBusy" @click="saveProvider">{{ providerBusy ? '正在保存' : `保存${discoveredModels.filter((item) => item.selected).length || 1}个模型` }}</button>
        </footer>
        </section>
      </div>
    </Teleport>
  </div>
</template>
