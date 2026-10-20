import textwrap

import pytest

from workchain.spec import SpecError, load_chain


def _write(tmp_path, text):
    p = tmp_path / "chain.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def test_valid_chain(tmp_path):
    f = _write(tmp_path, """
        name: ok
        params: {topic: hi}
        nodes:
          - {name: a, type: shell, command: "echo {{ params.topic }}", outputs: [stdout]}
          - {name: b, type: shell, needs: [a], command: "echo {{ nodes.a.outputs.stdout }}", outputs: [stdout]}
    """)
    chain = load_chain(f)
    assert len(chain.nodes) == 2


def test_missing_param(tmp_path):
    f = _write(tmp_path, """
        name: bad
        nodes:
          - {name: a, type: shell, command: "echo {{ params.nope }}", outputs: [stdout]}
    """)
    with pytest.raises(SpecError, match="未定义的参数"):
        load_chain(f)


def test_ref_non_upstream(tmp_path):
    f = _write(tmp_path, """
        name: bad
        nodes:
          - {name: a, type: shell, command: "echo hi", outputs: [stdout]}
          - {name: b, type: shell, command: "echo {{ nodes.a.outputs.stdout }}", outputs: [stdout]}
    """)
    with pytest.raises(SpecError, match="非上游节点"):
        load_chain(f)


def test_ref_undeclared_output(tmp_path):
    f = _write(tmp_path, """
        name: bad
        nodes:
          - {name: a, type: shell, command: "echo hi", outputs: [stdout]}
          - {name: b, type: shell, needs: [a], command: "echo {{ nodes.a.outputs.ghost }}", outputs: [stdout]}
    """)
    with pytest.raises(SpecError, match="未声明的输出"):
        load_chain(f)


def test_cycle(tmp_path):
    f = _write(tmp_path, """
        name: bad
        nodes:
          - {name: a, type: shell, needs: [b], command: "echo", outputs: [stdout]}
          - {name: b, type: shell, needs: [a], command: "echo", outputs: [stdout]}
    """)
    with pytest.raises(SpecError, match="环"):
        load_chain(f)


def test_bad_type(tmp_path):
    f = _write(tmp_path, """
        name: bad
        nodes:
          - {name: a, type: magic, command: "echo"}
    """)
    with pytest.raises(SpecError, match="type"):
        load_chain(f)
