import textwrap

import pytest

from workchain import dag
from workchain.engine import Engine, NodeResult
from workchain.spec import load_chain
from workchain.store import RunStore, new_state, node_result_to_dict
from workchain import resume as R


def _chain(tmp_path, text):
    p = tmp_path / "chain.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return load_chain(str(p))


LINEAR = """
    name: lin
    params: {m: hi}
    nodes:
      - {name: a, type: shell, command: "echo {{ params.m }}", outputs: [stdout]}
      - {name: b, type: shell, needs: [a], command: "echo {{ nodes.a.outputs.stdout }}B", outputs: [stdout]}
      - {name: c, type: shell, needs: [b], command: "echo {{ nodes.b.outputs.stdout }}C", outputs: [stdout]}
"""


def test_descendants():
    names = ["a", "b", "c", "d"]
    needs = {"a": [], "b": ["a"], "c": ["b"], "d": ["a"]}
    assert dag.descendants(names, needs, ["b"]) == {"c"}
    assert dag.descendants(names, needs, ["a"]) == {"b", "c", "d"}


def test_store_roundtrip(tmp_path):
    store = RunStore(str(tmp_path / "runs"))
    rid = store.new_run_id("lin")
    state = new_state(rid, "lin", "x.yaml", {"m": "hi"})
    state["nodes"]["a"] = node_result_to_dict(
        NodeResult(name="a", status="succeeded", outputs={"stdout": "hi"}, attempts=1)
    )
    store.save(state)
    assert store.exists(rid)
    assert store.latest() == rid
    loaded = store.load(rid)
    assert loaded["nodes"]["a"]["outputs"]["stdout"] == "hi"
    assert store.list_runs()[0]["succeeded"] == 1


def test_cached_skips_execution(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    cached = {"a": NodeResult(name="a", status="cached", outputs={"stdout": "hi"})}
    report = Engine(chain).run(cached=cached)
    assert report.ok
    assert "a" in report.cached
    assert set(report.ran) == {"b", "c"}
    assert report.results["b"].outputs["stdout"] == "hiB"


def test_runnable_restriction(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    cached = {
        "a": NodeResult(name="a", status="cached", outputs={"stdout": "hi"}),
        "c": NodeResult(name="c", status="cached", outputs={"stdout": "old"}),
    }
    report = Engine(chain).run(cached=cached, runnable={"b"})
    assert report.ran == ["b"]          # 只有 b 跑
    assert set(report.cached) == {"a", "c"}
    assert report.skipped == []         # c 已 cached,不算 skipped


def test_checkpoint_called(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    seen = []
    Engine(chain).run(checkpoint=lambda n, r: seen.append(n))
    assert set(seen) == {"a", "b", "c"}


def _state_with(succeeded):
    nodes = {n: {"status": "succeeded", "outputs": {"stdout": n}} for n in succeeded}
    return {"nodes": nodes}


def test_plan_resume(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    cached, runnable = R.plan_resume(chain, _state_with(["a"]))
    assert set(cached) == {"a"}
    assert runnable is None


def test_plan_from_invalidates_descendants(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    cached, runnable = R.plan_from(chain, _state_with(["a", "b", "c"]), "b")
    assert set(cached) == {"a"}         # b, c 被失效
    assert runnable is None


def test_plan_only_ok(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    cached, runnable = R.plan_only(chain, _state_with(["a", "b", "c"]), "b")
    assert runnable == {"b"}
    assert set(cached) == {"a", "c"}    # b 本身要重跑,a/c 复用


def test_plan_only_requires_upstream(tmp_path):
    chain = _chain(tmp_path, LINEAR)
    with pytest.raises(ValueError, match="上游"):
        R.plan_only(chain, _state_with(["a"]), "c")  # b 没成功
