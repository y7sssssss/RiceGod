# RiceGod Demo Questions

这些问题用于演示 RiceGod 的主要能力。建议先启动 Neo4j、Milvus，并完成 `recipe.json` 和 `data.txt` 的导入。完整演示流程见 [end_to_end_demo.md](end_to_end_demo.md)，示例返回见 [sample_responses.md](sample_responses.md)。

| 问题 | 预期路由 | 演示价值 |
| --- | --- | --- |
| 你好 | `general_query` | 普通对话，不触发知识库 |
| 我想做菜 | `additional_query` | 信息不足时追问 |
| 香肠炒菜干怎么做 | `recipe_query` | 菜谱做法、食材和步骤查询 |
| 香肠炒菜干需要什么食材 | `recipe_query` | 结构化食材查询 |
| 用鸡蛋能做什么菜 | `recipe_query` | 按食材找菜 |
| 有什么酱香的热菜 | `recipe_query` | 按口味和类型找菜 |
| 有什么麻辣的家常菜 | `recipe_query` | 按口味和菜品方向找菜 |
| 川菜有什么历史特点 | `food_knowledge` | 饮食文化 RAG |
| 佛跳墙的由来是什么 | `food_knowledge` | 菜品典故检索 |
| 鲁菜有什么文化特点 | `food_knowledge` | 菜系文化检索 |

## 路由规则速览

```text
做法 / 食材 / 找菜 / 推荐 -> recipe_query
历史 / 典故 / 菜系文化     -> food_knowledge
寒暄                       -> general_query
信息不足                   -> additional_query
```
