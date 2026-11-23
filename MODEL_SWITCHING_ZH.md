# 本机模型配置

所有阶段在本机的 DeepSeek Harness（代理运行框架）中执行，模型推理通过配置的 API
（服务接口）完成。项目从 `.env` 读取服务地址、密钥和默认模型 `Sage-S2.1-Flash`。

## 常用命令

使用默认模型运行一篇论文：

```bash
bash scripts/run_ams_hep_batch.sh --limit 1
```

临时换用同一个服务提供的其他模型时，传入 `--model 模型名`。
只查看配置时使用 `--show-config`，只检查目录和请求时使用 `--dry-run`。

## 修改连接配置

`.env` 保存以下字段：

```dotenv
API_BASE="模型服务端点"
API_KEY="模型密钥"
MODEL_NAME="Sage-S2.1-Flash"
```

修改后，下次启动会重新读取。密钥通过进程环境传递，运行请求只保存凭证变量名称。
配置文件权限设为仅当前用户可读写。

`config/model_profiles.json` 的 `local` 配置指定凭证文件、API 协议和输入类型；当前启用文本输入。

需要模型直接判断图像内容时，选择支持图像输入的模型，并在配置的 `input` 中声明 `image`。

## 共用工具与本机运行设置

`SERPER_KEY`、`JINA_KEY`、`MINERU_TOKEN` 从 `.env` 读取，供各模型共用。
当前进程中的同名变量可以覆盖文件值。

`.env.runtime` 保存本机运行设置，例如 `DEEPSEEK_HARNESS_NODE`（Node 可执行文件路径）。
启动器会读取系统 HTTP 代理，并传给 Node，使其与 Python 使用一致的联网设置。
也可以在 `.env.runtime` 显式设置 `HTTP_PROXY`、`HTTPS_PROXY` 和 `NO_PROXY`。

结果按模型分别保存在 `runs/ams02-hep-taskgen/local/<模型>/`。
比较模型或修改任务方法时，使用新的 `--output-root` 保存一轮独立实验。
