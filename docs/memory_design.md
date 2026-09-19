# 食谱智能客服长期记忆设计

## 目标
- `messages`：保存原始对话日志
- `memories`：保存用户原话记忆、向量，以及可核验的来源消息和证据
- 新问题到来时按 `user_id` 做向量召回，完整补充过敏和忌口，并用于检索约束与回复 prompt
- 支持记忆查看和删除

## 数据表
```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id VARCHAR(128) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  role VARCHAR(16) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memories (
  id BIGSERIAL PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  type VARCHAR(64) NOT NULL,
  content TEXT NOT NULL,
  embedding VECTOR NOT NULL,
  source_message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,
  evidence TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## Memory Extractor Prompt
系统提示词见：
- `ricegod/application/memory/prompts.py` 中 `MEMORY_EXTRACTOR_SYSTEM_PROMPT`

输出格式：
```json
{
  "memories": [
    {
      "type": "dietary_preference | allergy | disliked_ingredient | liked_cuisine | cooking_constraint | equipment | household | goal | recipe_feedback | todo",
      "content": "不能吃辣",
      "source_message_id": 123,
      "evidence": "不能吃辣"
    }
  ]
}
```

## 写入流程

以下是普通提取路径；纯寒暄和纯序号指令跳过提取，明确更正与遗忘在召回前走独立流程。跳过规则、支持句式和混合请求处理见 [记忆操作设计](memory_operations_design.md)。

1. 先写 `messages` 中的当前 user 消息；若本轮用户消息写入失败，跳过提取，避免误用上轮消息
2. 按 `user_id + conversation_id` 读取消息，仅把最新一条用户消息及其数据库 id 交给提取模型；助手回复和旧轮次不进入提取上下文
3. 要求候选提供 `source_message_id` 和连续的用户原话 `evidence`。校验来源是本轮用户消息、证据确实在消息中，并将候选内容固定为核验后的原话，再过滤敏感信息（token/password/api_key/验证码等）
4. 为每条 `memory.content` 生成 embedding
5. 按 `user_id` 搜索相关记忆，仅保留与候选同类型的记录，交给 LLM 仅在 add / no_op 中选择一个工具；未返回工具时重试一次，普通提取不授权更新或删除
6. 新增保存候选原话，不采用工具自行扩展的内容。明确更正和遗忘独立处理：核验本轮数据库用户消息，定位唯一目标，检查原内容再更新或删除，并在召回前生效；详见 [记忆操作设计](memory_operations_design.md)
7. 协调模型失败时仍使用原有相似度阈值兜底：超过阈值跳过，否则尝试新增。这不等同于可靠的事实冲突判断，仍需后续改进
8. 返回 `MemoryWriteResult`：实际新增/更新的记录、已有完全相同的记录、成功删除的 id，以及失败和跳过计数。模型工具调用意图不能作为保存成功的证据；仅语义相似或普通 no_op 不确认已保存
9. 根据结果补充保存确认，只确认实际成功的内容。部分成功说明其余未保存；不可用、失败、未提取或跳过时不宣称已记住。最终回复替换同 id 的 Checkpoint 助手消息后，再保存 PostgreSQL assistant 消息

所有回答路径在写入前提示模型不得提前确认记忆操作，图内最终回答也移除提前确认句，避免初始 Checkpoint 留下虚假的“已记住”。`/chat` 再次检查回答并附加基于写入结果的确认。Checkpoint 同步或 assistant 消息日志失败会记录异常，不撤销已经完成的记忆写入；当前没有跨数据库与 Checkpoint 的事务一致性。

例如，用户只说“不吃香菜，也不能吃辣”，即使助手推荐了葱香菜式，也不能据此写入“喜欢葱香”。之后用户明确说“我喜欢葱香口味”时，该原话可以作为新记忆保存。证据缺失、消息 id 无效、助手消息作为来源、证据不在用户消息中时，候选被拒绝。

启动时通过 `ADD COLUMN IF NOT EXISTS` 补充来源字段。历史记忆仍可读取，来源默认为 `null`、证据为空，不伪造来源或自动删除旧记录；过去已经污染的记忆需要单独审查。删除原始消息会将来源 id 置空，保留证据文本。

原话校验保证可追溯，不等同于理解正确。类型分类、否定语境中的片段选择，以及同类型不同事实的冲突协调仍依赖模型判断。

## 召回流程
1. 新问题生成 query embedding
2. 按 `user_id` 在 `memories` 做向量检索（TopK）
3. 独立读取该用户全部 `allergy` 与 `disliked_ingredient`，合并去重；最终数量可以超过 TopK
4. 已识别的食材排除和不吃辣约束进入任务计划，同时用于 Neo4j 查询与本地 JSON 兜底
5. 将召回记忆拼成 prompt 注入最终回复；单独补充的限制记忆 score 为 0，不表示语义匹配分数

## API
- `POST /chat`
  - 入参：`user_id`, `conversation_id`, `message`
  - 行为：保存 user 消息 -> 必要时执行明确操作 -> 召回记忆 -> 生成回答或直接返回操作结果 -> 必要时提取新增记忆 -> 确认并同步 Checkpoint 与 assistant 历史
  - 返回：`answer`, `used_memories`, `memory_write`（原有字段，以及 extraction_status、extraction_skip_reason、operation）

- `GET /memories?user_id=...`
  - 返回当前用户全部长期记忆，包含 `source_message_id` 和 `evidence`

- `DELETE /memories/{id}?user_id=...`
  - 删除指定记忆

- `GET /messages?user_id=...&conversation_id=...`
  - 仅返回对应用户与会话的原始对话消息

消息查询和记忆删除接口必须传 `user_id`。当前是按调用方提供的 id 隔离，未实现身份认证。仅提取本轮消息避免中性后续问题重提旧事实；明确的单条更正与遗忘已独立实现，但任意改写、跨轮指代、批量修改和删除标记未实现。删除长期记忆不删除聊天历史及历史 Checkpoint。

任务编排与评估详见 [优化设计](optimization_design.md)。

独立任务和写入确认的专项验收见 [真实模型与数据库记录](task_memory_verification.json)。其中最后一轮专门对唯一虚构测试用户拒绝 memory INSERT/UPDATE，验证写入失败的回复；模型和检索调用仍为真实服务。该验收不代表任意复杂冲突或遗忘表达都已正确理解。
