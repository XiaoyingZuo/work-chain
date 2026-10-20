# work-chain 二期规划

一期交付了单机、YAML 定义、三类执行器、并发执行、静态断链校验的内核。二期是把它从"本地脚本编排"推向"可靠的工作流服务"。下面按优先级分组,并标注与一期的衔接点。

## 背景:一期有意砍掉的东西

一期为了快速跑通,刻意不做下列能力——不是不需要,而是归到二期(部分已在 P0 补齐,见下):

- ~~没有持久化:进程退出,运行状态即丢失,不能续跑。~~ → P0#1 已补齐
- 没有服务化:只能命令行单次执行,无 API、无多 worker。
- ~~失败即跳过下游,没有断点续跑 / 从失败节点重跑。~~ → P0#1/#2 已补齐
- Python 节点的 `timeout` 无法真正中断(线程不可强杀);目前仅 shell/llm 的 timeout 生效。(P0#3,未完成)
- 无成本/耗时的结构化观测(只有每节点 duration)。

---

## P0:可靠性与可恢复(最该先做)

### 1. 运行状态持久化 + 断点续跑 ✅ 已完成
- 每次 `run` 落盘到 `.workchain/runs/<run_id>.json`(JSON 状态文件,原子写),每个节点完成即 checkpoint,崩溃可续。
- `workchain resume [run_id]`:复用已成功节点的 outputs,只补跑失败/未跑/被阻塞的;默认续最近一次。
- `workchain runs` 列出历史 run。
- 实现:`store.py`(RunStore)、`engine.run(cached=, checkpoint=)`、`resume.py`。

### 2. 从失败节点重跑 / 单节点重跑 ✅ 已完成
- `workchain resume --from <node>`:该节点及其所有后代强制重跑,其余复用。
- `workchain resume --only <node>`:只重跑该节点(校验其上游已成功),其余不动。
- 实现:`dag.descendants`、`engine.run(runnable=)`、`resume.plan_from/plan_only`。

### 3. Python 节点的真正超时与取消  ⬜ 未完成(P0 剩余项)
一期用线程池,无法强杀,目前仅 shell/llm 的 `timeout` 生效,python 节点的 `timeout` 不起作用。可选方案:
- 把 python 节点放进 `ProcessPoolExecutor`,用进程超时 + kill;因 entry 是 `module:func`/`file.py:func`,子进程里重新 import 即可,无需 pickle 函数本身,但 args/返回值需可 JSON 序列化(当前输出契约已要求 JSON 友好)。
- 或提供 async 执行器,节点协程可被 cancel。
需权衡:进程池带来序列化开销和跨进程 import 约束。

---

## P1:观测与成本(LLM 工作流刚需)

### 4. 每节点 token / 成本 / 耗时埋点
LLM 执行器已经拿到 API 响应,把 `usage`(prompt/completion tokens)记进 NodeResult;按模型价表折算成本;run 结束给出成本汇总(哪个节点最贵)。
> 这正是我们拆解 Octo-Workflow 时发现它缺的东西,别重蹈覆辙。

### 5. 结构化事件日志 + 可视化
把 `EventHook` 的事件流写成结构化日志(JSONL),支持事后用 `workchain inspect <run_id>` 还原时间线;可选输出 DAG 执行甘特图 / mermaid。

---

## P2:编排能力增强

### 6. 条件分支与跳过
节点加 `when: "{{ nodes.x.outputs.flag }}"` 条件,为假则跳过(标记 skipped 而非 failed,下游照常可依赖其默认值)。

### 7. fan-out / map(动态并行)
一个节点产出列表,下游对列表每个元素并行实例化(类似 map)。需要引入"动态节点"概念,是对当前静态 DAG 的较大扩展。

### 8. 更强的模板与数据契约
- 一期是极简 `{{ a.b.c }}`,二期可评估是否需要默认值、简单表达式(谨慎,别退化成任意代码执行)。
- 给节点 outputs 加**类型/JSON Schema**,不止校验键存在,还校验值的结构。

---

## P3:服务化(走向 Octo 那种形态)

> 这一步本质上是重做我们拆解过的 Octo-Workflow 控制面。只有当"多人/多机共享工作流"成为真需求时才值得做。

### 9. 控制面 API + 拉取式 worker
- 编排器存状态、算 ready 集合;worker 通过 `claim → attempt → complete` 拉取执行。
- 必须补上 Octo 疑似缺失的两点(我们拆解时发现的坑):
  - **租约超时回收**:worker claim 后崩溃,节点要能自动回到 ready,别卡死。
  - **乐观并发(CAS)**:每个写操作带 `expected_updated_at`,防并发覆盖。

### 10. 模板 / 版本化 / 复用
workflow 模板 + 版本 + 实例化,对应 Octo 的 templates/gallery。属于锦上添花。

### 11. 认证与多租户
用户、API Key(**带 scope + 过期**,别学 Octo 的全权限长期 key)、workflow 可见性。
服务若暴露到网络,**必须上 TLS**(Octo 跑在明文 HTTP 上传凭证,是反面教材)。

---

## 建议路线

```
一期(已完成)
  → P0 持久化 + 断点续跑 + 单节点重跑   ✅ 已完成(剩 P0#3 python 硬超时)
  → P1 成本/耗时观测                    ← LLM 场景几乎必做,建议下一步
  → P2 条件分支                         ← 按实际编排需求再加
  → P3 服务化                           ← 仅当需要多人/多机协作时
```
