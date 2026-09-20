# 固定评测与阶段耗时

固定集：[data/evaluation/fixed_cases.json](../data/evaluation/fixed_cases.json)。当前版本 1.2，包含八个独立场景、36 轮真实模式检查，其中 31 轮适用于离线模式。场景之间使用不同的随机测试用户；场景内共享用户记忆，按 `conversation` 区分会话。每次重复运行使用全新的用户，不复用前次记录。

## 评测范围

| 类别 | 固定检查 |
| --- | --- |
| 菜谱条件 | 指定数量、数量上限、多食材、主辅料忌口、不吃辣、口味、工艺、类型和指定菜名详情 |
| 条件冲突 | “用鸡蛋推荐两道菜，不要鸡蛋”应指出同一食材要求冲突，并询问用户选择，不直接检索其他菜 |
| 多任务 | 早餐/晚餐食材、数量和口味分别生效，共享过敏限制 |
| 连续追问 | 寒暄后选择第二道、继续选择第三道、分组选择、歧义/越界澄清、跨会话隔离 |
| 长历史 | 显式构造三十轮历史夹具后推荐和序号选择 |
| 记忆 | 已有忌口进入检索、更正、遗忘后立即推荐、未找到目标、歧义目标与批量遗忘拒绝 |
| 真实模式专项 | 新记忆提取保存、跨会话召回、临时条件不保存、真实文化证据和文化任务依赖菜谱 |

同一问题同时要求使用和排除鸡蛋，按用户最终确认的产品规则应先澄清。版本 1.1 曾错误采用排除优先，版本 1.2 修正这一条期望；这是明确的需求修订，不是把所有失败都改成成功。其他非冲突案例保持不变。数量不足时不放宽硬条件；多食材案例允许实际候选不足，但仍核验请求上限没有被模型改写。菜名详情案例核验菜名定位和实际记录食材，不要求把菜名中的每种食材重复提取成过滤条件。

期望数据由固定集显式指定。`evaluation/scoring.py` 独立检查返回记录的主辅料、口味、工艺等字段，不调用生产的 `RecipeConstraints.matches` 作为判断标准。连续选择的期望菜名取自指定前轮的实际展示列表和独立指定的编号，检查本轮定位、返回记录和实际回答一致。

`evaluation/cases.py` 使用严格模型拒绝未知字段、重复 id、不合法数量和无效的前轮引用。改动期望条件、问题或数据都应调整版本；报告另存固定集 SHA-256，防止比较时混入不同题目。

## 离线与真实模式

离线运行使用完整聊天处理器和实际本地 19,669 道菜谱，关闭模型调用；数据库记忆、向量和 Checkpointer 明确使用内存夹具。它验证确定性规则、排序、任务分发、状态与明确记忆操作，不验证 SQL、语义召回或模型提取。真实模式使用当前配置的模型、Embedding、Neo4j、Milvus 和 PostgreSQL，要求 PostgreSQL Checkpointer，并在结束时清理本次随机测试用户的消息、记忆和 Checkpoint。

长历史的三十轮是直接写入的夹具，不是三十轮真实模型生成的对话。Checkpointer 也按相同用户与会话写入夹具。每轮核验 API 回复、Checkpoint 回答和原始 assistant 历史一致。

离线八个指定检索案例另与旧关键词评分检索比较：同一本地数据和用户问题，不传硬条件和个性化排序请求。冲突澄清案例不属于检索基线。此基线只表示局部检索策略，不能描述为过去完整系统的版本效果，不能用它计算端到端速度提升。

## 耗时记录

`application/performance.py` 使用 `perf_counter` 和请求级 `ContextVar`。每次 `/chat` 响应新增可空的 `performance` 字段，包含随机 `trace_id`、`total_ms`、`stages` 和控制范围内的 `events`。计时不写入 State、原始聊天文本或长期记忆。不同请求独立收集，图内并行任务共享当前请求收集器。

主要阶段：

| 阶段 | 范围 |
| --- | --- |
| `total` | 从等待会话锁到完成聊天与原始消息写入，包含等待锁，不包含 HTTP 序列化和网络传输 |
| `lock_wait` | 同一用户会话锁的等待 |
| `checkpoint_read` / `history_recovery` | 检查持久化 State、必要时恢复原始历史 |
| `user_message_write` / `assistant_message_write` | 原始消息表写入 |
| `memory_operation` | 召回前的明确更正或遗忘 |
| `memory_recall` | 记忆向量召回和完整限制记忆读取 |
| `graph` | 完整图调用，包含图调度、节点和 Checkpoint 开销 |
| `planner` | 统一领域识别和任务规划，包括输入构建、模型调用、校验与规则回退 |
| `recipe_retrieval` / `knowledge_retrieval` | 每个检索任务；保留任务 id |
| `conversation` / `answer` | 普通对话任务与统一回答节点；确定性短路也按实际执行计时 |
| `memory_extraction` | 答复后的提取、去重协调和实际记忆写入 |
| `memory_extract_model` / `memory_reconcile_model` | 提取和协调模型子调用，含客户端重试 |
| `memory_confirmation` / `checkpoint_write` | 实际结果确认及必要的 State 更新 |

