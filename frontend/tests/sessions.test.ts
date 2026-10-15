import { describe, expect, it } from 'vitest'
import { useSessions } from '../src/features/sessions/useSessions'

function storage() {
  let value: string | null = null
  return { getItem: () => value, setItem: (_: string, next: string) => { value = next } }
}
describe('local session directory', () => {
  it('restores identity, selected conversation and rename without storing messages', () => {
    const store = storage()
    const first = useSessions(store)
    const id = first.activeId.value
    first.rename(id, '我的晚餐')
    first.markSent(id)
    first.create()
    first.select(id)
    const restored = useSessions(store)
    expect(restored.userId.value).toBe(first.userId.value)
    expect(restored.activeId.value).toBe(id)
    expect(restored.activeSession.value.name).toBe('我的晚餐')
    expect(restored.activeSession.value.hasMessages).toBe(true)
    expect(Object.keys(JSON.parse(store.getItem()!).sessions[0])).toEqual(['id', 'name', 'createdAt', 'updatedAt', 'hasMessages'])
  })
  it('continues with an explicit warning when browser storage is blocked', () => {
    const blocked = { getItem: () => { throw Error('blocked') }, setItem: () => { throw Error('blocked') } }
    const result = useSessions(blocked)
    expect(result.activeId.value).toBeTruthy()
    expect(result.storageError.value).toBeTruthy()
  })
  it('drops corrupt and duplicate records in a saved directory', () => {
    const date = new Date().toISOString()
    const valid = { id: 'good', name: '晚餐', createdAt: date, updatedAt: date, hasMessages: false }
    const result = useSessions({ getItem: () => JSON.stringify({ version: 1, userId: 'demo_test', activeId: 'missing', sessions: [null, valid, valid, { ...valid, id: '<invalid>' }] }), setItem: () => {} })
    expect(result.sessions.value).toHaveLength(1)
    expect(result.activeId.value).toBe('good')
  })
})
