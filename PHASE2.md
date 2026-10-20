# work-chain 二期规划

一期交付了单机、YAML 定义、三类执行器、并发执行、静态断链校验的内核。二期是把它从"本地脚本编排"推向"可靠的工作流服务"。下面按优先级分组,并标注与一期的衔接点。

## 背景:一期有意砍掉的东西

一期为了快速跑通,刻意不做下列能力——不是不需要,而是归到二期:

- 没有持久化:进程退出,运行状态即丢失,不能续跑。
- 没有服务化:只能命令行单次执行,无 API、无多 worker。
- 失败即跳过下游,没有断点续跑 / 从失败节点重跑。
- Python 节点的 `timeout` 无法真正中断(线程不可强杀);目前仅 shell/llm 的 timeout 生效。
- 无成本/耗时的结构化观测(只有每节点 duration)。

---

## P0:可靠性与可恢复(最该先做)

### 1. 运行状态持久化 + 断点续跑
把每次 run 的节点状态、outputs、attempts 落盘(SQLite 或 JSON 状态文件)。
- 新增 `workchain resume <run_id>`:只跑未完成 / 失败的节点,已成功节点直接复用其 outputs。
- 衔接点:`engine.RunReport` 已经是完整状态快照,持久化它即可;`_build_context` 读 outputs_store 改为可从磁盘回填。

### 2. 从失败节点重跑 / 单节点重跑
`workchain run --from <node>` / `--only <node>`,配合持久化的上游 outputs。
解决一期"改了下游一个节点,却要把整条链从头跑一遍(尤其 LLM 很贵)"的痛点。

### 3. Python 节点的真正超时与取消
一期用线程池,无法强杀。二期可选:
- 把 python 节点放进 `ProcessPoolExecutor`,用进程超时 + kill;
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
  → P0 持久化 + 断点续跑 + 单节点重跑   ← 投入产出比最高,立刻能用
  → P1 成本/耗时观测                    ← LLM 场景几乎必做
  → P2 条件分支                         ← 按实际编排需求再加
  → P3 服务化                           ← 仅当需要多人/多机协作时
```
