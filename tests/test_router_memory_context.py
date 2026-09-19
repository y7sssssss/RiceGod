import asyncio

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ricegod.application.agents import router, conversation
from ricegod.application.agents.state import AgentState, Router


def test_messages_with_memory_context_includes_context_as_system_message():
    state = AgentState(
        messages=[HumanMessage(content="帮我推荐晚餐")],
        memory_context="已知用户长期记忆：- [goal] 用户在减脂。",
    )

    messages = conversation._messages_with_memory_context(state)

    assert len(messages) == 2
    assert isinstance(messages[0], SystemMessage)
    assert "长期记忆" in messages[0].content
    assert isinstance(messages[1], HumanMessage)


def test_general_query_injects_memory_context_temporarily(monkeypatch):
    captured: dict[str, object] = {}

    class FakeModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return AIMessage(content="好的")

    monkeypatch.setattr(router.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(conversation, "_build_llm", lambda temperature=0.7: FakeModel())

    state = AgentState(
        messages=[HumanMessage(content="你好")],
        memory_context="已知用户长期记忆：- [allergy] 用户对花生过敏。",
    )
    result = asyncio.run(conversation.general_query(state))

    assert "messages" in result
    llm_messages = captured["messages"]
    assert isinstance(llm_messages[0], SystemMessage)
    assert isinstance(llm_messages[1], SystemMessage)
    assert "长期记忆" in llm_messages[1].content
    assert isinstance(llm_messages[2], HumanMessage)


def test_analyze_intent_does_not_use_memory_context(monkeypatch):
    captured: dict[str, object] = {}

    class FakeStructuredModel:
        async def ainvoke(self, messages):
            captured["messages"] = messages
            return Router(type=["general_query"], logic="ok")

    class FakeModel:
        def with_structured_output(self, schema):
            return FakeStructuredModel()

    monkeypatch.setattr(router.settings, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(router, "_build_llm", lambda temperature=0: FakeModel())

    state = AgentState(
        messages=[HumanMessage(content="你好")],
        memory_context="已知用户长期记忆：- [allergy] 用户对花生过敏。",
    )
    result = asyncio.run(router.analyze_intent(state))

    assert result["router"].type == ["general_query"]
    llm_messages = captured["messages"]
    assert isinstance(llm_messages[0], SystemMessage)
    assert llm_messages[0].content == router.ROUTER_SYSTEM_PROMPT
    assert isinstance(llm_messages[1], HumanMessage)
    assert all("长期记忆" not in str(msg.content) for msg in llm_messages)


def test_heuristic_router_detects_recipe_and_food_knowledge_tasks(monkeypatch):
    monkeypatch.setattr(router.settings, "LLM_API_KEY", None)

    state = AgentState(messages=[HumanMessage(content="我不吃辣，推荐一道川菜并介绍一下它的来历")])
    result = asyncio.run(router.analyze_intent(state))

    assert result["router"].type == ["recipe_query", "food_knowledge"]
