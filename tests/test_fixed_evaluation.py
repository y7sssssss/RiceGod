import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ricegod.application.agents.state import PlannedTask, QueryPlan, TaskResult
from ricegod.evaluation.cases import EvaluationSuite, RecipeExpectation, Turn
from ricegod.evaluation.scoring import compare_reports, distribution, score_turn, satisfies_fields, summarize


def test_frozen_suite_is_valid_and_modes_are_explicit():
    suite = EvaluationSuite.model_validate_json(Path("data/evaluation/fixed_cases.json").read_text(encoding="utf-8"))
    turns = [t for s in suite.scenarios for t in s.turns]
    assert len(turns) == 36 and sum("offline" in t.modes for t in turns) == 31
    conflict = next(t for t in turns if t.id == "contradiction")
    assert conflict.routes == ["additional_query"] and conflict.conflict_clarification == "矛盾"
    assert conflict.recipes is None and not conflict.baseline


def test_conflicting_ingredients_require_a_question_without_recipe_execution():
    turn = Turn(id="conflict", message="用鸡蛋推荐菜，不要鸡蛋", conflict_clarification="矛盾")
    conversation = PlannedTask(id="t", route="additional_query", question=turn.message)
    state = {"plan": QueryPlan(tasks=[conversation]), "task_results": {}}
    body = {"answer":"要求矛盾，请问您想用鸡蛋还是排除鸡蛋？", "sources":[]}
    assert all(c["passed"] for c in score_turn(turn, body, state, [], {})[0])
    assert not all(c["passed"] for c in score_turn(turn, {**body, "answer":"要求矛盾。"}, state, [], {})[0])
    assert not all(c["passed"] for c in score_turn(turn, {**body, "sources":[{"name":"其他菜"}]}, state, [], {})[0])
    recipe = PlannedTask(id="r", route="recipe_query", question=turn.message)
    assert not all(c["passed"] for c in score_turn(turn, body, {**state, "plan":QueryPlan(tasks=[recipe])}, [], {})[0])


@pytest.mark.parametrize("wrong", [{"unknown":True}, {"count":11}])
def test_ground_truth_rejects_invalid_fields(wrong):
    with pytest.raises(ValidationError):
        RecipeExpectation.model_validate({"count":3, **wrong})


def test_suite_rejects_missing_or_cross_conversation_selection_reference():
    for conversation in ("other",):
        with pytest.raises(ValidationError):
            EvaluationSuite.model_validate({"version":"1", "description":"test", "scenarios":[{"id":"test", "turns":[
                {"id":"seed", "message":"推荐菜"}, {"id":"select", "message":"第二道", "conversation":conversation,
                    "selection":{"from_turn":"seed", "number":2}}]}]})


def test_independent_field_checker_handles_main_and_auxiliary_ingredients():
    expected = RecipeExpectation(count=1, required=["鸡蛋"], excluded=["香菜"], avoid_spicy=True,
                                 dish_types=["家常菜"], methods=["蒸"])
    row = {"name":"鸡蛋羹", "主食材":[{"name":"鸡蛋", "amount":"两个"}], "辅料":[["盐","少许"]],
           "类型":["家常菜"], "口味":["清淡"], "工艺":["蒸"]}
    assert satisfies_fields(row, expected)
    assert not satisfies_fields({**row, "辅料":[{"name":"香菜"}]}, expected)
    assert not satisfies_fields({**row, "辅料":[["辣椒粉","少许"]]}, expected)
    assert not satisfies_fields({**row, "主食材":[["土豆","一个"]]}, expected)


def test_missing_recipe_result_fails_case_even_with_no_returned_rows():
    turn = Turn(id="test", message="推荐两道鸡蛋菜", recipes=[RecipeExpectation(count=2, required=["鸡蛋"])])
    state = {"plan": QueryPlan(tasks=[]), "task_results": {}}
    checks, fields = score_turn(turn, {"answer":"没有结果"}, state, [], {})
    assert fields == {"returned":0, "matched":0} and not all(c["passed"] for c in checks)


def test_named_dish_can_satisfy_fields_without_ingredient_filter_extraction():
    turn = Turn(id="test", message="鸡蛋羹怎么做", recipes=[RecipeExpectation(count=1, dish_name="鸡蛋羹", required=["鸡蛋"])])
    from ricegod.domain.recipes import RecipeConstraints
    from ricegod.domain.recipe_ranking import RecipeRequest
    task = PlannedTask(id="t", route="recipe_query", question=turn.message,
        constraints=RecipeConstraints(dish_name="鸡蛋羹"), recipe_request=RecipeRequest(limit=1))
    state = {"plan": QueryPlan(tasks=[task]), "task_results":{"t": TaskResult(task_id="t", route="recipe_query",
        question=turn.message, records=[{"name":"鸡蛋羹","主食材":[["鸡蛋","两个"]]}])}}
    checks, fields = score_turn(turn, {"answer":"鸡蛋羹"}, state, [], {})
    assert all(c["passed"] for c in checks) and fields["matched"] == 1


def test_statistics_have_explicit_denominators_and_nearest_rank_percentiles():
    assert distribution([10,20,30,40])["p50_ms"] == 20
    assert distribution([10,20,30,40])["p95_ms"] == 40
    summary = summarize([])
    assert summary["planner_fallback"] == {"passed":0,"total":0,"rate":None}
    assert summary["returned_record_field_satisfaction"]["rate"] is None
    assert summary["request_latency"]["samples"] == 0


def test_fallback_statistics_exclude_rules_and_selection():
    cases = [{"passed":True,"checks":[],"fields":{"returned":0,"matched":0},"client_ms":10,
              "performance":{"total_ms":8,"stages":[],"events":[
                  {"kind":"planner","mode":"rules"},{"kind":"planner","mode":"selection"},
                  {"kind":"planner","mode":"model"},{"kind":"planner","mode":"fallback","phase":"validation"}]}}]
    summary = summarize(cases)
    assert summary["planner_fallback"] == {"passed":1,"total":2,"rate":.5}
    assert summary["planner_validation_failure"]["rate"] == .5


def test_report_comparison_rejects_different_mode_and_allows_code_changes():
    metadata = {"suite_sha256":"a","mode":"offline","recipe_sha256":"b","case_ids":["test"],"repeats":1,"code_sha256":"old"}
    previous = {"metadata":metadata,"summary":summarize([])}
    current = {"metadata":{**metadata,"code_sha256":"new"},"summary":summarize([])}
    assert compare_reports(current, previous)["compatible"]
    current["metadata"]["mode"] = "live"
    assert compare_reports(current, previous)["different_fields"] == ["mode"]


def test_offline_runner_saves_report_and_restores_configuration(tmp_path, monkeypatch):
    from scripts import evaluate_fixed_suite as runner
    from ricegod.config import settings
    suite = tmp_path / "suite.json"
    suite.write_text(json.dumps({"version":"test","description":"test","scenarios":[{"id":"one","turns":[
        {"id":"greeting","message":"你好","routes":["general_query"],"extraction_status":"skipped"}]}]}), encoding="utf-8")
    monkeypatch.setattr(settings, "LLM_API_KEY", "original-not-used")
    output = tmp_path / "report.json"
    report = runner.evaluate(suite, "offline", output)
    assert report["passed"] and report["test_rows_cleaned"]
    assert settings.LLM_API_KEY == "original-not-used"
    assert json.loads(output.read_text(encoding="utf-8"))["summary"]["timing_coverage"]["rate"] == 1
