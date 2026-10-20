"""DAG 拓扑排序、环检测与 ready 集合计算。纯函数,不依赖执行状态的具体实现。"""

from __future__ import annotations

from typing import Dict, Iterable, List, Set


class CycleError(ValueError):
    """DAG 中存在环时抛出。"""


def topological_order(names: Iterable[str], needs: Dict[str, List[str]]) -> List[str]:
    """Kahn 算法:返回拓扑序;若有环则抛 CycleError。

    names: 全部节点名
    needs: 节点名 -> 其直接上游节点名列表
    """
    names = list(names)
    indegree: Dict[str, int] = {n: 0 for n in names}
    children: Dict[str, List[str]] = {n: [] for n in names}
    for node, parents in needs.items():
        for p in parents:
            children[p].append(node)
            indegree[node] += 1

    # 保持声明顺序的稳定输出
    queue = [n for n in names if indegree[n] == 0]
    order: List[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for child in children[cur]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(order) != len(names):
        remaining = sorted(set(names) - set(order))
        raise CycleError(f"DAG 中存在环,涉及节点: {', '.join(remaining)}")
    return order


def ready_nodes(
    names: Iterable[str],
    needs: Dict[str, List[str]],
    succeeded: Set[str],
    finished: Set[str],
) -> List[str]:
    """返回当前可执行的节点:尚未结束,且所有上游都已成功。

    finished = 已成功 ∪ 已失败 ∪ 运行中(由调用方传入以避免重复调度)。
    """
    result: List[str] = []
    for n in names:
        if n in finished:
            continue
        if all(p in succeeded for p in needs.get(n, [])):
            result.append(n)
    return result


def blocked_nodes(
    names: Iterable[str],
    needs: Dict[str, List[str]],
    failed: Set[str],
) -> Set[str]:
    """返回因上游失败而永远无法执行的节点(传递闭包)。"""
    blocked: Set[str] = set()
    changed = True
    dead = set(failed)
    while changed:
        changed = False
        for n in names:
            if n in dead:
                continue
            if any(p in dead for p in needs.get(n, [])):
                dead.add(n)
                blocked.add(n)
                changed = True
    return blocked
