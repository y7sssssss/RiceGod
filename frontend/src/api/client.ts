import axios from 'axios'
import type { ChatRequest, ChatResponse, MemoryRecord, MessageRecord } from '../types/api'

const client = axios.create({ baseURL: '/api', timeout: 20_000 })

export function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.code === 'ECONNABORTED') return '请求超时，后端可能仍在处理。请核对历史后再重试。'
    if (!error.response) return '无法连接后端，请确认 FastAPI 已启动，并检查网络和 Vite 代理配置。'
    const detail: unknown = error.response.data?.detail
    const suffix = typeof detail === 'string' ? `：${detail}` : ''
    if (error.response.status >= 500 && !error.response.data) return `后端或 API 代理未能完成请求（HTTP ${error.response.status}），请确认 FastAPI 已启动及代理目标正确。`
    if (error.response.status === 503) return `服务暂不可用${suffix}`
    if (error.response.status === 422) return `请求参数校验失败${suffix || '，请检查输入长度和身份参数。'}`
    return `请求失败（HTTP ${error.response.status}）${suffix}`
  }
  return error instanceof Error ? error.message : '发生未知错误，请稍后重试。'
}

// Deliberately no automatic retry interceptor: POST /chat can have side effects.
export const api = {
  health: () => client.get<{ status: string; service: string }>('/health').then(r => r.data),
  chat: (request: ChatRequest) => client.post<ChatResponse>('/chat', request, { timeout: 180_000 }).then(r => r.data),
  messages: (user_id: string, conversation_id: string) => client.get<MessageRecord[]>('/messages', {
    params: { user_id, conversation_id, limit: 1000 },
  }).then(r => r.data),
  memories: (user_id: string) => client.get<MemoryRecord[]>('/memories', { params: { user_id } }).then(r => r.data),
  deleteMemory: (user_id: string, id: number) => client.delete<{ deleted: boolean }>(`/memories/${id}`, {
    params: { user_id },
  }).then(r => r.data),
}
