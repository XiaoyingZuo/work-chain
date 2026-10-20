from workchain import dag
from workchain.dag import CycleError

import pytest


def test_topological_order_linear():
    names = ["a", "b", "c"]
    needs = {"a": [], "b": ["a"], "c": ["b"]}
    assert dag.topological_order(names, needs) == ["a", "b", "c"]


def test_topological_order_diamond():
    names = ["a", "b", "c", "d"]
    needs = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}
    order = dag.topological_order(names, needs)
    assert order.index("a") < order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_cycle_detected():
    names = ["a", "b"]
    needs = {"a": ["b"], "b": ["a"]}
    with pytest.raises(CycleError):
        dag.topological_order(names, needs)


def test_ready_nodes():
    names = ["a", "b", "c"]
    needs = {"a": [], "b": ["a"], "c": ["a"]}
    ready = dag.ready_nodes(names, needs, succeeded=set(), finished=set())
    assert ready == ["a"]
    ready = dag.ready_nodes(names, needs, succeeded={"a"}, finished={"a"})
    assert set(ready) == {"b", "c"}


def test_blocked_transitive():
    names = ["a", "b", "c"]
    needs = {"a": [], "b": ["a"], "c": ["b"]}
    blocked = dag.blocked_nodes(names, needs, failed={"a"})
    assert blocked == {"b", "c"}
