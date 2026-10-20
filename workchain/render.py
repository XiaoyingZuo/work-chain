"""极简模板渲染:仅支持 {{ a.b.c }} 形式的变量引用。

可引用的命名空间:
    {{ params.<key> }}            链级参数
    {{ nodes.<name>.outputs.<k> }} 上游节点的输出
不引入 Jinja2,避免执行任意表达式带来的复杂度与安全面。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

_TOKEN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class RenderError(KeyError):
    """模板引用无法解析时抛出。"""


def extract_refs(text: str) -> List[str]:
    """抽取文本中全部引用路径,如 'nodes.outline.outputs.text'。"""
    if not isinstance(text, str):
        return []
    return [m.group(1) for m in _TOKEN.finditer(text)]


def _resolve(path: str, context: Dict[str, Any]) -> Any:
    cur: Any = context
    parts = path.split(".")
    for i, part in enumerate(parts):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            so_far = ".".join(parts[: i + 1])
            raise RenderError(f"模板引用无法解析: {{{{ {path} }}}} (在 '{so_far}' 处中断)")
    return cur


def render(text: str, context: Dict[str, Any]) -> str:
    """把 text 中的 {{ ... }} 全部替换为 context 中的值。"""
    if not isinstance(text, str):
        return text

    def repl(m: re.Match) -> str:
        value = _resolve(m.group(1), context)
        return value if isinstance(value, str) else _to_str(value)

    return _TOKEN.sub(repl, text)


def render_any(value: Any, context: Dict[str, Any]) -> Any:
    """递归渲染 str / list / dict。"""
    if isinstance(value, str):
        return render(value, context)
    if isinstance(value, list):
        return [render_any(v, context) for v in value]
    if isinstance(value, dict):
        return {k: render_any(v, context) for k, v in value.items()}
    return value


def _to_str(value: Any) -> str:
    import json

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def parse_node_ref(path: str) -> Tuple[str, str] | None:
    """若 path 形如 nodes.<name>.outputs.<key>,返回 (name, key),否则 None。"""
    parts = path.split(".")
    if len(parts) == 4 and parts[0] == "nodes" and parts[2] == "outputs":
        return parts[1], parts[3]
    return None
