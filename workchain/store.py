"""运行状态持久化。

每次 run 分配一个 run_id,状态写到 <state_dir>/<run_id>.json,默认 state_dir=.workchain/runs。
每个节点完成后 checkpoint 落盘,因此进程中途崩溃也能 resume。
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional

DEFAULT_STATE_DIR = os.path.join(".workchain", "runs")


class RunStore:
    def __init__(self, state_dir: str = DEFAULT_STATE_DIR) -> None:
        self.state_dir = state_dir

    def _ensure_dir(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)

    def path(self, run_id: str) -> str:
        return os.path.join(self.state_dir, f"{run_id}.json")

    def new_run_id(self, chain_name: str) -> str:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in chain_name) or "chain"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{safe}-{stamp}-{random.randint(0, 0xFFFF):04x}"

    def save(self, state: Dict[str, Any]) -> None:
        self._ensure_dir()
        state["updated_at"] = time.time()
        tmp = self.path(state["run_id"]) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path(state["run_id"]))  # 原子替换,避免半写状态

    def load(self, run_id: str) -> Dict[str, Any]:
        with open(self.path(run_id), "r", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, run_id: str) -> bool:
        return os.path.exists(self.path(run_id))

    def list_runs(self) -> List[Dict[str, Any]]:
        if not os.path.isdir(self.state_dir):
            return []
        runs = []
        for fn in os.listdir(self.state_dir):
            if not fn.endswith(".json"):
                continue
            try:
                data = self.load(fn[:-5])
            except (OSError, json.JSONDecodeError):
                continue
            nodes = data.get("nodes", {})
            runs.append(
                {
                    "run_id": data.get("run_id"),
                    "chain": data.get("chain"),
                    "updated_at": data.get("updated_at", 0),
                    "succeeded": sum(1 for v in nodes.values() if v.get("status") == "succeeded"),
                    "failed": sum(1 for v in nodes.values() if v.get("status") == "failed"),
                    "total": len(nodes),
                }
            )
        runs.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return runs

    def latest(self) -> Optional[str]:
        runs = self.list_runs()
        return runs[0]["run_id"] if runs else None


def new_state(run_id: str, chain_name: str, chain_file: str, params: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    return {
        "run_id": run_id,
        "chain": chain_name,
        "chain_file": os.path.abspath(chain_file),
        "params": params,
        "created_at": now,
        "updated_at": now,
        "nodes": {},
    }


def node_result_to_dict(result) -> Dict[str, Any]:
    d = asdict(result)
    d["duration"] = round(d.get("duration", 0.0), 3)
    return d
