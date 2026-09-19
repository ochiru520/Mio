/** Diaries operations. Reactive values and cross-domain callbacks are explicitly injected.
 * This module owns behavior, not application-global mutable state.
 * Dependencies are accessors so async callbacks see the latest state.
 */
import * as diaryApi from '../services/diaryApi.js'

export function useDiaries(deps) {
  async function loadStats(year = 0, month = 0) {
    if (deps.statsLoading.value) return
    deps.statsLoading.value = true
    try {
      deps.statsData.value = await deps.request(`/api/agent/stats?year=${year}&month=${month}`)
      deps.statsLoaded.value = true
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.statsLoading.value = false
    }
  }
  function changeStatsMonth(delta) {
    const current = new Date(Date.UTC(deps.statsData.value.year, deps.statsData.value.month - 1 + delta, 1))
    loadStats(current.getUTCFullYear(), current.getUTCMonth() + 1)
  }
  function openStatsDiary(cell) {
    if (cell.hasDiary) openDiary(cell.date)
  }
  async function loadDiaries() {
    void deps.refreshDayDashboard()
    const requestId = ++deps.diarySearchRequestId
    const query = deps.diarySearch.value.trim()
    try {
      const loadedDiaries = await deps.request(`/api/diaries?q=${encodeURIComponent(query)}`)
      if (requestId !== deps.diarySearchRequestId || query !== deps.diarySearch.value.trim()) return
      deps.diaries.value = loadedDiaries
      if (!loadedDiaries.length) {
        deps.selectedDiary.value = null
      } else if (!deps.selectedDiary.value || !loadedDiaries.some((item) => item.date === deps.selectedDiary.value.date)) {
        const firstDiary = await deps.request(`/api/diaries/${loadedDiaries[0].date}`)
        if (requestId !== deps.diarySearchRequestId || query !== deps.diarySearch.value.trim()) return
        deps.selectedDiary.value = firstDiary
      }
    } catch (error) {
      if (requestId === deps.diarySearchRequestId) deps.errorMessage.value = error.message
    }
  }
  function scheduleDiarySearch() {
    if (deps.diarySearchTimer) window.clearTimeout(deps.diarySearchTimer)
    deps.diarySearchTimer = window.setTimeout(() => {
      deps.diarySearchTimer = null
      void loadDiaries()
    }, 250)
  }
  async function openDiary(date) {
    deps.activeView.value = 'diaries'
    try {
      deps.selectedDiary.value = await deps.request(`/api/diaries/${date}`)
    } catch (error) {
      deps.errorMessage.value = error.message
    }
  }
  async function generateTodayDiary() {
    deps.diaryBusy.value = 'generate'
    try {
      await diaryApi.generateTodayDiary()
      await loadDiaries()
      await deps.refreshDayDashboard()
      if (deps.logicalDate.value) await openDiary(deps.logicalDate.value)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function confirmDiary() {
    if (!deps.selectedDiary.value) return
    deps.diaryBusy.value = 'confirm'
    try {
      await deps.request(`/api/diaries/${deps.selectedDiary.value.date}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ confirmed: true }),
      })
      await openDiary(deps.selectedDiary.value.date)
      await loadDiaries()
      await deps.refreshDayDashboard()
      if (deps.statsLoaded.value) await loadStats(deps.statsData.value.year, deps.statsData.value.month)
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function updateDiary(date, payload) {
    deps.diaryBusy.value = 'edit'
    try {
      await deps.request(`/api/diaries/${date}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      await loadDiaries()
      await openDiary(date)
      await deps.refreshDayDashboard()
      if (deps.statsLoaded.value) await loadStats(deps.statsData.value.year, deps.statsData.value.month)
      return true
    } catch (error) {
      deps.errorMessage.value = error.message
      return false
    } finally {
      deps.diaryBusy.value = ''
    }
  }
  async function openDailyReview(date) {
    deps.activeView.value = 'memory'
    deps.memoryTab.value = 'daily'
    deps.selectedDailyDate.value = date
    if (!deps.memoryLoaded.value) await deps.loadMemoryHub()
  }
  async function generateDailyReview(date = deps.selectedDailyDate.value || deps.logicalDate.value) {
    if (!date || deps.reviewBusy.value) return
    deps.selectedDailyDate.value = date
    deps.reviewBusy.value = 'daily'
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/reviews/${date}/generate`, { method: 'POST', body: '{}' })
      deps.dailyReviews.value = await deps.request('/api/reviews')
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  async function generateWeekly(start = deps.selectedWeeklyStart.value || deps.weekStartFor(deps.logicalDate.value)) {
    if (!start || deps.reviewBusy.value) return
    deps.selectedWeeklyStart.value = start
    deps.reviewBusy.value = 'weekly'
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/weekly/${start}/generate`, { method: 'POST', body: '{}' })
      deps.weeklyReviews.value = await deps.request('/api/weekly')
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  async function generateMonthly(month = deps.selectedMonthlyMonth.value || deps.logicalDate.value.slice(0, 7)) {
    if (!month || deps.reviewBusy.value) return
    deps.selectedMonthlyMonth.value = month
    deps.reviewBusy.value = 'monthly'
    deps.errorMessage.value = ''
    try {
      await deps.request(`/api/monthly/${month}/generate`, { method: 'POST', body: '{}' })
      deps.monthlyReviews.value = await deps.request('/api/monthly')
    } catch (error) {
      deps.errorMessage.value = error.message
    } finally {
      deps.reviewBusy.value = ''
    }
  }
  return { loadStats, changeStatsMonth, openStatsDiary, loadDiaries, scheduleDiarySearch, openDiary, generateTodayDiary, confirmDiary, updateDiary, openDailyReview, generateDailyReview, generateWeekly, generateMonthly }
}
