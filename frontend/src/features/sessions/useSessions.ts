import { computed, ref } from 'vue'

export interface Session { id: string; name: string; createdAt: string; updatedAt: string; hasMessages: boolean }
interface Directory { version: 1; userId: string; activeId: string; sessions: Session[] }
const KEY = 'ricegod.sessions.v1'
const validId = (value: unknown): value is string => typeof value === 'string' && /^[a-zA-Z0-9_-]{1,128}$/.test(value)

export function useSessions(storage: Pick<Storage, 'getItem' | 'setItem'> = localStorage) {
  const storageError = ref('')
  const sessions = ref<Session[]>([])
  const userId = ref(`demo_${crypto.randomUUID()}`)
  const activeId = ref('')
  try {
    const data = JSON.parse(storage.getItem(KEY) || 'null') as Directory | null
    if (data?.version === 1 && validId(data.userId) && Array.isArray(data.sessions)) {
      userId.value = data.userId
      const seen = new Set<string>()
      sessions.value = data.sessions.filter(s => {
        if (!s || !validId(s.id) || seen.has(s.id) || typeof s.name !== 'string' ||
          !Number.isFinite(Date.parse(s.createdAt)) || !Number.isFinite(Date.parse(s.updatedAt))) return false
        seen.add(s.id)
        return true
      }).map(s => ({ id: s.id, name: s.name.slice(0, 60), createdAt: s.createdAt, updatedAt: s.updatedAt, hasMessages: !!s.hasMessages }))
      activeId.value = sessions.value.some(s => s.id === data.activeId) ? data.activeId : sessions.value[0]?.id || ''
    }
  } catch {
    storageError.value = '本地会话目录无法读取。当前会话仍可使用，但刷新后可能无法恢复。'
  }

  function save() {
    try {
      storage.setItem(KEY, JSON.stringify({ version: 1, userId: userId.value, activeId: activeId.value, sessions: sessions.value }))
    } catch {
      storageError.value = '浏览器未能保存会话目录，刷新后可能无法恢复。'
    }
  }
  function create() {
    const now = new Date().toISOString()
    const session: Session = { id: crypto.randomUUID(), name: '新的饮食灵感', createdAt: now, updatedAt: now, hasMessages: false }
    sessions.value.unshift(session)
    activeId.value = session.id
    save()
    return session.id
  }
  function select(id: string) {
    if (sessions.value.some(s => s.id === id)) { activeId.value = id; save() }
  }
  function rename(id: string, name: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session && name.trim()) { session.name = name.trim().slice(0, 60); save() }
  }
  function markSent(id: string) {
    const session = sessions.value.find(s => s.id === id)
    if (session) { session.hasMessages = true; session.updatedAt = new Date().toISOString(); save() }
  }
  if (!activeId.value) create()
  const activeSession = computed(() => sessions.value.find(s => s.id === activeId.value)!)
  const sortedSessions = computed(() => [...sessions.value].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)))
  return { sessions: sortedSessions, userId, activeId, activeSession, storageError, create, select, rename, markSent }
}
