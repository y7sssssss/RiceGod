# 文化检索固定评测

固定集版本：`1.0`；Top-K：4。

该评测衡量检索文档命中、拒答和来源字段，不判断历史陈述本身的真实性。

| 指标 | 结果 |
| --- | ---: |
| Recall@K | 100.0% |
| MRR | 1.000 |
| 无答案识别率 | 0.0% |
| 来源覆盖率 | 100.0% |
| 通过案例 | 37/42 |

## 失败案例

- `kungpao_origin_absent`：期望 []，实际 ['data.txt_12', 'data.txt_23', 'data.txt_20', 'data.txt_10']，错误 无
- `fotiaoqiang_origin_absent`：期望 []，实际 ['data.txt_25', 'data.txt_26', 'data.txt_24', 'data.txt_22']，错误 无
- `cantonese_cuisine_absent`：期望 []，实际 ['data.txt_4', 'data.txt_3', 'data.txt_10', 'data.txt_15']，错误 无
- `coffee_absent`：期望 []，实际 ['data.txt_12', 'data.txt_26', 'data.txt_7', 'data.txt_11']，错误 无
- `pizza_absent`：期望 []，实际 ['data.txt_21', 'data.txt_13', 'data.txt_19', 'data.txt_20']，错误 无
