"""示例 Python 节点函数。每个函数返回 dict,键即节点的 outputs。"""


def to_upper(text: str) -> dict:
    return {"result": text.upper()}


def char_count(text: str) -> dict:
    return {"n": len(text)}
