// Mirrors ricegod/application/{chat,memory}/models.py and performance.py.
export interface ChatRequest {
  user_id: string
  conversation_id: string
  request_id: string
  message: string
}

export interface MessageRecord {
  id: number
  conversation_id: string
  user_id: string
  role: string
  content: string
  created_at: string
}

export type MemoryType = 'dietary_preference' | 'allergy' | 'disliked_ingredient' |
  'liked_cuisine' | 'cooking_constraint' | 'equipment' | 'household' | 'goal' | 'recipe_feedback' | 'todo'

export interface MemoryRecord {
  id: number
  user_id: string
  type: MemoryType
  content: string
  created_at: string
  updated_at: string
  source_message_id: number | null
  evidence: string
}

export interface MemoryWriteResult {
  available: boolean
  saved: MemoryRecord[]
  unchanged: MemoryRecord[]
  deleted_ids: number[]
  failed_count: number
  skipped_count: number
  extraction_status: 'not_run' | 'skipped' | 'completed' | 'failed'
  extraction_skip_reason: string | null
  extraction_error: 'model_unavailable' | 'model_call_failed' | 'invalid_output' | null
  status: string
  operation: {
    action: 'update' | 'delete'
    status: 'applied' | 'not_found' | 'ambiguous' | 'rejected' | 'failed'
    memory_id: number | null
    message: string
  } | null
}

export interface PerformanceReport {
  trace_id: string
  total_ms: number
  stages: { name: string; start_ms: number; duration_ms: number; status: 'ok' | 'error' | 'cancelled'; task_id: string | null }[]
  events: Record<string, unknown>[]
}

export interface ChatResponse {
  answer: string
  route: string
  logic: string
  sources: string[]
  used_memories: { id: number; type: string; content: string; score: number }[]
  memory_write: MemoryWriteResult
  performance: PerformanceReport | null
}
