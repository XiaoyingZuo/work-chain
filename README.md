# work-chain

单机 Agent 工作流编排器。用一个 YAML 文件描述 DAG,节点可以跑 **LLM / Python 函数 / Shell 命令**,无依赖关系的节点**并发执行**,上游的输出通过模板 `{{ nodes.X.outputs.key }}` 流向下游。

这是一期(单机版)。二期(服务化、分布式、持久化等)规划见 [PHASE2.md](PHASE2.md)。

## 安装

```bash
cd work-chain
pip install -e .          # 仅依赖 PyYAML
```

## 快速开始

```bash
workchain validate examples/demo.yaml   # 只做静态校验,不执行
workchain run examples/demo.yaml         # 执行(自动持久化运行状态)
workchain run examples/demo.yaml -o result.json   # 结果写 JSON
```

也可以不安装直接 `python3 -m workchain.cli run examples/demo.yaml`。

## 断点续跑与部分重跑

每次 `run` 会把运行状态落盘到 `.workchain/runs/<run_id>.json`(每个节点完成即 checkpoint,崩溃也能续)。

```bash
workchain runs                      # 列出历史 run 及成功/失败统计
workchain resume                    # 续跑最近一次:复用已成功节点,只补跑失败/未跑/被阻塞的
workchain resume <run_id>           # 续跑指定 run
workchain resume --from b           # 从节点 b 起强制重跑(连同 b 的所有下游),其余复用
workchain resume --only b           # 只重跑节点 b(要求其上游已成功),其余不动
workchain run x.yaml --no-persist   # 本次不落盘(不可续跑)
workchain run x.yaml --state-dir D  # 自定义状态目录
```

典型场景:链跑到一半某个 LLM 节点失败 → 改好后 `workchain resume`,前面成功的节点直接复用 outputs,不重复花钱重算。改了某个中间节点的 prompt → `resume --from 该节点`,只重跑它和下游。


## YAML 结构

```yaml
name: my-chain
max_workers: 4            # 并发度
params:                   # 链级参数,用 {{ params.x }} 引用
  topic: "示例"
llm:                      # LLM 默认配置(OpenAI 兼容网关),可被节点覆盖
  base_url: ${WORKCHAIN_LLM_BASE_URL}
  api_key: ${WORKCHAIN_LLM_API_KEY}
  model: ${WORKCHAIN_LLM_MODEL}
nodes:
  - name: a
    type: shell           # llm | python | shell
    command: "echo {{ params.topic }}"
    outputs: [stdout]     # 声明输出键(用于静态断链校验 + 运行时契约检查)
  - name: b
    type: python
    needs: [a]            # 依赖,构成 DAG
    entry: "examples.helpers:to_upper"   # module:func 或 file.py:func
    args: {text: "{{ nodes.a.outputs.stdout }}"}
    outputs: [result]
    retries: 2            # 失败重试次数
    timeout: 60           # 秒(shell/llm 生效)
```

`${ENV}` 会从环境变量注入,别把密钥写进文件。

## 节点类型

| type | 关键字段 | 产出(outputs) |
|------|----------|----------------|
| `shell` | `command`,可选 `cwd` / `env` / `parse_json` | `stdout` `stderr` `returncode`;`parse_json=true` 时把 stdout 当 JSON 并入 |
| `python` | `entry`(`module:func` 或 `file.py:func`)、`args` | 函数返回的 dict(键即 outputs) |
| `llm` | `prompt`,可选 `system` / `model` / `temperature` / `json_output` | `text`;`json_output=true` 时并入解析出的键 |

## 数据流与校验

- 下游节点在任意字符串字段里用 `{{ nodes.<上游>.outputs.<键> }}` 引用上游输出,`{{ params.<键> }}` 引用链参数。
- **静态校验(加载期)**:DAG 无环、`needs` 指向存在节点、引用的上游必须在 `needs` 里且该输出键已声明、参数存在。断链问题在 `validate` 阶段就暴露,不用跑到一半才发现。
- **运行时契约**:节点实际产出必须包含它声明的所有 `outputs`,否则判失败。

## 执行语义

- 无依赖的节点并发执行(线程池,`max_workers`)。
- 节点失败且重试用尽 → 标记失败,其所有下游被**跳过(blocked)**。
- `workchain run` 退出码:全部成功=0,有失败/跳过=2,加载/校验错=1。

## 测试

```bash
pip install -e ".[dev]"
pytest -q
```
