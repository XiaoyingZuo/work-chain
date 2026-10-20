"""并发调度引擎:按 DAG 依赖并行执行节点,支持重试,失败则阻塞下游。"""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from . import dag
from .executors import ExecutorError, get_executor
from .spec import Chain, Node


@dataclass
class NodeResult:
    name: str
    status: str  # succeeded | failed | cached
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    attempts: int = 0
    duration: float = 0.0


@dataclass
class RunReport:
    chain: str
    results: Dict[str, NodeResult] = field(default_factory=dict)
    blocked: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    @property
    def succeeded(self) -> List[str]:
        return [n for n, r in self.results.items() if r.status in ("succeeded", "cached")]

    @property
    def failed(self) -> List[str]:
        return [n for n, r in self.results.items() if r.status == "failed"]

    @property
    def cached(self) -> List[str]:
        return [n for n, r in self.results.items() if r.status == "cached"]

    @property
    def ran(self) -> List[str]:
        return [n for n, r in self.results.items() if r.status in ("succeeded", "failed")]

    @property
    def ok(self) -> bool:
        return not self.failed and not self.blocked


# 事件: (event_type, node_name, payload)
EventHook = Callable[[str, str, Dict[str, Any]], None]
# checkpoint: (node_name, NodeResult) —— 每个节点终态确定后调用,用于落盘
Checkpoint = Callable[[str, "NodeResult"], None]


class Engine:
    def __init__(self, chain: Chain, on_event: Optional[EventHook] = None) -> None:
        self.chain = chain
        self.nmap = chain.node_map()
        self.needs = chain.needs_map()
        self.names = [n.name for n in chain.nodes]
        self.on_event = on_event or (lambda *a: None)

    def run(
        self,
        cached: Optional[Dict[str, "NodeResult"]] = None,
        runnable: Optional[Set[str]] = None,
        checkpoint: Optional[Checkpoint] = None,
    ) -> RunReport:
        """执行 chain。

        cached:   已成功节点及其 outputs,直接复用、不重跑(断点续跑/部分重跑用)。
        runnable: 若给定,仅调度此集合内的节点;其余既不是 cached 也不是 blocked 的记为 skipped。
        checkpoint: 每个节点确定终态后回调,用于持久化。
        """
        report = RunReport(chain=self.chain.name)
        succeeded: Set[str] = set()
        failed: Set[str] = set()
        finished: Set[str] = set()  # succeeded ∪ failed ∪ running
        attempts: Dict[str, int] = {n: 0 for n in self.names}
        outputs_store: Dict[str, Dict[str, Any]] = {}
        checkpoint = checkpoint or (lambda *a: None)

        # 预置 cached 节点(当作已成功)
        for name, res in (cached or {}).items():
            succeeded.add(name)
            finished.add(name)
            outputs_store[name] = res.outputs
            report.results[name] = res
            self.on_event("cached", name, {})

        def allowed(name: str) -> bool:
            return runnable is None or name in runnable

        with ThreadPoolExecutor(max_workers=self.chain.max_workers) as pool:
            running: Dict[Future, str] = {}

            def submit_ready() -> None:
                ready = dag.ready_nodes(self.names, self.needs, succeeded, finished)
                for name in ready:
                    if not allowed(name):
                        continue
                    if len(running) >= self.chain.max_workers:
                        break
                    finished.add(name)  # 占位防重复调度
                    attempts[name] += 1
                    self.on_event("start", name, {"attempt": attempts[name]})
                    fut = pool.submit(self._exec_node, self.nmap[name], outputs_store)
                    running[fut] = name

            submit_ready()
            while running:
                done, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for fut in done:
                    name = running.pop(fut)
                    node = self.nmap[name]
                    ok, result = fut.result()
                    if ok:
                        succeeded.add(name)
                        outputs_store[name] = result.outputs
                        result.attempts = attempts[name]
                        report.results[name] = result
                        self.on_event("success", name, {"outputs": result.outputs})
                        checkpoint(name, result)
                    else:
                        if attempts[name] <= node.retries:
                            # 还能重试:退出 finished 占位,等待下一轮重新调度
                            finished.discard(name)
                            self.on_event(
                                "retry", name,
                                {"attempt": attempts[name], "error": result.error},
                            )
                        else:
                            failed.add(name)
                            result.attempts = attempts[name]
                            report.results[name] = result
                            self.on_event("failed", name, {"error": result.error})
                            checkpoint(name, result)
                submit_ready()

        report.blocked = sorted(dag.blocked_nodes(self.names, self.needs, failed))
        for b in report.blocked:
            self.on_event("blocked", b, {})

        settled = succeeded | failed | set(report.blocked)
        report.skipped = sorted(n for n in self.names if n not in settled)
        for s in report.skipped:
            self.on_event("skipped", s, {})
        return report

    def _exec_node(self, node: Node, outputs_store) -> tuple[bool, NodeResult]:
        context = self._build_context(node, outputs_store)
        executor = get_executor(node.type)
        start = time.monotonic()
        try:
            outputs = executor.run(node, self.chain, context)
            self._check_outputs(node, outputs)
            return True, NodeResult(
                name=node.name, status="succeeded", outputs=outputs,
                duration=time.monotonic() - start,
            )
        except Exception as e:  # noqa: BLE001
            msg = str(e) if isinstance(e, ExecutorError) else f"{type(e).__name__}: {e}"
            return False, NodeResult(
                name=node.name, status="failed", error=msg,
                duration=time.monotonic() - start,
            )

    def _build_context(self, node: Node, outputs_store) -> Dict[str, Any]:
        nodes_ctx = {
            dep: {"outputs": outputs_store.get(dep, {})} for dep in node.needs
        }
        return {"params": self.chain.params, "nodes": nodes_ctx}

    @staticmethod
    def _check_outputs(node: Node, outputs: Dict[str, Any]) -> None:
        missing = [k for k in node.outputs if k not in outputs]
        if missing:
            raise ExecutorError(
                f"节点 '{node.name}' 声明了 outputs {node.outputs},但实际缺少 {missing};"
                f"实际产出键={list(outputs)}"
            )