每项记录相对请求开始的 `start_ms`、`duration_ms`、`status`，检索任务附 `task_id`。异常和取消也保留跨度；错误请求在服务日志中保留计时，HTTP 500 不返回 `performance`，评测因此另统计耗时覆盖率。

父子跨度重叠、检索可能并行，所以各阶段平均值和 `total_work_ms` 不能相加作为用户等待时间。请求总耗时只取 `total`。报告也分别汇总普通图请求、序号选择、直接记忆操作，避免低耗时短路请求掩盖正常推荐延迟。

Planner 事件区分 `rules`、`selection`、`model`、`fallback`，规划回退记录 `context/model/validation` 阶段和异常类型，任务记录 `ok/empty/degraded/failed/blocked`。上下文事件包含估算输入长度和预算，不是提供商精确 token 数。性能日志和事件不记录用户 id、原话、凭证或完整异常文本。

## 指标定义

- 案例通过：所有适用字段与流程检查通过的轮次 / 已执行轮次。单项失败不能被大量无关检查掩盖。
- 返回记录字段满足：符合独立期望字段的记录 / 返回记录。无记录的分母为零时返回 null，不算 100%；数量、缺失任务和可用状态另行检查。
- 推荐数量检查：满足固定数量或显式允许的不足策略的任务 / 有数量期望的任务。
- 连续选择正确：定位、记录和回答均符合前轮指定编号的轮次 / 有明确选择期望的轮次；澄清另计入案例检查。
- 规划回退：进入模型规划路径后回退的次数 / 进入模型规划路径次数，包含预算拦截；规则规划和确定性选择不进入分母。另列 `validation` 阶段失败率。
- 记忆检查：明确写入状态、持久记忆存在或不存在的断言通过数 / 适用断言数；不是记忆提取精确率、召回率。
- 平均、P50、P95：按实际请求或阶段样本计算，分位数采用 nearest-rank；样本数同时显示。小样本 P95 不能当作生产 SLA。

固定集不评估口味满意度、所有自然语言表达、文化史实正确性、鉴权、多进程并发或生产负载。当前真实请求通过 FastAPI TestClient 进入完整处理器，不包含真实 HTTP 网络耗时；用户与会话编号隔离也不等于身份认证。

## 运行与比较

```powershell
$env:LANGGRAPH_STRICT_MSGPACK='true'
$env:LANGSMITH_TRACING='false'
$env:LANGCHAIN_TRACING_V2='false'
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode offline
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode live

# 真实模式会产生模型/Embedding 调用；可按独立场景选择。
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode live --scenario followups --output docs/followups_evaluation.json

# 同一固定集、模式、数据、案例和重复次数的报告才能比较。
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode live --repeats 3 --output docs/baseline_live.json
# 优化之后按相同参数复跑。
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode live --repeats 3 --output docs/optimized_live.json --compare docs/baseline_live.json
```

输出 JSON 保存逐轮问题、答案、检查、实际任务证据与计时；同名 Markdown 展示摘要和失败案例，CSV 导出阶段耗时。报告包含数据和代码 hash、模型与上下文配置、包版本、开始/结束时间及清理状态，不保存 API key 或 DSN。模型和配置可以变更，但比较结果会提示配置差异；需自行控制数据库内容、机器负载和服务商状态，不能仅凭一次差值断言某项改动带来提升。

当前离线结果见 [固定离线评测](fixed_evaluation_offline.md)，真实结果见 [固定真实评测](fixed_evaluation_live.md)。全量回归 **257 passed**，包含八项真实 PostgreSQL 用例；新增覆盖请求隔离、并行跨度、失败与取消、严格评测数据、独立字段判断、分母与分位数、报告比较和离线运行。固定题目允许按照明确产品规则版本化修订，不允许为隐藏真实失败而放宽标准。

## 文化检索专项评测

文化检索使用 [42 条固定案例](../data/evaluation/culture_retrieval_cases.json)，直接以当前切分后的 `data.txt_<chunk_index>` 作为相关性标注，不额外创建逻辑文档。案例覆盖食材与烹饪历史、命名来历、古代菜单、文学饮食、依赖菜谱的连续追问，以及知识库无答案和域外干扰问题。

运行时先用向量相似度阈值过滤候选，再按 Rerank 相关度阈值过滤最终片段；全部低于阈值时主链路返回 empty，由回答节点明确说明知识库没有可靠依据。当前固定集的无阈值校准中，有答案案例最低 Rerank 分为 0.741，无答案案例最高为 0.269，因此默认阈值采用向量 0.35、Rerank 0.50，在两组之间保留余量。该数值是当前模型、语料和题集下的经验阈值，模型或语料变化后应重新评测。

