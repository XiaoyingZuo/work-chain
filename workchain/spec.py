"""YAML 解析、数据模型与提交前静态校验。

静态校验把"断链"问题左移到加载期:
  - DAG 无环、needs 指向存在的节点
  - 模板里引用的 {{ nodes.X.outputs.k }} —— X 必须是上游且 k 必须被 X 声明
  - {{ params.x }} 必须在 params 中存在
  - 各类型节点的必填字段齐全
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml

from . import dag
from .render import extract_refs, parse_node_ref

VALID_TYPES = {"llm", "python", "shell"}
_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class SpecError(ValueError):
    """Spec 非法时抛出,信息面向使用者。"""


@dataclass
class Node:
    name: str
    type: str
    needs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    retries: int = 0
    timeout: float | None = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


@dataclass
class Chain:
    name: str
    nodes: List[Node]
    params: Dict[str, Any] = field(default_factory=dict)
    llm: Dict[str, Any] = field(default_factory=dict)
    max_workers: int = 4

    def node_map(self) -> Dict[str, Node]:
        return {n.name: n for n in self.nodes}

    def needs_map(self) -> Dict[str, List[str]]:
        return {n.name: list(n.needs) for n in self.nodes}


def _subst_env(value: Any) -> Any:
    """递归把字符串中的 ${VAR} 替换成环境变量。未定义则保持原样并留给校验报错。"""
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, list):
        return [_subst_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _subst_env(v) for k, v in value.items()}
    return value


def load_chain(path: str) -> Chain:
    """从 YAML 文件加载并静态校验一条 chain。"""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SpecError(f"{path}: 顶层必须是映射(mapping)")
    data = _subst_env(data)

    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise SpecError("nodes 必须是非空列表")

    nodes: List[Node] = []
    for i, rn in enumerate(raw_nodes):
        if not isinstance(rn, dict) or "name" not in rn:
            raise SpecError(f"第 {i} 个节点缺少 name 字段")
        name = str(rn["name"])
        ntype = rn.get("type")
        if ntype not in VALID_TYPES:
            raise SpecError(f"节点 '{name}' 的 type 必须是 {sorted(VALID_TYPES)},当前={ntype!r}")
        nodes.append(
            Node(
                name=name,
                type=ntype,
                needs=list(rn.get("needs", []) or []),
                outputs=list(rn.get("outputs", []) or []),
                retries=int(rn.get("retries", 0) or 0),
                timeout=rn.get("timeout"),
                raw=rn,
            )
        )

    chain = Chain(
        name=str(data.get("name", "unnamed")),
        nodes=nodes,
        params=dict(data.get("params", {}) or {}),
        llm=dict(data.get("llm", {}) or {}),
        max_workers=int(data.get("max_workers", 4) or 4),
    )
    validate(chain)
    return chain


# --- 类型必填字段 ---
_REQUIRED_FIELDS = {
    "llm": ["prompt"],
    "shell": ["command"],
    "python": ["entry"],
}


def validate(chain: Chain) -> None:
    names = [n.name for n in chain.nodes]
    nmap = chain.node_map()

    # 1. 名称唯一
    seen: set[str] = set()
    for n in names:
        if n in seen:
            raise SpecError(f"节点名重复: '{n}'")
        seen.add(n)

    # 2. needs 指向存在的节点 + 类型必填字段
    for node in chain.nodes:
        for dep in node.needs:
            if dep not in nmap:
                raise SpecError(f"节点 '{node.name}' 依赖了不存在的节点 '{dep}'")
        for req in _REQUIRED_FIELDS.get(node.type, []):
            if not node.raw.get(req):
                raise SpecError(f"{node.type} 节点 '{node.name}' 缺少必填字段 '{req}'")

    # 3. 无环(顺带确认拓扑可排)
    try:
        dag.topological_order(names, chain.needs_map())
    except dag.CycleError as e:
        raise SpecError(str(e)) from e

    # 4. 模板引用静态校验(断链左移)
    _validate_refs(chain, nmap)


def _validate_refs(chain: Chain, nmap: Dict[str, Node]) -> None:
    for node in chain.nodes:
        upstream = set(node.needs)
        for text in _iter_strings(node.raw):
            for ref in extract_refs(text):
                head = ref.split(".")[0]
                if head == "params":
                    key = ref.split(".", 1)[1] if "." in ref else ""
                    if key not in chain.params:
                        raise SpecError(
                            f"节点 '{node.name}' 引用了未定义的参数 {{{{ {ref} }}}}"
                        )
                elif head == "nodes":
                    parsed = parse_node_ref(ref)
                    if not parsed:
                        raise SpecError(
                            f"节点 '{node.name}' 的引用格式应为 nodes.<节点>.outputs.<键>: {{{{ {ref} }}}}"
                        )
                    src, key = parsed
                    if src not in nmap:
                        raise SpecError(f"节点 '{node.name}' 引用了不存在的节点 '{src}'")
                    if src not in upstream:
                        raise SpecError(
                            f"节点 '{node.name}' 引用了非上游节点 '{src}' 的输出;"
                            f"请把 '{src}' 加入其 needs"
                        )
                    if key not in nmap[src].outputs:
                        raise SpecError(
                            f"节点 '{node.name}' 引用了 '{src}' 未声明的输出 '{key}';"
                            f"'{src}' 已声明的 outputs={nmap[src].outputs}"
                        )
                else:
                    raise SpecError(
                        f"节点 '{node.name}' 的引用命名空间应为 params / nodes: {{{{ {ref} }}}}"
                    )


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
