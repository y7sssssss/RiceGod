"""Readable reports and exportable per-stage statistics from the same JSON."""

import csv
from pathlib import Path


def _rate(value):
    rate = value["rate"]
    return f"{value['passed']}/{value['total']} ({rate:.1%})" if rate is not None else "无适用样本"


def write_readable_report(report, output: Path):
    summary, metadata = report["summary"], report["metadata"]
    lines = ["# 固定评测结果", "", f"模式：`{metadata['mode']}`；固定集版本：`{metadata['suite_version']}`；重复次数：{metadata['repeats']}。", "",
             "结果仅针对固定案例中的字段和流程，不代表总体问答准确率、口味满意度或史实准确率。", "",
             "| 指标 | 结果 |", "| --- | --- |"]
    if metadata.get("rescored_from"):
        revision = metadata["rescored_from"]
        lines[4:4] = [f"本报告依据用户明确的冲突澄清规则，对版本 {revision['suite_version']} 的已有执行证据重新评分；没有重新执行模型或数据库请求，原始耗时保持不变。仅修订 `contradiction` 案例，原报告已归档。", ""]
    for label, key in [("案例通过", "cases"), ("返回记录字段满足", "returned_record_field_satisfaction"),
                       ("推荐数量检查", "recipe_quantity_accuracy"), ("连续选择正确", "selection_accuracy"),
                       ("记忆检查", "memory_checks"), ("模型规划回退", "planner_fallback"),
                       ("规划校验失败", "planner_validation_failure"), ("耗时记录覆盖", "timing_coverage")]:
        lines.append(f"| {label} | {_rate(summary[key])} |")
    latency = summary["request_latency"]
    lines += ["", f"服务内请求耗时：平均 {latency['mean_ms']} ms，P50 {latency['p50_ms']} ms，P95 {latency['p95_ms']} ms（{latency['samples']} 个样本）。", "",
              "## 按请求路径的耗时", "", "| 路径 | 样本 | 平均 ms | P50 ms | P95 ms |", "| --- | --- | --- | --- | --- |"]
    for path, values in summary["request_latency_by_path"].items():
        lines.append(f"| {path} | {values['samples']} | {values['mean_ms']} | {values['p50_ms']} | {values['p95_ms']} |")
    lines += ["", "## 各阶段耗时", "", "阶段包含父子跨度和并行任务，不能将下表各项相加作为用户等待时间。", "",
              "| 阶段 | 调用数 | 平均 ms | P50 ms | P95 ms | 异常数 |", "| --- | --- | --- | --- | --- | --- |"]
    for name, values in summary["stage_latency"].items():
        lines.append(f"| {name} | {values['samples']} | {values['mean_ms']} | {values['p50_ms']} | {values['p95_ms']} | {values['errors']} |")
    baseline = summary["legacy_keyword_baseline"]
    if baseline["cases"]:
        lines += ["", "## 本地关键词基线", "",
                  f"同一批 {baseline['cases']} 个离线案例：旧关键词评分返回字段满足 {_rate(baseline['fields'])}；当前约束与排序返回字段满足 {_rate(summary['current_on_baseline_cases']['fields'])}。", "",
                  "此基线只比较本地检索字段，不代表历史完整系统版本的效果或速度。"]
    failures = [case for case in report["cases"] if not case["passed"]]
    lines += ["", "## 失败案例", ""]
    if failures:
        for case in failures:
            failed = ", ".join(c["name"] for c in case["checks"] if not c["passed"])
            lines.append(f"- `{case['id']}`（第 {case['repeat']} 次）：{failed}")
    else:
        lines.append("本轮没有失败案例。")
    if "comparison" in report:
        comparison = report["comparison"]
        lines += ["", "## 与指定报告比较", ""]
        if comparison["compatible"]:
            lines.append("身份检查通过；以下差值为当前减去指定报告，不能直接据此作因果归因。")
            lines.append(f"配置发生变化：{comparison['configuration_changed']}。")
            lines.extend(f"- `{key}`：{value}" for key, value in comparison["deltas"].items())
        else:
            lines.append("拒绝比较：" + ", ".join(comparison["different_fields"]))
    lines += ["", f"测试用户已清理：{report['test_rows_cleaned']}。", ""]
    output.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")
    with output.with_suffix(".csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["stage", "samples", "mean_ms", "p50_ms", "p95_ms", "errors", "total_work_ms"])
        writer.writeheader()
        for name, values in summary["stage_latency"].items():
            writer.writerow({"stage": name, **values})