专项报告统计 Recall@K、MRR、无答案识别率和已有 `source/chunk_index` 字段的覆盖率，并保存固定集、原始语料的 SHA-256 与切分参数，避免语料或切分变化后继续比较失效编号。

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_culture_retrieval
```

实际 TCP HTTP 验收 **13/13** 项通过，覆盖响应计时、并行阶段区间、不同请求 trace 隔离、计时不进入持久化 State、推荐与连续选择，以及 PostgreSQL 中的回答一致性。本轮测试用户数据已清理，见 [HTTP 验收记录](performance_http_verification.json)。

## 优化前基线

按照版本 1.2 的冲突澄清规则，对已有版本 1.1 执行证据重新评分：离线 **30/31** 条通过，真实 **36/36** 条通过。没有重新执行模型或数据库请求，原始执行代码 hash、答案和耗时保持不变；JSON 中 `rescored_from` 保存原套件与报告 hash、修订时间及仅修改的一条案例 id，原报告另行归档。真实运行返回记录字段检查 **53/53**、明确序号选择 **4/4**、记忆断言 **15/15**、请求计时覆盖 **36/36**。这些是固定案例的检查结果，不能据此宣称系统总体准确率为 100%。测试用户数据已清理。

真实模型对于 `contradiction` 返回 `additional_query`，随后指出逻辑矛盾并询问用户，没有执行菜谱检索，符合规则。此前认定其为路由故障的结论撤回。离线确定性路径仍直接返回其他菜，因此这一条在修订后的评测中失败。评测新增独立的 `conflict_clarification` 检查，要求解释冲突、提出问题、不返回来源且不执行菜谱任务；它不依赖序号选择专用的 `recipe_selection_error`。

真实请求总耗时平均 **9.18 秒**、P50 **11.42 秒**、P95 **18.10 秒**。分别统计后，普通图路径 25 次请求平均 **12.77 秒**，序号选择路径 7 次平均 **1.46 秒**，直接记忆操作 4 次平均 **0.32 秒**。Planner 阶段 32 次执行平均 **5.17 秒**，其中含规则和选择短路；21 次进入模型规划路径中，8 次因独立菜谱任务的原文范围重叠而校验回退（**38.1%**）。路由、回答阶段平均分别 **1.79 秒**、**1.75 秒**。

以上为 Planner 优化前的单次基线，另存 [优化前报告](planner_baseline_live.md)。阶段跨度不能相加，基线自身不证明优化收益。

## Planner 优化后实测

同一固定集版本 1.2、相同模型与上下文配置下，重新实际执行：真实 **36/36**、离线 **30/31**。这次报告没有重新评分，模型与数据库请求是真实运行。真实字段检查 **53/53**、连续序号选择 **4/4**、记忆断言 **15/15** 通过，测试数据已清理。离线唯一失败仍是已有的鸡蛋冲突澄清。该结果来自 Router/Planner 合并前，作为历史基线保留。

原固定题目的 Planner 模型路径次数从 **21 次降为 0 次**，本轮走规则规划 25 次、序号选择 7 次；并非关闭整个系统的模型。回答与必要的记忆提取仍使用实际模型。由于这轮模型规划分母为零，回退率为 null，不能把它写成“模型规划成功率 100%”。复杂规划另外使用补充题目验收。此处的独立 Router 阶段耗时只属于历史报告，当前阶段统计统一记为 `planner`。

普通图请求平均耗时由 **12.77 秒降至 6.26 秒**；全部 36 次请求平均由 **9.18 秒降至 4.68 秒**，P95 由 **18.10 秒降至 8.84 秒**。Planner 阶段平均由 **5.17 秒降至 65.89 毫秒**（都含规则和序号选择）。对照身份检查通过且配置未变化；单次小样本、数据库和提供商状态仍可能影响差异，这些是本轮观测，不能当作普遍性能保证。

实现与复杂规划补充验收见 [Planner 优化设计](planner_optimization_design.md)。如需统一无模型或模型故障时的澄清行为，后续仍需给确定性路径补冲突检测。

补充复杂规划评测 **3/3** 通过，三次实际模型规划、零回退，记录字段 **6/6** 和数量 **5/5** 通过。平均总耗时 **19.38 秒**，复杂规划仍需后续优化；结果见 [复杂规划报告](planner_evaluation_live.md)。本地 API 已重启，当前实际 HTTP 验收 **13/13** 项通过。

## 统一 Planner 当前离线验收

Router 合并后使用 GustoBot Conda 环境重跑固定集，并连接包含 19,669 道菜的真实 Neo4j 数据。结果为 **31/31 案例、283/283 检查通过**；其中 Planner 规则路径 20 次、序号选择路径 7 次，没有模型规划样本。`用鸡蛋推荐两道菜，不要鸡蛋` 现在确定性进入 `additional_query` 并返回冲突澄清，不执行菜谱检索；分餐中的相反条件继续按任务独立处理。

本轮另以真实 Neo4j 执行 `search/similar/substitute` 集成用例，2/2 通过。报告见 [统一 Planner 离线评测](fixed_evaluation_offline_unified.md)。该报告不调用外部模型，不代表复杂语义 Planner 或文化检索的线上效果；需要模型的三条复杂规划题由开发者按上面的 `planner_cases.json` 命令手动复测。

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m scripts.evaluate_fixed_suite --mode live --suite data/evaluation/planner_cases.json --output docs/planner_evaluation_live_unified.json
```
