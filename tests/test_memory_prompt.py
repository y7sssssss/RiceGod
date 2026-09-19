from datetime import datetime, timezone

from ricegod.application.memory.models import MemorySearchResult
from ricegod.application.memory.service import MemoryService


def test_build_memory_prompt():
    memories = [
        MemorySearchResult(
            id=1,
            user_id="u1",
            type="allergy",
            content="用户对花生过敏，推荐时必须避免花生及花生制品。",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            score=0.95,
        )
    ]
    prompt = MemoryService.build_memory_prompt(memories)
    assert "已知用户长期记忆" in prompt
    assert "[allergy]" in prompt
    assert "花生过敏" in prompt
