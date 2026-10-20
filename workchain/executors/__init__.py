"""执行器接口与注册表。

每个执行器把一个节点跑完,返回 outputs 字典(键应覆盖节点声明的 outputs)。
执行器自行用 render 渲染自己的类型字段;engine 负责构造 context 并校验返回的 outputs。
"""

from __future__ import annotations

from typing import Any, Dict, Protocol


class ExecutorError(RuntimeError):
    """节点执行失败(非零退出、请求出错、函数抛异常等)。"""


class Executor(Protocol):
    def run(self, node, chain, context: Dict[str, Any]) -> Dict[str, Any]:
        ...


def get_executor(node_type: str) -> Executor:
    from .llm import LLMExecutor
    from .python import PythonExecutor
    from .shell import ShellExecutor

    table = {
        "llm": LLMExecutor(),
        "python": PythonExecutor(),
        "shell": ShellExecutor(),
    }
    if node_type not in table:
        raise ExecutorError(f"未知节点类型: {node_type}")
    return table[node_type]
