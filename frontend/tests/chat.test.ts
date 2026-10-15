import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed, effectScope, nextTick, ref, type EffectScope } from 'vue'
import { api } from '../src/api/client'
import { useChat } from '../src/features/chat/useChat'
import type { ChatResponse } from '../src/types/api'

vi.mock('../src/api/client', () => ({ api: { messages: vi.fn(), chat: vi.fn() }, errorMessage: (e: Error) => e.message }))
// Contract fixtures are for unit tests only; application has no demo response fallback.
const response = (answer: string): ChatResponse => ({ answer, route: 'recipe_query', logic: 'test fixture', sources: [], used_memories: [], memory_write: { available: false, status: 'unavailable', saved: [], unchanged: [], deleted_ids: [], failed_count: 0, skipped_count: 0, extraction_status: 'not_run', extraction_skip_reason: null, extraction_error: null, operation: null }, performance: null })
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
let scope: EffectScope
function setup() {
  const activeId = ref('one')
  const completed = vi.fn()
  scope = effectScope()
  const chat = scope.run(() => useChat(ref('demo_test'), activeId, vi.fn(), completed))!
  return { chat, activeId, completed }
}
beforeEach(() => { vi.resetAllMocks(); vi.mocked(api.messages).mockResolvedValue([]) })
afterEach(() => scope?.stop())

describe('real API chat orchestration', () => {
  it('reactively exits initial history loading even when the backend is unavailable', async () => {
    const first = deferred<Awaited<ReturnType<typeof api.messages>>>()
    vi.mocked(api.messages).mockReturnValueOnce(first.promise)
    const { chat } = setup()
    const displayedLoading = computed(() => chat.current.value.loading)
    expect(displayedLoading.value).toBe(true)
    first.reject(Error('backend offline'))
    await nextTick()
    expect(displayedLoading.value).toBe(false)
    expect(chat.current.value.historyError).toBe('backend offline')
  })
  it('prevents double sends and keeps late replies in their originating conversation', async () => {
    const { chat, activeId } = setup()
    await nextTick()
    const pending = deferred<ChatResponse>()
    vi.mocked(api.chat).mockReturnValue(pending.promise)
    const sending = chat.send('第一餐')
    await chat.send('不应重复提交')
    expect(api.chat).toHaveBeenCalledTimes(1)
    activeId.value = 'two'
    await nextTick()
    pending.resolve(response('第一餐回答'))
    await sending
    expect(chat.current.value.messages).toHaveLength(0)
    activeId.value = 'one'
    // Before server reload, the returned response belongs only to 'one'.
    expect(chat.current.value.messages.at(-1)?.content).toBe('第一餐回答')
  })
  it('keeps conversation_id and uses a distinct request_id for each new turn', async () => {
    const { chat } = setup()
    await nextTick()
    vi.mocked(api.chat).mockResolvedValue(response('回答'))
    await chat.send('第一轮')
    await chat.send('第二轮')
    const [first, second] = vi.mocked(api.chat).mock.calls.map(args => args[0])
    expect(first!.conversation_id).toBe(second!.conversation_id)
    expect(first!.request_id).not.toBe(second!.request_id)
    expect(first!.user_id).toBe('demo_test')
  })
  it('never retries automatically and reuses the full original request after verifying history', async () => {
    const { chat } = setup()
    await nextTick()
    vi.mocked(api.chat).mockRejectedValueOnce(Error('lost response')).mockResolvedValueOnce(response('恢复回答'))
    await chat.send('请记住口味')
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(chat.current.value.failedRequest).toBeDefined()
    const original = vi.mocked(api.chat).mock.calls[0]![0]
    vi.mocked(api.messages).mockResolvedValue([{ id: 1, role: 'user', content: original.message, user_id: 'demo_test', conversation_id: 'one', created_at: new Date().toISOString() }, { id: 2, role: 'assistant', content: '恢复回答', user_id: 'demo_test', conversation_id: 'one', created_at: new Date().toISOString() }])
    await chat.retry()
    expect(vi.mocked(api.chat).mock.calls[1]![0]).toEqual(original)
    expect(chat.current.value.messages).toHaveLength(2)
    expect(chat.current.value.failedRequest).toBeUndefined()
  })
  it('blocks POST replay if the durable history service cannot be verified', async () => {
    const { chat } = setup()
    await nextTick()
    vi.mocked(api.chat).mockRejectedValue(Error('network'))
    await chat.send('晚餐')
    vi.mocked(api.messages).mockRejectedValue(Error('503 memory store unavailable'))
    await chat.retry()
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(chat.current.value.error).toContain('暂不能安全重试')
    expect(chat.current.value.failedRequest).toBeDefined()
  })
  it('forbids replay of turns first sent while persistence was unavailable even after it recovers', async () => {
    vi.mocked(api.messages).mockRejectedValueOnce(Error('503 unavailable'))
    const { chat } = setup()
    await nextTick()
    vi.mocked(api.chat).mockRejectedValue(Error('lost response'))
    await chat.send('没有持久化的请求')
    vi.mocked(api.messages).mockResolvedValue([])
    await chat.retry()
    expect(api.chat).toHaveBeenCalledTimes(1)
    expect(chat.current.value.error).toContain('本轮发送前未能确认持久化服务')
  })
  it('ignores stale history responses and never fabricates historical execution metadata', async () => {
    const old = deferred<Awaited<ReturnType<typeof api.messages>>>()
    vi.mocked(api.messages).mockReturnValueOnce(old.promise)
    const { chat } = setup()
    await chat.loadHistory()
    old.resolve([{ id: 7, user_id: 'demo_test', conversation_id: 'one', role: 'assistant', content: '过期历史', created_at: new Date().toISOString() }])
    await nextTick()
    expect(chat.current.value.messages).toHaveLength(0)
    expect(chat.current.value.selectedResponse).toBeUndefined()
  })
})
