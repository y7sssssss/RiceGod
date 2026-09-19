"""Conservative full-message rules; unknown expressions still reach extraction."""

import re


_GREETING = re.compile(r"(?:你好(?:呀|啊)?|您好|谢谢(?:你|您)?|多谢|感谢你|再见|拜拜|hello|hi|thanks|thank you|bye)", re.I)
_SELECTION = re.compile(
    r"(?:请)?(?:展开|介绍)?(?:(?:第[一二三四五六七八九十百两\d]+组|早餐|午餐|晚餐)(?:的)?)?第[一二三四五六七八九十百两\d]+"
    r"(?:道(?:菜)?|个|项)(?:的)?(?:怎么做|如何做|做法|步骤|详细做法|详情|展开|介绍一下)?"
)


def extraction_skip_reason(message: str) -> str | None:
    text = message.strip().strip("。！!？?，,；; \n\t")
    if _GREETING.fullmatch(text):
        return "pure_greeting"
    if _SELECTION.fullmatch(text):
        return "pure_recipe_selection"
    return None
