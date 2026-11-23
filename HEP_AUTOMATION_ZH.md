# HEP 自动流程与论文缓存

完整运行入口是 `scripts/run_ams_hep_batch.sh`。一条命令依次准备论文、规划、生成任务、
独立审核、解题、数值评分、科学评审，并保存汇总报告。

```bash
bash scripts/run_ams_hep_batch.sh --limit 1
```

默认使用本机 `.env` 中配置的 `Sage-S2.1-Flash`。模型切换见 [模型配置](MODEL_SWITCHING_ZH.md)。

## 阶段与材料

| 阶段 | 工作与结果 |
| --- | --- |
| 论文准备 | 下载官方论文和附件，提取正文、图像、表格，记录来源与指纹 |
| 任务规划 | 检查论文身份，找出可能的分析方向和必要输入 |
| Builder（任务生成） | 主会话按科研步骤定题，DSH 原生子代理并行制题，逐题搜索文献并准备题面、公开数据与两层评分清单 |
| Reviewer（任务审核） | 逐题独立核对科学内容和数据；通过后交付公开任务 |
| Solver（解题） | 只使用公开任务，保存实际分析代码、数值结果、图表和报告 |
| 数值评分 | 按隐藏参考数值和预先声明的容差检查提交 |
| Judge（科学评审） | 逐项判断方法、误差处理和解释，程序汇总小项、大项及总分 |
| 汇总 | 把各阶段状态与评分写入 `batch_report.json` |

Reviewer 检查论文身份、数据来源、单位与分箱、不确定度、可评估性、公开与隐藏材料的
分离、科学合理性。审核报告保存每项检查使用的文件证据。

Builder 可以使用 Serper 搜索、Jina 网页读取和 MinerU 论文解析。Reviewer、Solver、
Judge 使用各自工作区已提供的资料，不启用上述外部资料工具。

## 一篇论文生成多道任务

先还原论文的研究动机、关键决策和支撑结论的证据，再选出值得交给答题者的决策。
可从问题界定、假设、方法设计、测量、比较、推断和稳健性检验中选题。
通常选 2–4 道，题型和数量随论文与材料决定。同一种计算仅换粒子或时间段时，
合并成一题。每题都能用自己随附的背景、文献和所需数据独立解答。
每题在 `decision_checks` 中列出直接检查核心判断的数值或科学评审项，程序核对这些名称。
交接清单同时记录答题者可见的起始材料、要留下的决策和私有评分依据。程序为每道题开一个 DSH 子会话，每个子会话在自己的题目目录中工作。
官方表格若在每个观测窗口重复同一套分箱，可以在数据说明里用 `group_by`（分组列）指明窗口列。
程序只在每个窗口内部检查分箱顺序和重叠，保留原始表格。
只有一道题能成立时，在 `single_task_reason` 写清其他候选为什么无法独立作答和评分。
显式指定单题计划时，继续只生成指定题。物理选择规则见
[科研拆题指南](sources/ResearchFactory/researchfactory/taskgen/tasks/researchclawbench/hep_research_playbook.md)。

先试一篇，仅运行到生成阶段：

```bash
bash scripts/run_ams_hep_batch.sh --limit 1 --through build --output-root runs/ams02-series-check
```

在该论文目录中用 `uv run -m researchfactory.taskgen.tasks.researchclawbench.validate_package --series`
复查整组。运行同一命令并把 `--through build` 改成 `--through review`，可接着逐题审核。
主会话调用 `build_task_series`（生成并交付整组题）。程序等所有子会话结束，检查整组文件，再逐题导出。日常复查用 `--series`。

## 论文提前准备

准备 AMS 清单中的论文与官方附件：

```bash
bash scripts/prepare_ams_papers.sh --workers 2
```

主论文默认使用 MinerU 云解析，已有结果直接复用。先检查一篇论文的缓存：

```bash
bash scripts/run_ams_hep_batch.sh \
  --prepare-only --limit 1 \
  --output-root runs/ams02-mineru-cache-pilot
```

`--prepare-only` 只处理资料。`--pdf-backend pymupdf` 可将主论文切换为 PyMuPDF（本地
PDF 库）解析。补充 PDF 使用 PyMuPDF，官方 CSV、ROOT 和补充 PDF 均复用已有缓存。

