import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../src/api/client'
import { useMemory } from '../src/features/memory/useMemory'

vi.mock('../src/api/client', () => ({ api: { memories: vi.fn(), deleteMemory: vi.fn() }, errorMessage: (e: Error) => e.message }))
beforeEach(() => vi.resetAllMocks())
describe('memory API state', () => {
  it('shows service errors rather than reporting empty memory', async () => {
    vi.mocked(api.memories).mockRejectedValue(Error('503 unavailable'))
    const memory = useMemory('demo_test')
    await memory.refresh()
    expect(memory.error.value).toContain('503')
    expect(memory.loading.value).toBe(false)
  })
  it('refreshes actual data after delete and reports unconfirmed deletion', async () => {
    vi.mocked(api.deleteMemory).mockResolvedValue({ deleted: false })
    vi.mocked(api.memories).mockResolvedValue([])
    const memory = useMemory('demo_test')
    await memory.remove(42)
    expect(api.deleteMemory).toHaveBeenCalledWith('demo_test', 42)
    expect(api.memories).toHaveBeenCalledWith('demo_test')
    expect(memory.error.value).toContain('后端未确认删除')
    expect(memory.deleting.value).toBeNull()
  })
})
