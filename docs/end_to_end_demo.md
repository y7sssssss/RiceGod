# RiceGod End-to-End Demo

这份文档用于面试演示或本地验收：从依赖服务启动、数据导入、API 启动，到调用 `/chat` 获取示例回答。

> 推荐使用 Python `urllib` 调用接口。PowerShell 的 `Invoke-WebRequest` 在某些终端里会把中文响应显示成乱码，但 API 实际返回是 UTF-8。

## 1. 确认运行环境

```powershell
C:\Anaconda\envs\GustoBot\python.exe --version
```

预期输出类似：

```text
Python 3.10.20
```

## 2. 启动 Neo4j 和 Milvus

在项目根目录运行：

```powershell
docker-compose up -d
```

服务地址：

```text
Neo4j Browser: http://localhost:7474
Milvus: localhost:19530
```

## 3. 导入数据

导入菜谱知识图谱到 Neo4j：

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m scripts.import_recipes_to_neo4j --clear
```

导入饮食文化文本到 Milvus：

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m scripts.ingest_food_knowledge_to_milvus --clear
```

如果只想演示 API 和本地 JSON fallback，可以先跳过 Neo4j/Milvus 导入；`recipe_query` 在 Neo4j 不可用时会使用 `data/recipe.json` 作为兜底证据。

## 4. 启动 API

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m uvicorn ricegod.main:app --reload --host 127.0.0.1 --port 8000
```

打开 API 文档：

```text
http://127.0.0.1:8000/docs
```

健康检查：

```text
http://127.0.0.1:8000/health
```

预期响应：

```json
{"status":"ok","service":"ricegod"}
```

## 5. 调用 /chat

推荐用 Python 调用，中文显示最稳定：

```powershell
C:\Anaconda\envs\GustoBot\python.exe -c "import json, urllib.request; data=json.dumps({'message':'香肠炒菜干怎么做','thread_id':'demo'}, ensure_ascii=False).encode('utf-8'); req=urllib.request.Request('http://127.0.0.1:8000/chat', data=data, headers={'Content-Type':'application/json'}); print(urllib.request.urlopen(req, timeout=30).read().decode('utf-8'))"
```

也可以使用 PowerShell：

```powershell
$body = @{ message = "香肠炒菜干怎么做"; thread_id = "demo" } | ConvertTo-Json
Invoke-WebRequest -Uri "http://127.0.0.1:8000/chat" `
  -Method POST `
  -Body $body `
  -ContentType "application/json; charset=utf-8" `
  -UseBasicParsing
```

## 6. 推荐演示顺序

1. `你好`
   - 展示普通对话路由：`general_query`
2. `我想做菜`
   - 展示信息不足追问：`additional_query`
3. `香肠炒菜干怎么做`
   - 展示菜谱图谱查询或本地 JSON fallback：`recipe_query`
4. `香肠炒菜干需要什么食材`
   - 展示结构化食材查询：`recipe_query`
5. `有什么酱香的热菜`
   - 展示按口味和类型找菜：`recipe_query`
6. `川菜有什么历史特点`
   - 展示饮食文化 RAG：`food_knowledge`

示例返回见 [sample_responses.md](sample_responses.md)。

## 7. 测试

```powershell
C:\Anaconda\envs\GustoBot\python.exe -m pytest -q
```

预期结果：

```text
13 passed
```
