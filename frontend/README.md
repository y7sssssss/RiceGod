# RiceGod AI 饮食助手 · Web 前端

独立的桌面端 Vue 3 应用，使用 Composition API、`<script setup>`、TypeScript、Vite、Element Plus、Axios 和 markdown-it。原有 `ricegod/static/index.html` 保留，后端代码及契约未作修改。

## 启动

需要 Node.js **20.19+ 或 22.12+**（推荐当前 LTS）、npm，以及按项目根目录文档配置的真实 FastAPI 和数据库服务。

在项目根目录执行：

```powershell
cd frontend
npm ci
npm run dev
```

访问 **http://127.0.0.1:5173/**。在另一个终端，从项目根目录启动原有后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn ricegod.main:app --host 127.0.0.1 --port 8000
```

Python 环境应以项目现有后端配置为准。PostgreSQL/checkpointer 初始化失败会阻止 FastAPI 启动；前端不能代替这些依赖。

默认 `/api/*` 由 Vite 代理到 `http://127.0.0.1:8000/*`。更换后端地址时：

```powershell
Copy-Item .env.example .env.local
# 编辑 .env.local 中的 API_PROXY_TARGET 后重启 Vite
```

`API_PROXY_TARGET` 只供 Vite 读取。前端没有 API Key 配置项，请勿添加密钥到 `VITE_*` 变量。

## 检查与预览

```powershell
npm run typecheck
npm test
npm run build
npm run preview
```

构建产物位于 `dist/`，本地生产预览地址为 **http://127.0.0.1:4173/**。预览同样配置 `/api` 代理。正式托管 `dist/` 时，需要由 Web 服务器将 `/api/*` 反向代理到 FastAPI，并去掉 `/api` 前缀；静态文件本身不会代理请求。

## 界面与操作

- 左栏创建、选择与重命名会话。会话名称是本地元数据，不从模型回答生成。
- 中栏提供六类提问入口；点击只填入输入框，由用户确认发送。Enter 发送，Shift+Enter 换行，中文输入法组词时不会误发。
- 每次新轮次生成独立 `request_id`，会话内保持同一个 `conversation_id`。请求过程中禁止重复提交；不同会话独立处理返回结果。
- 回复使用安全 Markdown 渲染，显示实际 `sources`。HTTP(S) 来源可打开，图数据库名称等来源以文本显示。
- 右栏“我的饮食画像”读取真实记忆，按类型分组；删除需要确认，之后重新查询后端。
- “Agent 执行信息”显示选中回答的路由、路由说明、来源、使用的记忆、写入回执和可选耗时/阶段/事件。消息下方可选择本轮执行信息。
- 健康状态只表示 `/health` 是否可访问，不代表模型和数据库全部健康。点击状态可重新检查。

## 会话、重试与隐私边界

`localStorage` 的 `ricegod.sessions.v1` 只保存版本、本地演示 `user_id`、当前会话 ID，以及会话 ID、名称、创建/更新时间、是否发过消息。**不保存消息正文、草稿、长期记忆、执行内容或密钥。**重命名的内容本身保存在本地，请勿将敏感信息用作会话名称。浏览器阻止存储时会显示警告。

消息通过 `GET /messages` 获取。页面刷新会恢复本地会话目录、身份和选中项，再从后端读取该会话历史。无法查询已有会话历史时，阻止继续发送以避免在未知上下文中续聊；未发送过的新会话允许尝试聊天，但会明确提示持久化不可用。

`POST /chat` 没有自动重试。失败后保留原始请求在内存中，“核对并重试”仅在**原轮次发送前已成功查询历史**、且重试前再次查询成功时复用原始四个请求字段。该策略依据当前源码：`/messages` 使用具体 `MemoryStore`，同一实现具备 `begin_turn`、唯一键、会话锁及完成结果缓存。没有成功验证持久化的轮次禁止重发，即使服务稍后恢复。

这不是无条件的 exactly-once 保证：失败轮次可重新执行，原后端在失败之前的外部副作用不一定回滚；服务端重部署、更换数据库或多个实例配置不一致也超出前端可确认的范围。没有请求状态/能力 API，因此不自动探测完成状态，也不根据消息文本相同就推断请求完成。重试成功后重新读取历史，避免把已提交的回答在界面追加两次。

刷新后原失败请求的正文与幂等键不会恢复或自动重放，只会加载后端已经保存的消息。记忆删除失败或结果不明时不自动重试，提示刷新核对。

当前没有认证，仅使用此浏览器生成的演示身份；**不具备真实用户隔离、安全授权或跨设备同步能力**。

## 文件组织

```text
frontend/
├── public/ricegod.svg
├── src/
│   ├── api/client.ts
│   ├── components/SourceList.vue
│   ├── composables/useHealth.ts
│   ├── features/
│   │   ├── agent/AgentPanel.vue
│   │   ├── chat/{ChatWorkspace.vue,MessageBubble.vue,useChat.ts,markdown.ts}
│   │   ├── memory/{MemoryPanel.vue,useMemory.ts}
│   │   └── sessions/{SessionSidebar.vue,useSessions.ts}
│   ├── styles/{theme.css,markdown.css}
│   ├── types/api.ts
│   ├── App.vue
│   └── main.ts
├── tests/{chat,memory,sessions,markdown}.test.ts
├── docs/
│   ├── DEVELOPMENT_REPORT.md
│   └── screenshots/desktop-*.jpg
├── .env.example
├── .gitignore
├── index.html
├── package.json
├── package-lock.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

实际联调状态、接口限制及验收步骤见 [开发报告](docs/DEVELOPMENT_REPORT.md)。
