"""Shell 执行器:渲染 command 后通过子进程执行,捕获 stdout/stderr/returncode。"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict

from . import ExecutorError
from ..render import render


class ShellExecutor:
    def run(self, node, chain, context: Dict[str, Any]) -> Dict[str, Any]:
        command = render(node.get("command"), context)
        cwd = node.get("cwd")
        env_extra = node.get("env")
        env = None
        if env_extra:
            import os

            env = {**os.environ, **{str(k): str(v) for k, v in env_extra.items()}}

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=node.timeout,
                cwd=cwd,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise ExecutorError(f"shell 超时({node.timeout}s): {command}") from e

        stdout = proc.stdout
        if proc.returncode != 0:
            raise ExecutorError(
                f"shell 退出码 {proc.returncode}: {command}\nstderr: {proc.stderr.strip()[:2000]}"
            )

        outputs: Dict[str, Any] = {
            "stdout": stdout.strip(),
            "stderr": proc.stderr.strip(),
            "returncode": proc.returncode,
        }
        if node.get("parse_json"):
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError as e:
                raise ExecutorError(f"parse_json=true 但 stdout 不是合法 JSON: {e}") from e
            if isinstance(parsed, dict):
                outputs.update(parsed)
        return outputs
