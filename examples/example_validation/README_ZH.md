# 共享示例证据约定

[English](README.md)

这是示例验证记录的小型只读发布封装，沿用
[AutomationBench 证据规则](../automationbench/docs/EVIDENCE_ZH.md)中的精确字节哈希、
私有原始证据与分发副本区分、追加式历史以及独立评分边界。它不替代既有示例的
schema，不启动任务、调用模型、写 Registry、应用补丁或评分。Registry 和原始
评分器仍是权威来源。辅助程序仅使用 Python 标准库。

## 文件与回传内容

- `result-manifest.schema.json`：严格的 draft-07 结构，版本为
  `rpnh/example-evidence/v1`。
- `template.json`：尚未完成的 ERP2299 模板，**不是执行结果或成功证明**。
- `validate.py`：只读检查结构、一致性、字节哈希和安全路径；向标准输出写
  `rpnh/example-merge-summary/v1` JSON。
- `tests/test_validation.py`：仅含离线合成测试。

每次验证在白名单公开文件旁生成一个**新的** `result-manifest.json`，使用唯一记录
ID。逻辑 condition 必须固定任务范围、检查点揭示与反馈规则、seed/reset、允许的
操作面、评分器和预算语义。按精确文件字节计算哈希，不对 JSON 重排后再计算。
任务指令和输入清单也必须有精确哈希，输入清单继续列出不可变输入文件的哈希。
缺失身份保留 `null` 并说明原因。公开模型配置应单独审查，记录精确 provider/model
ID、参数和预算；不能拿私有启动配置的哈希冒充公开配置。不得包含密钥、令牌、
账号、私有端点或本地绝对路径。

`source` 记录共同 RPNH 基线和实际受测提交；未提交 overlay 的 `tested_commit`
为 `null`，由逐文件最终哈希识别，不虚构提交。列出所有自有范围内的变更文件，
记录基线哈希（仅新增文件为 `null`）、最终哈希和相对路径。本版约定不支持删除。
先固定不可变源快照再计算哈希。辅助程序只核验列出的文件，集成者还需检查完整
diff/清单、拒绝漏报变更，并保留无关文件。

为每个验证包明确指定源码路径白名单。在约定的干净基线上检查完整 diff，
保留无关文件。

## 各阶段的含义

五阶段独立记录：`offline` 纯契约检查；`mock` 假/脚本模型接口检查；`native`
真实安装的 owner/native/service 集成；`provider` 真实外部模型执行；`evaluation`
原始或补充评分器完成。`passed` 表示该阶段命令完成了预定检查。
**评分阶段 passed 不代表业务任务成功**，失败分数和原始各分项仍须保留。
公开命令应可复现且不含秘密；精确私有命令日志可以私下保留，同时公开参数化命令。

passed/failed 都需保留证据；blocked/not_run/unknown 分开解释。不能用假模型或
替换传输的测试冒充 native 验收。模型调用数未知时为 `null`，零也必须有覆盖所述
范围的观测证据，真实与假 provider 分别计数。本工具检查证据引用，不凭标签推导
调用数或执行事实。

原始成绩保留评分器身份、原始分项名和数值以及不可变的评分输出。
`benchmark_result` 要求真实模型、原始任务语义、native 验收、实际 Registry 证据
及固定的条件/输入身份。假模型只能记录 `grader_compatibility`；新增检查使用
`supplementary`，二者均非正式模型成绩。若原始协议给受阻执行记零，保留原始记录，
但不得将其变成已完成的 RPNH benchmark result。私有或结构化评分细节保留在证据
文件；标量分项只是精确投影，不另行聚合。不能跨条件混合分数；SCB 1→2→3 前缀
不代表完成全部五个检查点；ERP2000 单独作为冒烟记录，不能算作 ERP2299 成功。

## 文件、Registry 与历史

公开文件使用安全相对路径、字节数和**实际分发字节**的 SHA-256，脱敏和换行变化
也算字节变化。私有证据不在封装中暴露路径，仅保留摘要、大小、角色和
`retained_private_bytes` 范围。原始模型对话与 Registry 数据库保持私有。
引用的文件必须是保留的不可变快照，不使用持续变化的 `latest` 文件。给定根目录
时工具能检查完整性，但不能保证文件系统不可变，也无法验证不可访问的私有原件。

Registry `ref` 原样复制：VersionRef 为 `entity_type`、`logical_id`、`version_id`；
ResourceVersionRef 为 `resource_id`、`resource_version_id`。保留所属 `task_id`。
实际 TaskControl 或 result-evidence 的脱敏投影必须包含相同任务和精确引用，
工具会重新解析核对。删除私有路径/内容时不改身份。没有 Registry 输出则为
`unavailable`，不得虚构 ID。本约定不注册值、不创造权威，也不跨机器拼接活跃
Registry。领域 lineage/reuse 仍放在示例自己的证据文件中；区分代码修改、流程
定义修订、定义复用与有效的数值结果复用。

每次尝试、条件变更、补救和重新评分都创建新记录，使用
`history.previous_record_artifacts` 按哈希引用早期记录。保留失败、受阻尝试和
未修改的原始分数；纠正不能覆盖历史。在合并版本上的检查是新证据，旧结果仍保留
原来的受测身份。

发现问题时分到上游任务/评分器、绑定/配置、harness 实现、模型推理或环境/未知。
另标注确定程度：`hypothesis`（假设）、`observed`（有观测但原因未定）或 `confirmed`
（已确认）。确认模型/harness 归因前必须排除配置问题，并提供具体证据和复现。
未确认的源码集成缺口或设计疑问仍可报告，无需虚构确定性；分类也未定时使用未知。
记录预期、观测、影响、下一步和建议负责人。源码假设或人工参考数字不算实际
实验失败或性能增益。

## 检查、审查与合并

在仓库根目录提供验证包、最终源码快照和干净基线。`EXPECTED_BASE` 是该验证包
约定的精确基线提交；下面的白名单示例选择 ERP 文件：

```sh
python examples/example_validation/validate.py RETURN/result-manifest.json \
  --artifacts-root RETURN --source-root FINAL --base-root BASE \
  --expected-base "$EXPECTED_BASE" \
  --allow-prefix examples/erp_bench/ > merge-summary.json
python -m unittest discover -s examples/example_validation/tests -v
```

验证该示例时使用 `examples/slopcodebench/`。省略的核验选项会明确标为未验证。
模板可通过结构检查，但所有运行阶段仍未执行。`merge_review_ready` 仅表示已提供
要求的基线/路径/哈希检查、声明的公开文件审查和离线通过结果，**不等于合并批准、
真实运行验收或发布授权**。还需独立核对实际 Git 基线及完整 diff。privacy 设为
passed 前检查每个公开文件和自由文本的秘密/私有路径；工具不是秘密扫描器，
审查者声明也不是自动扫描结果。

先合并互不重叠的源码，再对组合树运行相关测试，并在实际最终修订上完成受影响的
owner/评分器验收。不得给旧成绩改贴新身份。即使源码准备完成，native/provider/
evaluation 阻塞仍保持开放。纯检查不涉及真实模型、安装、大下载、Actions 或推送。
