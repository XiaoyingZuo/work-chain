"""命令行入口: workchain run / resume / runs / validate。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Dict, Optional, Set

from . import resume as resume_mod
from .engine import Engine, NodeResult, RunReport
from .spec import Chain, SpecError, load_chain
from .store import RunStore, new_state, node_result_to_dict

_MARKS = {
    "start": "▶", "success": "✓", "failed": "✗",
    "retry": "↻", "blocked": "⊘", "cached": "⤼", "skipped": "·",
}


def _event_printer(event: str, name: str, payload: Dict[str, Any]) -> None:
    mark = _MARKS.get(event, event)
    if event == "start":
        print(f"  {mark} {name} (attempt {payload.get('attempt')})", flush=True)
    elif event == "success":
        print(f"  {mark} {name}  outputs={list(payload.get('outputs', {}))}", flush=True)
    elif event == "cached":
        print(f"  {mark} {name} 复用上次成功结果", flush=True)
    elif event == "retry":
        print(f"  {mark} {name} 重试中: {payload.get('error')}", flush=True)
    elif event == "failed":
        print(f"  {mark} {name} 失败: {payload.get('error')}", flush=True)
    elif event == "blocked":
        print(f"  {mark} {name} 因上游失败被跳过", flush=True)
    elif event == "skipped":
        print(f"  {mark} {name} 未在本次执行范围内", flush=True)


def _print_report(report: RunReport) -> None:
    print("\n=== 结果 ===")
    ran = report.ran
    print(f"本次执行: {ran}" if ran else "本次执行: (无,全部复用)")
    if report.cached:
        print(f"复用: {report.cached}")
    if report.failed:
        print(f"失败: {report.failed}")
    if report.blocked:
        print(f"阻塞(上游失败): {report.blocked}")
    if report.skipped:
        print(f"跳过(不在范围): {report.skipped}")


def _write_output(report: RunReport, path: str) -> None:
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
        "skipped": report.skipped,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, indent=2)
    print(f"结果已写入 {path}")


def _execute(
    chain: Chain,
    store: Optional[RunStore],
    state: Dict[str, Any],
    cached: Dict[str, NodeResult],
    runnable: Optional[Set[str]],
    quiet: bool,
    output: Optional[str],
) -> int:
    print(f"run_id={state['run_id']}  chain='{chain.name}'  max_workers={chain.max_workers}")

    def checkpoint(name: str, result: NodeResult) -> None:
        state["nodes"][name] = node_result_to_dict(result)
        if store:
            store.save(state)

    for n, r in (cached or {}).items():
        state["nodes"][n] = node_result_to_dict(r)
    if store:
        store.save(state)

    engine = Engine(chain, on_event=None if quiet else _event_printer)
    report = engine.run(cached=cached, runnable=runnable, checkpoint=checkpoint)

    for b in report.blocked:
        state["nodes"][b] = {"status": "blocked"}
    for s in report.skipped:
        state["nodes"].setdefault(s, {"status": "skipped"})
    if store:
        store.save(state)
        print(f"\n状态已保存: {store.path(state['run_id'])}")

    _print_report(report)
    if output:
        _write_output(report, output)
    return 0 if report.ok else 2


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
    store = None if args.no_persist else RunStore(args.state_dir)
    run_id = (store.new_run_id(chain.name) if store
              else f"{chain.name}-{int(time.time())}")
    state = new_state(run_id, chain.name, args.file, chain.params)
    return _execute(chain, store, state, cached={}, runnable=None,
                    quiet=args.quiet, output=args.output)


def cmd_resume(args) -> int:
    store = RunStore(args.state_dir)
    run_id = args.run_id or store.latest()
    if not run_id:
        print("没有可续跑的 run;先用 workchain run 跑一次", file=sys.stderr)
        return 1
    if not store.exists(run_id):
        print(f"找不到 run_id={run_id}", file=sys.stderr)
        return 1
    state = store.load(run_id)
    try:
        chain = load_chain(state["chain_file"])
    except (SpecError, FileNotFoundError) as e:
        print(f"重新加载 chain 失败({state.get('chain_file')}): {e}", file=sys.stderr)
        return 1

    try:
        if args.only:
            cached, runnable = resume_mod.plan_only(chain, state, args.only)
        elif getattr(args, "from_node", None):
            cached, runnable = resume_mod.plan_from(chain, state, args.from_node)
        else:
            cached, runnable = resume_mod.plan_resume(chain, state)
    except ValueError as e:
        print(f"计划生成失败: {e}", file=sys.stderr)
        return 1

    return _execute(chain, store, state, cached=cached, runnable=runnable,
                    quiet=args.quiet, output=args.output)


def cmd_runs(args) -> int:
    store = RunStore(args.state_dir)
    runs = store.list_runs()
    if not runs:
        print(f"{args.state_dir} 下没有记录")
        return 0
    print(f"{'run_id':<40} {'chain':<18} {'成功/总数':<10} 更新时间")
    for r in runs:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["updated_at"]))
        prog = f"{r['succeeded']}/{r['total']}" + (f" ✗{r['failed']}" if r["failed"] else "")
        print(f"{r['run_id']:<40} {str(r['chain']):<18} {prog:<10} {ts}")
    return 0


def _add_state_dir(p: argparse.ArgumentParser) -> None:
    p.add_argument("--state-dir", default=".workchain/runs", help="运行状态目录")


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(prog="workchain", description="单机 Agent 工作流编排器")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="从头运行一条 chain")
    p_run.add_argument("file", help="chain 的 YAML 文件")
    p_run.add_argument("-o", "--output", help="把结果写入 JSON 文件")
    p_run.add_argument("-q", "--quiet", action="store_true", help="不打印每个节点的进度")
    p_run.add_argument("--no-persist", action="store_true", help="不持久化运行状态(不可续跑)")
    _add_state_dir(p_run)
    p_run.set_defaults(func=cmd_run)

    p_res = sub.add_parser("resume", help="续跑某次 run(默认最近一次),复用已成功节点")
    p_res.add_argument("run_id", nargs="?", help="要续跑的 run_id,省略则取最近一次")
    g = p_res.add_mutually_exclusive_group()
    g.add_argument("--from", dest="from_node", metavar="NODE",
                   help="强制从该节点开始重跑(连同其所有下游)")
    g.add_argument("--only", metavar="NODE", help="只重跑该节点(要求上游已成功)")
    p_res.add_argument("-o", "--output", help="把结果写入 JSON 文件")
    p_res.add_argument("-q", "--quiet", action="store_true", help="不打印每个节点的进度")
    _add_state_dir(p_res)
    p_res.set_defaults(func=cmd_resume)

    p_runs = sub.add_parser("runs", help="列出已持久化的 run")
    _add_state_dir(p_runs)
    p_runs.set_defaults(func=cmd_runs)

    p_val = sub.add_parser("validate", help="只做静态校验,不执行")
    p_val.add_argument("file", help="chain 的 YAML 文件")
    p_val.set_defaults(func=cmd_validate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
