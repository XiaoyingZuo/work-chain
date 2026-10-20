"""断点续跑 / 部分重跑的计划生成。

把"上一次 run 的状态 + 模式"翻译成 engine.run 需要的 (cached, runnable):
  - resume: 复用全部已成功节点,其余(失败/未跑/被阻塞)重新执行
  - from X:  X 及其所有后代强制重跑,其余已成功节点复用
  - only X:  只跑 X,其余已成功节点复用;要求 X 的直接上游都已成功
"""

from __future__ import annotations

from typing import Dict, Optional, Set, Tuple

from . import dag
from .engine import NodeResult
from .spec import Chain


def cached_from_state(state: dict) -> Dict[str, NodeResult]:
    """从持久化状态里取出已成功节点,重建为 status=cached 的 NodeResult。"""
    out: Dict[str, NodeResult] = {}
    for name, nd in state.get("nodes", {}).items():
        if nd.get("status") in ("succeeded", "cached"):
            out[name] = NodeResult(
                name=name,
                status="cached",
                outputs=nd.get("outputs", {}) or {},
                attempts=nd.get("attempts", 0),
                duration=nd.get("duration", 0.0),
            )
    return out


def plan_resume(chain: Chain, state: dict) -> Tuple[Dict[str, NodeResult], Optional[Set[str]]]:
    cached = {n: r for n, r in cached_from_state(state).items() if n in chain.node_map()}
    return cached, None


def plan_from(
    chain: Chain, state: dict, node: str
) -> Tuple[Dict[str, NodeResult], Optional[Set[str]]]:
    _require_node(chain, node)
    names = [n.name for n in chain.nodes]
    needs = chain.needs_map()
    invalid = {node} | dag.descendants(names, needs, [node])
    cached = {
        n: r for n, r in cached_from_state(state).items()
        if n in chain.node_map() and n not in invalid
    }
    return cached, None


def plan_only(
    chain: Chain, state: dict, node: str
) -> Tuple[Dict[str, NodeResult], Optional[Set[str]]]:
    _require_node(chain, node)
    cached = {n: r for n, r in cached_from_state(state).items() if n in chain.node_map()}
    cached.pop(node, None)  # 目标节点本身要重跑
    missing = [p for p in chain.node_map()[node].needs if p not in cached]
    if missing:
        raise ValueError(
            f"--only {node} 需要其上游先成功,但缺少上游成功记录: {missing};"
            f"请先 resume 或 run 跑出这些上游"
        )
    return cached, {node}


def _require_node(chain: Chain, node: str) -> None:
    if node not in chain.node_map():
        raise ValueError(f"chain 中不存在节点 '{node}'")
