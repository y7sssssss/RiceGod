# 会话 State 持久化

默认运行时使用官方 `langgraph-checkpoint-postgres==3.1.2`，与长期记忆复用现有 PostgreSQL，不新增 Redis 服务。会话内的消息、任务计划、任务结果、最终回答和 `last_recommendations` 都属于 Checkpoint；用户偏好、过敏与忌口仍由原有长期记忆服务管理。

## 保存和恢复

1. `create_runtime()` 打开连接池，初始化官方表，再将 Checkpointer 注入 `build_graph()`。
2. `ChatService` 用用户 ID 和会话 ID 组成 JSON 格式的 `_thread_key`。同一会话的新消息通过 `add_messages` 合并，图由官方机制保存执行状态。
3. 每轮先 `await graph.aget_state(config)`。已有 Checkpoint 消息时，不从原始消息表重复补入历史；没有消息才使用原有文本历史恢复逻辑。
4. 记忆写入完成后，最终确认回复继续按原消息 ID 更新 Checkpoint，并写入原始消息表，避免额外追加一条回复。
5. 新进程创建新的连接池和 Graph 后，相同 `_thread_key` 从 PostgreSQL 恢复 State，序号选择仍读取原来的推荐分组和顺序。

不在每轮手动把完整历史重新提交给图，不把推荐快照作为长期偏好。数据库中的原始消息用于 `/messages` 查询；Checkpoint 用于恢复图的结构化运行状态，两者承担不同职责。

## 配置与资源

`CHECKPOINT_BACKEND` 默认 `postgres`，也接受显式 `memory` 临时演示模式。连接沿用 `POSTGRES_DSN`，未设置时由已有 `POSTGRES_HOST/PORT/DB/USER/PASSWORD` 构造，密码由 psycopg 转义。现有 PostgreSQL Docker 数据卷无需变更。

启动自动调用官方 `setup()`，创建 `checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 与 `checkpoint_migrations`。迁移连接使用 autocommit，并用数据库会话级 advisory lock 串行协调多个启动过程。连接或迁移失败直接使启动失败，不静默退回内存。

当前 Windows Uvicorn 默认事件循环为 Proactor，psycopg 异步连接不支持它。因此使用官方同步 `PostgresSaver`，通过 `asyncio.to_thread` 桥接图需要的异步读取、写入、历史读取、线程删除和 Delta 历史方法。存储格式、SQL 和迁移都由官方实现负责，现有 Uvicorn 启动命令与同步验收脚本保持可用。同步历史迭代器在工作线程内消费完再向异步调用者返回，避免持有游标锁时调用者再次读 State 发生死锁。

连接池上限 4，设置连接与语句超时；`Runtime` 通过 `ExitStack` 管理关闭及初始化失败后的资源释放。严格 Msgpack 序列化显式允许本项目的 State、任务、推荐分组和条件类型。

## GustoBot 源码对比

本轮核对此前参考的 `develop` 分支代码，而非按 README 描述推断实现：

- [主图构建](https://github.com/skygazer42/GustoBot/blob/develop/gustobot/application/agents/lg_builder.py) 使用 `MemorySaver()`，并在 compile 时传入，未将 Redis 接到 Checkpointer。
- [State](https://github.com/skygazer42/GustoBot/blob/develop/gustobot/application/agents/lg_states.py) 使用 `add_messages` 合并消息；[聊天入口](https://github.com/skygazer42/GustoBot/blob/develop/gustobot/interfaces/http/v1/chat.py) 将 `session_id` 作为 `thread_id`，调用时只提交新用户消息，另外通过数据库 CRUD 保存消息和查询历史。
- [Redis 模块](https://github.com/skygazer42/GustoBot/blob/develop/gustobot/application/services/redis_cache.py) 提供语义回答缓存和 `RedisConversationHistory`。后者将消息 JSON 追加到 Redis List，裁剪数量并设置 TTL；它不是 LangGraph Checkpointer，也不保存完整 Graph State。检查的主聊天入口未调用这两个类。

值得借鉴的是稳定会话标识、框架消息合并、聊天日志与运行状态的职责划分。RiceGod 已有这些机制，此次进一步把 Checkpointer 改为数据库持久化。没有必要为了相同能力再引入 Redis 消息列表；消息数量上限和模型上下文管理可以后续独立设计，不能把有 TTL 和裁剪的列表称为永久完整历史。

官方持久化用法见 [LangGraph 添加记忆文档](https://docs.langchain.com/oss/python/langgraph/add-memory)。

## 验证与边界

接入持久化时严格序列化下完整回归 168 项通过，其中 4 项真实 PostgreSQL 集成用例不调用外部模型。覆盖新连接池与新 Graph 恢复、连续选第二与第三道、分组选择、用户与会话隔离、类型和消息 ID 保持、记忆确认回复保持、避免重复补入文本历史及异步历史读取的重入。

真实 HTTP 跨进程验证脚本分两阶段，中间由操作者重启 API：

```powershell
python -m scripts.verify_state_persistence --phase before --server-pid <重启前PID>
# 停止并重新启动当前 API
python -m scripts.verify_state_persistence --phase after --server-pid <重启后PID>
```

前一阶段调用真实模型、Embedding 和数据库保存偏好并推荐三道；后一阶段先从 Checkpoint 校验原始消息 ID 与推荐列表，再通过 TCP 连续寒暄、选择第二道、选择第三道，检查 Neo4j 来源和最终消息历史。本轮 API 进程从 60332 重启为 15324，19/19 项检查通过，结果见 [重启验收记录](state_persistence_verification.json)。独立 UUID 测试用户的消息、记忆和 Checkpoint 精确清理；中途取消可运行 `--phase cleanup`。

接入持久化后，真实任务条件与记忆确认专项再次运行，14/14 项通过，包含受控写入失败；记录见 [专项验收](task_memory_verification.json)。

原内存快照无法自动追溯迁移；历史文本不会被假定为结构化推荐列表。此次新增持久化不增加鉴权，不实现多进程同一会话写入协调，也不使长期记忆、Checkpoint 与消息表写入成为整体事务。持久化保留完整当前 State，但尚无 Checkpoint 保留期限或归档策略。
