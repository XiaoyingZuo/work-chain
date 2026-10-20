"""命令行入口: workchain run/validate <chain.yaml>。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict

from .engine import Engine
from .spec import SpecError, load_chain


def _event_printer(event: str, name: str, payload: Dict[str, Any]) -> None:
    marks = {
        "start": "▶", "success": "✓", "failed": "✗",
        "retry": "↻", "blocked": "⊘",
    }
    mark = marks.get(event, event)
    if event == "start":
        print(f"  {mark} {name} (attempt {payload.get('attempt')})", flush=True)
    elif event == "success":
        keys = list(payload.get("outputs", {}))
        print(f"  {mark} {name}  outputs={keys}", flush=True)
    elif event == "retry":
        print(f"  {mark} {name} 重试中: {payload.get('error')}", flush=True)
    elif event == "failed":
        print(f"  {mark} {name} 失败: {payload.get('error')}", flush=True)
    elif event == "blocked":
        print(f"  ⊘ {name} 因上游失败被跳过", flush=True)


def cmd_validate(args) -> int:
    try:
        chain = load_chain(args.file)
    except (SpecError, FileNotFoundError) as e:
        print(f"校验失败: {e}", file=sys.stderr)
        return 1
    print(f"OK: chain '{chain.name}' 校验通过,{len(chain.nodes)} 个节点")
    return 0


def cmd_run(args) -> int:
    try:
        chain = load_chain(args.file)
    except (SpecError, FileNotFoundError) as e:
        print(f"加载失败: {e}", file=sys.stderr)
        return 1

    print(f"运行 chain '{chain.name}'(max_workers={chain.max_workers})")
    engine = Engine(chain, on_event=None if args.quiet else _event_printer)
    report = engine.run()

    print("\n=== 结果 ===")
    print(f"成功: {report.succeeded}")
    if report.failed:
        print(f"失败: {report.failed}")
    if report.blocked:
        print(f"跳过(上游失败): {report.blocked}")

    if args.output:
        dump = {
            "chain": report.chain,
            "ok": report.ok,
            "nodes": {
                n: {
                    "status": r.status, "outputs": r.outputs,
                    "error": r.error, "attempts": r.attempts,
                    "duration": round(r.duration, 3),
                }
                for n, r in report.results.items()
            },
            "blocked": report.blocked,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2)
        print(f"\n结果已写入 {args.output}")

    return 0 if report.ok else 2


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(prog="workchain", description="单机 Agent 工作流编排器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="运行一条 chain")
    p_run.add_argument("file", help="chain 的 YAML 文件")
    p_run.add_argument("-o", "--output", help="把结果写入 JSON 文件")
    p_run.add_argument("-q", "--quiet", action="store_true", help="不打印每个节点的进度")
    p_run.set_defaults(func=cmd_run)

    p_val = sub.add_parser("validate", help="只做静态校验,不执行")
    p_val.add_argument("file", help="chain 的 YAML 文件")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
