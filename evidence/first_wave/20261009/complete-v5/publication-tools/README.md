# 有界证据发布辅助器（尚未执行）

只用 Python 标准库，发布器只向固定目录
`RPNH-main/evidence/first_wave/20261009/complete-v5/` 写入新的证据文件。
不调用 Git、网络、模型、子进程、安装、测试或补丁应用。脚本导入不读取输入、不写缓存；
两入口均要求 `--run`。当前交付不创建目标目录、不运行发布器。

父任务结束所有证据写入、接受本实现并完成内容/隐私复核后，在任务根准备最终 JSON
（下列路径仅说明字段，必须替换为实际已接受文件；没有预填任何测试结论）：

```json
{
  "final": true,
  "publication_approved": true,
  "privacy_review_complete": true,
  "evidence_only": true,
  "results_summary": "RESULTS_ZH.md",
  "result_manifest": "result-manifest.json",
  "final_control_json": ["result-manifest.json"],
  "final_accepted_junit": ["stages/<lane>/<actual-final-accepted-junit>.xml"]
}
```

所有字段中的文件路径相对 `task-complete-v5-20261009/`；最终摘要文件名可以待定，
发布时分别映射到稳定公共入口 `RESULTS_ZH.md` 与 `result-manifest.json`。
`final_control_json` 和 `final_accepted_junit` 均须非空且由父任务明确指定，后者须是
已在正向选择范围内的实际 JUnit 文件；不按 `final` 文件名或日志文本猜测通过状态。
这里的 accepted 指父任务已审阅并接受发布证据，允许实际 FAIL（包括 native g7）；
验证只要求 JUnit 可解析且根元素正确，不要求 PASS，不改写失败状态。
四个布尔字段是父任务的批准/复核声明，正则检查不能代替复核。

父任务接受后才能执行（本轮未执行）：

```bash
python -B <WORKSPACE>/task-complete-v5-20261009/publication/publish_reviewed.py --gate-summary <WORKSPACE>/task-complete-v5-20261009/FINAL_GATE_SUMMARY.json --run
python -B <WORKSPACE>/task-complete-v5-20261009/publication/validate_reviewed.py --run
```

选择边界：

- 收到的 bundle 根文本、`tools/`、`plan-v5/`、`templates/`、`evidence/`；保留档案锁、
  SHA、大小和成员元数据，但不打开/上传档案。
- 恰好 12 个候选，由 `CANDIDATE_INDEX.json` 定位唯一解包根和选中根补丁；验证补丁 SHA
  及每个选中 received 成员的档案锁 SHA。包括根说明/清单/身份和正向列出的工具、审阅、
  原生门禁、报告、证据文本；保留原失败、空文件、畸形 XML/JSON、UTF-8 NUL 字节。
  文本后缀包括 `.mjs/.ts/.sha256/.exitcode`，保留实际 detached factory 审阅探针及身份清单。
- 本地任务根 `.py/.json/.jsonl/.md`、`logs/`、`reviews/`，五个阶段的顶层记录，以及明确
  列出的证据/原生/审阅子目录。包含实际日志、JUnit、身份/计数、review/probe/readback JSON。
  正向目录/后缀列表写在脚本常量中；未知目录和非文本文件记入排除清单，不递归猜测。
  局部目录例外仅限 `stages/core/S1/native/` 下的 `a001/a002/a003/a004/control001`，以及
  `stages/core/H7_history/review-resume-exact/`，用于保留失败探针原件和最终 JUnit；
  不把这些名字推广到其他目录，也不开放任意 `aNNN` 或 `control*`。
  `control001/control.xml` 可由父任务在 `final_accepted_junit` 中明确指定。
- 排除 `source/`、`baseline/`、`inputs/`、上游/历史源码快照、所有 `package*` 工作副本、
  `worktree-independent`、缓存/编译文件、数据库/WAL/SHM、私有配置及二进制。
  不复制任何候选源码路径（每阶段 0 份，满足最多 1 份）；选中补丁已携带改动。
  证据/审阅目录内历史补丁标为 `historical_patch_not_application_target`，不作为选中补丁。
  唯一显式快照目录例外是 `H7_core/evidence/source-v1/H7-core.patch`：仅保留此 V5 引用的
  历史失败版本补丁，并核对收到的档案锁 SHA；不遍历或复制该目录的其他源码。
  根目录 `.patch` 仍不通过通用 walk 选择，只有索引指定的 12 个根补丁逐个显式加入。
  `PRIVATE_NAME` 仅匹配 private-profile 等私有名称，不因单独的 profile 一词排除
  `test_profile_independent.py` 或公开 profile 定义/探针；其他正向选择边界仍适用。
  `auth` 名称的公开 `.py/.sh/.mjs/.ts` 测试/工具源码不因名字视为凭据；仍扫描内容并由父任务
  复核。`auth.json` 等数据、明确的 private-profile/credential/secret 文件保持排除。

默认累计预算 **64 MiB**，包括伴随 JSON、公共 README 和清单。先打印选择数量/分类字节，
超预算则在任何输出写入之前停止，绝不为凑预算静默删减失败证据。
`--budget-bytes` 仅供父任务审核实际选择后显式调整。需要新增目录时由父任务审核正向列表，
不能借提高预算纳入整个仓库。最终文件必须停止变化；准备末尾及验证时重新核对本地原件。

仅允许三种私有前缀替换，最长匹配优先：`<INPUT_DIRECTORY>` →
`<INPUT_DIRECTORY>`，`<WORKSPACE>` → `<WORKSPACE>`，`<USER_HOME>` → `<USER_HOME>`。
选中 received 根补丁及 received 根 JSON 控制文件冻结；若其中出现需替换前缀则停止交父任务
处理。其他文本保留原字节，除这三种记录在案的替换外不改写。原可解析 XML 替换用 XML
转义；畸形 XML 使用普通字节替换，保留其畸形结构，不伪装修复。NUL 或畸形 XML/JSON
同时保留 raw payload 与可转义 JSON 视图，伴随文件明确不是原件替代。
非 UTF-8 记录停止交父任务判断，不悄悄丢弃。实际密钥/私有端点或正则命中只报告本地精确
文件路径与类别，不输出匹配内容；检查失败时不产生部分脱敏发布包。

清单记录每个原件/包含件 SHA256、大小、完整字节/行范围、workspace 相对来源路径及逐次
替换的原/新字节范围和原行号。空文件行范围为 `[]`；非空行范围以 LF 计数加一（末尾 LF
包含终止空行）。生成文件记录生成器、来源 raw 路径及包含件 SHA/范围；清单本身不自哈希。
验证器从本地来源重建选择和每次替换、比较实际字节及成员集合/预算，检查最终控制 JSON 和
父任务明确接受的 JUnit；历史畸形原件须与显式清单一致，不要求全部旧 XML 可解析。

所有读写路径检查 workspace 边界和各级符号链接；输出必须新建且不覆盖已有路径，不写硬链接。
目标存在即停止；中断后由父任务审阅残留目录，本工具不自动删除或覆盖。清单最后写入，随后
只读验证；若写入/末尾验证失败，残留不代表接受。公共 README 使用相对 GitHub 链接指向结果、
manifest 和各阶段失败证据，不编造总数/状态。归档内历史脚本及本地命令只记录来源，需要
本地路径配置，不宣称可直接移植。无 return ZIP。
