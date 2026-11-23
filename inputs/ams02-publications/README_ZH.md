# AMS 论文输入

当前运行清单 `target_study_pool.jsonl` 包含 31 篇 AMS 论文。每行记录一篇目标论文的
身份、全文来源和官方补充数据链接。

| 文件 | 内容 |
| --- | --- |
| `target_study_pool.jsonl` | 当前任务生成使用的论文清单 |
| `catalog.json`、`catalog_enriched.json` | 官网目录与补充资料的采集快照 |
| `manifest.json`、`validation_summary.json` | 来源采集时的下载与身份核对记录 |
| `sources/` | 官网、INSPIRE 和机构存档的原始元数据快照 |

清单包含 163 个官方附件链接。下载后的论文、附件和解析结果保存在 `RF_CACHE_DIR`
指定的本机缓存目录中。

从项目根目录运行一篇论文：

```bash
bash scripts/run_ams_hep_batch.sh --limit 1
```

只准备论文资料：

```bash
bash scripts/prepare_ams_papers.sh --workers 2
```

完整参数与结果位置见 [HEP 自动流程](../../HEP_AUTOMATION_ZH.md)。
