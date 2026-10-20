import textwrap

from workchain.engine import Engine
from workchain.spec import load_chain


def _write(tmp_path, text):
    p = tmp_path / "chain.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return str(p)


def test_shell_chain_runs(tmp_path):
    f = _write(tmp_path, """
        name: run
        params: {msg: hello}
        nodes:
          - {name: a, type: shell, command: "echo {{ params.msg }}", outputs: [stdout]}
          - {name: b, type: shell, needs: [a], command: "echo {{ nodes.a.outputs.stdout }}-world", outputs: [stdout]}
    """)
    report = Engine(load_chain(f)).run()
    assert report.ok
    assert report.results["a"].outputs["stdout"] == "hello"
    assert report.results["b"].outputs["stdout"] == "hello-world"


def test_parallel_fanout(tmp_path):
    f = _write(tmp_path, """
        name: fan
        max_workers: 3
        params: {x: "1"}
        nodes:
          - {name: root, type: shell, command: "echo {{ params.x }}", outputs: [stdout]}
          - {name: l, type: shell, needs: [root], command: "echo L{{ nodes.root.outputs.stdout }}", outputs: [stdout]}
          - {name: r, type: shell, needs: [root], command: "echo R{{ nodes.root.outputs.stdout }}", outputs: [stdout]}
    """)
    report = Engine(load_chain(f)).run()
    assert report.ok
    assert report.results["l"].outputs["stdout"] == "L1"
    assert report.results["r"].outputs["stdout"] == "R1"


def test_failure_blocks_downstream(tmp_path):
    f = _write(tmp_path, """
        name: fail
        nodes:
          - {name: a, type: shell, command: "exit 3", outputs: [stdout]}
          - {name: b, type: shell, needs: [a], command: "echo ok", outputs: [stdout]}
    """)
    report = Engine(load_chain(f)).run()
    assert not report.ok
    assert "a" in report.failed
    assert "b" in report.blocked


def test_retry_then_succeed(tmp_path):
    marker = tmp_path / "count"
    f = _write(tmp_path, f"""
        name: retry
        nodes:
          - name: flaky
            type: shell
            retries: 2
            command: "c=$(cat {marker} 2>/dev/null || echo 0); c=$((c+1)); echo $c > {marker}; test $c -ge 2"
            outputs: [stdout]
    """)
    report = Engine(load_chain(f)).run()
    assert report.ok
    assert report.results["flaky"].attempts == 2
