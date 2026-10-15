import { ref } from 'vue'
import { api, errorMessage } from '../../api/client'
import type { MemoryRecord } from '../../types/api'

export function useMemory(userId: string) {
  const memories = ref<MemoryRecord[]>([])
  const loading = ref(false)
  const error = ref('')
  const deleting = ref<number | null>(null)
  let revision = 0
  async function refresh() {
    const token = ++revision
    loading.value = true
    error.value = ''
    try {
      const rows = await api.memories(userId)
      if (token === revision) memories.value = rows
    } catch (cause) {
      if (token === revision) error.value = errorMessage(cause)
    } finally { if (token === revision) loading.value = false }
  }
  async function remove(id: number) {
    if (deleting.value !== null) return
    deleting.value = id
    error.value = ''
    try {
      const result = await api.deleteMemory(userId, id)
      await refresh()
      if (!result.deleted) error.value = '后端未确认删除，这条记忆可能已被移除。已重新查询列表。'
      return result.deleted
    } catch (cause) {
      error.value = `删除未确认，请刷新列表核对后再操作。${errorMessage(cause)}`
      return false
    } finally { deleting.value = null }
  }
  return { memories, loading, error, deleting, refresh, remove }
}
