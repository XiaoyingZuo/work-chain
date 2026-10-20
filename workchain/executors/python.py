"""Python 执行器:加载 entry 指定的函数并调用,返回值(dict)作为 outputs。

entry 形如:
    "pkg.module:func"        通过 import 加载
    "/abs/path/to/file.py:func" 或 "rel/file.py:func"  按文件路径加载
函数以渲染后的 args 作为关键字参数调用;若函数签名含 context 参数,则额外注入 context。
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
from typing import Any, Callable, Dict

from . import ExecutorError
from ..render import render_any


class PythonExecutor:
    def run(self, node, chain, context: Dict[str, Any]) -> Dict[str, Any]:
        entry = node.get("entry")
        func = _load_callable(entry)
        args = render_any(dict(node.get("args", {}) or {}), context)

        if "context" in inspect.signature(func).parameters:
            args.setdefault("context", context)

        try:
            result = func(**args)
        except Exception as e:  # noqa: BLE001 - 把用户函数异常归一成执行失败
            raise ExecutorError(f"python entry '{entry}' 抛出异常: {e}") from e

        if result is None:
            return {}
        if not isinstance(result, dict):
            raise ExecutorError(
                f"python entry '{entry}' 必须返回 dict(将作为 outputs),实际返回 {type(result).__name__}"
            )
        return result


def _load_callable(entry: str) -> Callable:
    if not entry or ":" not in entry:
        raise ExecutorError(f"entry 格式应为 'module:func' 或 'file.py:func',当前={entry!r}")
    mod_part, func_name = entry.rsplit(":", 1)

    if mod_part.endswith(".py") or os.sep in mod_part or mod_part.startswith("."):
        path = os.path.abspath(mod_part)
        if not os.path.exists(path):
            raise ExecutorError(f"找不到文件: {path}")
        spec = importlib.util.spec_from_file_location("_wc_dyn_" + func_name, path)
        if spec is None or spec.loader is None:
            raise ExecutorError(f"无法加载模块文件: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(mod_part)

    if not hasattr(module, func_name):
        raise ExecutorError(f"模块中找不到函数 '{func_name}'")
    func = getattr(module, func_name)
    if not callable(func):
        raise ExecutorError(f"'{func_name}' 不是可调用对象")
    return func
