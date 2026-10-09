# RPNH v5 本地回传

## 接收与环境
- UTC窗口/run ID：
- 本完整包ZIP名称/bytes/SHA-256：
- 本包verify命令/exit/status：
- checkout origin身份、HEAD、branch、本地origin/main：
- diff/untracked与是否隔离保存：
- baseline/product精确commit、缺文件/fixture：
- Python/module来源、版本；Node/pnpm；binary路径/version/hash/可信来源：
- native/fixture/权限边界；真实model calls必须0：

## 每个stage（重复本节，禁止跨版本相加）
- lane/stage/input stage/run ID：
- 候选ZIP bytes/hash与唯一根patch hash：
- exact input/source/manifest身份及完整before/after清单路径：
- 实际应用命令/exit与original verifier结果：
- 测试命令/cwd/环境变量（去秘密）/UTC开始结束：
- stdout/stderr/JUnit/真实process exit路径：
- 独立node IDs、重跑/overlap/deselected/skip清单：
- PASS/FAIL/NOT_RUN/BLOCKED/UNIMPLEMENTED/INCONCLUSIVE，明确scope：
- dispatch/Popen/worker/child/model计数、exact refs：
- native transport/stock版本/应用接线、owner/stop/unknown/reader只读/zero-touch：
- 保留的历史失败与新失败原因；不得覆盖旧窗口：
- 缺环境、缺实现、下一步分别说明：

## 组合（未做即NOT_RUN）
- 选入候选清单/精确版本：
- 新组合身份/full inventory/新gate/独审：
- 实际回归node IDs/native范围及与各stage差异：
- 组合自己的命令/exit/日志/verdict：
- 未选项和未覆盖范围：

## 交付清单
- 附manifest、最小diff、必要文本日志、JUnit、身份与计数
- 无真实运行数据库/rawdata/credential/API key/用户私有配置/venv/node_modules/binary
- 不用stage PASS推导组合PASS；不把包交付、合入、native认证、默认升级混为一项
