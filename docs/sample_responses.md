# RiceGod Sample Responses

这些示例用于 README、面试讲解和本地验收。实际 `answer` 会随 LLM 配置、Neo4j/Milvus 是否已导入、检索结果略有变化；稳定字段是 `route`、`logic`、`sources` 和响应结构。

## 1. 普通对话

Request:

```json
{
  "message": "你好",
  "thread_id": "demo-general"
}
```

Response:

```json
{
  "answer": "你好，我是 RiceGod，可以帮你查询菜谱做法、食材搭配和饮食文化知识。",
  "route": "general_query",
  "logic": "keyword fallback: general chat",
  "sources": []
}
```

## 2. 信息不足追问

Request:

```json
{
  "message": "我想做菜",
  "thread_id": "demo-clarify"
}
```

Response:

```json
{
  "answer": "可以的。你想做哪道菜，或者手头有哪些食材？也可以告诉我偏好的口味。",
  "route": "additional_query",
  "logic": "keyword fallback: missing recipe details",
  "sources": []
}
```

## 3. 菜谱做法查询

Request:

```json
{
  "message": "香肠炒菜干怎么做",
  "thread_id": "demo-recipe"
}
```

Response:

```json
{
  "answer": "菜谱 1: 香肠炒菜干\n类型: 热菜\n口味: 酱香\n工艺: 炒\n耗时: 十分钟\n主食材: 香肠(2根)、菜干(200g)\n辅料: 豆豉(2匙)、蒜(少许)、葱(1颗)、酱油(2匙)、蚝油(1匙)、食用油(适量)\n做法: 1:准备的食材。2:香肉肠切片。3:爆香蒜末、豆豉。4:倒入香肉肠，中火翻炒。5:炒两分钟后倒入菜干翻炒。6:加入酱油、蚝油调味，再加入葱段。7:炒均匀即可出锅。8:成品。",
  "route": "recipe_query",
  "logic": "keyword fallback: recipe graph query",
  "sources": []
}
```

## 4. 食材查询

Request:

```json
{
  "message": "香肠炒菜干需要什么食材",
  "thread_id": "demo-ingredient"
}
```

Response:

```json
{
  "answer": "菜谱 1: 香肠炒菜干\n类型: 热菜\n口味: 酱香\n工艺: 炒\n耗时: 十分钟\n主食材: 香肠(2根)、菜干(200g)\n辅料: 豆豉(2匙)、蒜(少许)、葱(1颗)、酱油(2匙)、蚝油(1匙)、食用油(适量)\n做法: 1:准备的食材。2:香肉肠切片。3:爆香蒜末、豆豉。4:倒入香肉肠，中火翻炒。5:炒两分钟后倒入菜干翻炒。6:加入酱油、蚝油调味，再加入葱段。7:炒均匀即可出锅。8:成品。",
  "route": "recipe_query",
  "logic": "keyword fallback: recipe graph query",
  "sources": []
}
```

## 5. 按条件找菜

Request:

```json
{
  "message": "有什么酱香的热菜",
  "thread_id": "demo-filter"
}
```

Response:

```json
{
  "answer": "菜谱 1: 香肠炒菜干\n类型: 热菜\n口味: 酱香\n工艺: 炒\n耗时: 十分钟\n主食材: 香肠(2根)、菜干(200g)\n辅料: 豆豉(2匙)、蒜(少许)、葱(1颗)、酱油(2匙)、蚝油(1匙)、食用油(适量)\n做法: 1:准备的食材。2:香肉肠切片。3:爆香蒜末、豆豉。4:倒入香肉肠，中火翻炒。5:炒两分钟后倒入菜干翻炒。6:加入酱油、蚝油调味，再加入葱段。7:炒均匀即可出锅。8:成品。",
  "route": "recipe_query",
  "logic": "keyword fallback: recipe graph query",
  "sources": []
}
```

## 6. 饮食文化知识

Request:

```json
{
  "message": "川菜有什么历史特点",
  "thread_id": "demo-culture"
}
```

Response:

```json
{
  "answer": "当前未配置 LLM_API_KEY，无法生成文化知识总结。配置后可结合 Milvus 检索片段回答。",
  "route": "food_knowledge",
  "logic": "keyword fallback: food culture knowledge",
  "sources": []
}
```

配置 LLM 和 Milvus 后，这类问题会基于 `data/data.txt` 的检索片段生成饮食文化回答。