缓存保存原始文件、正文、公式、图像、候选表格、官方 CSV 预览和来源信息。
PDF 内容、解析器版本和参数共同决定缓存版本。复用前检查文件指纹；相同内容不会重复
解析。MinerU 作业中断后可以继续查询同一个已提交作业。

PyMuPDF 与 MinerU 的正文结果分别保存。补充 PDF 超过 64 页时，保存全文、逐页行号
索引和首尾页面预览。数值任务优先读取官方 CSV 或 ROOT，提取的表格由 Reviewer 核对。

## 结果位置

默认结果位于 `runs/ams02-hep-taskgen/<配置>/<模型>/`。

| 文件或目录 | 内容 |
| --- | --- |
| `prepared_pool.jsonl` | 带缓存引用的论文清单 |
| `hep_batch_plan.jsonl` | 物理任务规划 |
| `workflow_plan.json` | 本轮阶段与目录 |
| `workflow_state.json` | 当前阶段、状态和退出码 |
| `<论文目录>/task_series.json` | 私有任务清单，含科研阶段、问题、数据证据、核心计算、评分项和未覆盖步骤 |
| `<论文目录>/task_series.delegation.json` | 主会话与各题子会话的编号、开始和结束时间 |
| `<论文目录>/tasks/<题目名>/task/` | 单题材料，包含公开输入和隐藏评分文件 |
| `<论文目录>/tasks/<题目名>/public/` | 生成校验通过后导出的单题公开材料 |
| `<论文目录>/_meta.json` | 整篇生成状态和已交付的任务清单 |
| `reviewed/<论文目录>--<题目名>/` | 独立审核后的任务及报告 |
| `solver/` | 解题代码与输出 |
| `scores/` | 数值评分报告 |
| `judge/` | 科学评审与调用轨迹 |
| `batch_report.json` | 汇总报告，`paper_count` 是论文数，`count` 是任务记录数 |

只运行到生成或审核时也会保存汇总。尚未执行的解题、评分和科学评审字段留空。
公开任务包含 `task_info.json`、`data/` 和 `related_work/`。每道新题的 `related_work/` 随附公开 PDF 和 `references.json`，登记搜索词、来源及每篇论文的用途。
评分清单的每个小项都有自己的权重，`rubric_score` 是完整得分，取值为 0 到 1。`deterministic_score` 单独显示程序检查的得分。
目标论文、参考图像、隐藏数值和评分代码保留在私有材料区。

新版评分清单可以给小项标注能力。一道题可以同时考多项。规划、假设和工具操作等
过程评分点可以引用带编号的解题事件，Judge 在评分里记录证据编号。已有题可以经过
独立审核补充这些小项。定向问答或规划题可以评分科学答卷，无须凑数值题。

`batch_report.json` 按八项能力记录生成、审核和评分覆盖，以及有得分的题的平均分。
传入上一轮报告时，会依据缺口给新一轮论文分配抽取重点，并把两轮覆盖合并。

```bash
bash scripts/run_ams_hep_batch.sh --limit 1 --coverage-report /path/to/batch_report.json
```

## 运行范围与恢复

| 参数 | 含义 |
| --- | --- |
| `--limit 1` | 先处理第一篇论文 |
| `--pool 路径` | 指定其他物理论文清单 |
| `--model 模型名` | 使用同一个服务和密钥，临时换用指定模型 |
| `--jobs 1` | 每次生成一篇论文，每次审核一道题 |
| `--cache-workers 2` | 资料处理使用两个独立进程 |
| `--through build` | 执行到任务生成 |
| `--through review` | 执行到任务审核 |
| `--output-root 路径` | 为本轮实验指定独立目录 |
| `--dry-run` | 整理真实材料与请求，不调用模型 |
| `--show-config` | 显示当前模型配置 |

`--build-timeout`、`--review-timeout`、`--solver-timeout`、`--judge-timeout`
分别设置各代理阶段的最长秒数。

相同命令再次运行时，复用输入和产物都未变化的已完成阶段。审核超时后，重复相同命令
会接续已保存的原生会话。运行错误中止流程，并保留错误阶段和日志。
数值低分和科学拒绝作为评估结果如实保存。

批量实验前，先检查单篇的公开输入、审核记录、解题结果和评分。

从项目根目录运行 `uv run python -m pytest sources/ResearchFactory/tests -m integration --hep-run-dir <单篇试跑结果目录>`，
可用实际产物复查审核与评分。未指定结果目录时，只跳过依赖试跑产物的测试。
