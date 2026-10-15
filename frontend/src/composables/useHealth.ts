import { ref } from 'vue'
import { api, errorMessage } from '../api/client'

export function useHealth() {
  const status = ref<'checking' | 'online' | 'offline'>('checking')
  const error = ref('')
  async function check() {
    if (status.value === 'checking' && error.value === 'pending') return
    status.value = 'checking'
    error.value = 'pending'
    try {
      const data = await api.health()
      if (data.status !== 'ok') throw new Error('后端未返回健康状态 ok。')
      status.value = 'online'
      error.value = ''
    } catch (cause) { status.value = 'offline'; error.value = errorMessage(cause) }
  }
  return { status, error, check }
}
