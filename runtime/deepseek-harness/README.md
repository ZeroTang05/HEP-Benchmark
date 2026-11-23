# DeepSeek Harness 本机运行框架

此目录锁定官方 `@deepseek-ai/dsh 0.2.2-rc.1`，需要 Node 24.9.0 或更高版本。
从项目根目录运行 `bash scripts/setup.sh` 安装依赖。

| 文件 | 职责 |
| --- | --- |
| `run_task.mjs` | 启动原生 headless（非交互）运行，保存事件与结果 |
| `research_services.mjs` | 把 Serper、Jina、MinerU 接入原生工具接口 |
| `build_task_series.mjs` | 复用 DSH 原生子会话并行制题，统一校验和提交 |
| `package.json`、`package-lock.json` | 记录并锁定官方运行依赖 |

原生运行框架提供代理循环、模型请求、文件、Shell（命令执行）和图像工具。
`web_search`（搜索）使用 Serper；`web_fetch`（网页读取）使用 Jina；
`read_pdf`（论文读取）调用 Python 的 MinerU 缓存模块，返回正文和图表路径。

任务请求用 `research_services` 声明资料访问能力。HEP Builder（任务生成代理）启用，
其他阶段关闭原生 Web 工具与资料插件，并移除三个资料服务凭证。

多题请求用 `delegate_task_series` 启用清单分发工具。主会话交付清单后，程序用 DSH 原生 `spawn` 为每道题开一个新会话。子会话继承主会话的模型配置，接收自己题目的交接资料。整组完成记录保留在任务工作区。

模型和共用工具凭证由 `scripts/model_profiles.py` 从本机私有配置加载。
新会话与插件设置保存于项目 `.cache/deepseek-harness/`。
真实工具验收入口是 `scripts/validate_research_services.py`。

使用方法见 [本机 HEP 项目](../../README.md)。
