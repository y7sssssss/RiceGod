from ricegod.application.memory.models import MemoryItem
from ricegod.application.memory.safety import filter_sensitive_memories, is_sensitive_text


def test_sensitive_text_detection():
    assert is_sensitive_text("api_key is sk-1234567890123456")
    assert is_sensitive_text("password: 123456")
    assert not is_sensitive_text("用户不吃香菜")


def test_filter_sensitive_memories():
    memories = [
        MemoryItem(type="disliked_ingredient", content="用户不吃香菜", source_message_id=1, evidence="用户不吃香菜"),
        MemoryItem(type="todo", content="token=abcdef", source_message_id=2, evidence="token=abcdef"),
    ]
    filtered = filter_sensitive_memories(memories)
    assert len(filtered) == 1
    assert filtered[0].content == "用户不吃香菜"
