# HEP 任务生成与解题流水线

项目在本机运行，通过 API（服务接口）调用模型与资料服务。论文、工作区、分析代码、
图表和评分结果保存在本机。

```mermaid
flowchart LR
    A[论文与官方数据] --> B[预处理与缓存]
    B --> C[Builder 任务生成]
    C --> D[Reviewer 独立审核]
    D --> E[Solver 独立解题]
    E --> F[数值评分]
    F --> G[Judge 科学评审]
    G --> H[汇总报告]
```

## 安装并试跑一篇

需要联网、Bash、uv（Python 环境管理工具）和 Node.js 24.9.0 或更高版本（包含 npm）。
在 macOS 或 Linux 终端进入解压后的 `hep-research-pipeline` 目录。Windows 上请使用
WSL（适用于 Linux 的 Windows 子系统），并在 WSL 内安装依赖和运行命令。

**1. 安装依赖**

```bash
bash scripts/setup.sh
```

脚本会用 uv 准备 Python 3.10，并根据 `uv.lock` 和 `package-lock.json` 安装锁定版本。
如需指定 Node 路径，可在 `.env.runtime` 设置 `DEEPSEEK_HARNESS_NODE`。

**2. 填写自己的密钥**

如果解压后已有 `.env`，可直接进入第 3 步。仅当没有 `.env` 时，从模板创建：

```bash
cp .env.example .env
chmod 600 .env
```

编辑 `.env`，填入模型密钥和下方三个资料服务的密钥。默认模型为 `Sage-S2.1-Flash`；
如果使用其他兼容 OpenAI Chat Completions（聊天补全接口）的服务，同时改 `API_BASE`
和 `MODEL_NAME`。

**3. 生成任务**

```bash
bash scripts/run_ams_hep_batch.sh --limit 1 --through build
```

程序会优先使用随包的论文和解析缓存。缺少缓存时，它会下载论文与官方附件，并用 MinerU
解析主论文。随后按科研步骤生成多道独立任务。结果在 `runs/ams02-hep-taskgen/local/<模型名>/`，先查看
`batch_report.json` 和论文目录中的 `task_series.json`。

可以先给第 3 步的命令加 `--dry-run`，验证下载、缓存与任务目录准备；这一步仍会联网，
但不调用生成模型。完成单篇检查后，删掉 `--through build` 可继续审核、解题、评分和科学评审。
确认单篇各阶段结果后，再去掉 `--limit 1` 批量运行。

模型通过远程接口运行，MinerU 会接收需要解析的 PDF。生成任务时，代理还会调用终端
执行分析代码。请在独立的研究环境中使用可信的论文清单。当前锁定的 Node 依赖存在
`npm audit` 安全告警，部署到共享或生产环境前应先审核并更新依赖。

## 资料服务

| 服务 | 本机凭证变量 | 用途 |
| --- | --- | --- |
| Serper | `SERPER_KEY` | 搜索公开资料 |
| Jina Reader | `JINA_KEY` | 提取网页正文 |
| MinerU | `MINERU_TOKEN` | 解析论文正文、公式、图片和表格 |

三个资料服务共用 `.env` 中的凭证。模型切换方法见 [模型配置](MODEL_SWITCHING_ZH.md)。
论文按内容指纹缓存，完整结果可以重复使用。

## 主要目录

| 位置 | 内容 |
| --- | --- |
| `scripts/` | 本机启动、论文准备和真实工具验收 |
| `config/model_profiles.json` | 模型配置名称、协议和输入能力 |
| `sources/ResearchFactory/researchfactory/` | HEP 任务规划、合同、运行与评估代码 |
| `runtime/deepseek-harness/` | 官方运行框架的依赖锁文件和资料服务插件 |
| `inputs/ams02-publications/target_study_pool.jsonl` | 31 篇 AMS 论文及官方附件链接 |
| `inputs/target_study_pool.jsonl` | HiggsML、pyhf 的物理论文输入 |
| `.cache/` | 本机论文、附件和解析缓存 |
| `runs/` | 任务、审核、解题、评分和验收记录 |

运行方式与结果文件见 [完整流程](HEP_AUTOMATION_ZH.md)。
代码分工与科学检查见 [设计文档](RCB_HEP_DEEPSEEK_HARNESS_DESIGN.md)。

## 重新打包

```bash
uv run --no-project --python 3.10 python scripts/package_release.py
```

压缩包生成于 `dist/hep-research-pipeline.zip`。它包含源码、提示词、论文链接清单、
依赖锁文件和配置模板。

已安装依赖并配置 `.env` 后，用以下命令生成私有运行包：

```bash
uv run --no-project --python 3.10 python scripts/package_release.py --private
```

`dist/hep-research-pipeline-private.zip` 额外包含真实 `.env`、31 篇 AMS 论文、官方附件和解析结果。
打包时复用已有缓存，自动准备缺失的论文。`runs/`、代理历史会话和 `.env.runtime` 留在本机。
请通过私密渠道交付这个未加密的 ZIP。解压后执行 `chmod 600 .env`，将密钥文件限制为当前用户可读写。
