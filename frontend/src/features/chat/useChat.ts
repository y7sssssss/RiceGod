import { computed, reactive, watch, type Ref } from 'vue'
import { api, errorMessage } from '../../api/client'
import type { ChatRequest, ChatResponse, MessageRecord } from '../../types/api'

export interface ChatMessage {
  id: string
  role: string
  content: string
  createdAt: string
  response?: ChatResponse
}
interface ConversationState {
  messages: ChatMessage[]
  draft: string
  loading: boolean
  pending: boolean
  error: string
  historyError: string
  historyLimit: boolean
  persistenceVerified: boolean
  failedRetryAllowed: boolean
  failedRequest?: ChatRequest
  selectedResponse?: ChatResponse
  revision: number
}
const toMessage = (m: MessageRecord): ChatMessage => ({ id: `db-${m.id}`, role: m.role, content: m.content, createdAt: m.created_at })

export function useChat(userId: Ref<string>, activeId: Ref<string>, onSent: (id: string) => void, onComplete: () => void) {
  const conversations = reactive<Record<string, ConversationState>>({})
  function state(id: string): ConversationState {
    if (!conversations[id]) conversations[id] = { messages: [], draft: '', loading: false, pending: false, error: '', historyError: '', historyLimit: false, persistenceVerified: false, failedRetryAllowed: false, revision: 0 }
    // Read back through the reactive proxy. Returning the assignment expression would
    // return the raw object on first access and leave the initial UI stuck loading.
    return conversations[id]!
  }
  const current = computed(() => state(activeId.value))

  async function loadHistory(id = activeId.value) {
    const s = state(id)
    if (s.pending) return
    const revision = ++s.revision
    s.loading = true
    s.historyError = ''
    try {
      const rows = await api.messages(userId.value, id)
      if (s.revision !== revision) return
      s.persistenceVerified = true
      // Keep a failed turn's in-memory idempotency key until it is deliberately retried.
      if (!s.failedRequest) {
        s.messages = rows.map(toMessage)
        s.selectedResponse = undefined // /messages carries no execution metadata.
      }
      s.historyLimit = rows.length >= 1000
    } catch (error) {
      if (s.revision === revision) { s.historyError = errorMessage(error); s.persistenceVerified = false }
    } finally {
      if (s.revision === revision) s.loading = false
    }
  }

  async function execute(request: ChatRequest, retry: boolean) {
    const s = state(request.conversation_id)
    if (s.pending || s.loading) return
    ++s.revision // Invalidate older history responses for this conversation.
    s.pending = true
    s.error = ''
    if (!retry) {
      s.failedRetryAllowed = s.persistenceVerified
      s.messages.push({ id: request.request_id, role: 'user', content: request.message, createdAt: new Date().toISOString() })
      s.draft = ''
      onSent(request.conversation_id)
    }
    try {
      if (retry) {
        if (!s.failedRetryAllowed) throw new Error('本轮发送前未能确认持久化服务，不能安全重发。请先恢复后端，再新建会话；原会话不会自动重放。')
        // This exact backend exposes /messages only with the concrete durable MemoryStore.
        // Recheck availability before any replay, then keep ALL four original request fields.
        try { await api.messages(request.user_id, request.conversation_id) }
        catch (error) { throw new Error(`暂不能安全重试：无法确认持久化历史服务可用。${errorMessage(error)}`) }
      }
      const response = await api.chat(request)
      // Retry may recover a committed turn after a lost response. Reconcile from the server
      // rather than append a second local copy. Never infer completion from matching text.
      if (retry) {
        try {
          const rows = await api.messages(request.user_id, request.conversation_id)
          s.messages = rows.map(toMessage)
          const lastAssistant = [...s.messages].reverse().find(m => m.role === 'assistant')
          if (lastAssistant) lastAssistant.response = response
        } catch (error) {
          s.messages.push({ id: `answer-${request.request_id}`, role: 'assistant', content: response.answer, response, createdAt: new Date().toISOString() })
          s.historyError = `回答已返回，但历史同步失败。${errorMessage(error)}`
        }
      } else {
        s.messages.push({ id: `answer-${request.request_id}`, role: 'assistant', content: response.answer, response, createdAt: new Date().toISOString() })
      }
      s.selectedResponse = response
      s.failedRequest = undefined
      onComplete()
    } catch (error) {
      s.failedRequest = { ...request }
      s.error = errorMessage(error)
    } finally { s.pending = false }
  }

  async function send(message: string) {
    const s = current.value
    if (s.pending || s.loading || s.failedRequest || !message.trim() || message.length > 20_000) return
    await execute({ user_id: userId.value, conversation_id: activeId.value, request_id: crypto.randomUUID(), message: message.trim() }, false)
  }
  async function retry() {
    const request = current.value.failedRequest
    if (request) await execute({ ...request }, true)
  }

  watch(activeId, id => { void loadHistory(id) }, { immediate: true })
  return { current, send, retry, loadHistory }
}
