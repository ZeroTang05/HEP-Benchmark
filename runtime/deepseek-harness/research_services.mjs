/** 接入 Serper、Jina、MinerU；运行循环、Web 工具和文件工具由原生 Harness 提供。 */
import { defineTool } from "@deepseek-ai/dsh-tools";
import { WebError } from "@deepseek-ai/dsh-web";
import { realpath } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

export const name = "hep-research-services";
export const inject = ["web", "tools", "workingDirectory"];
const runFile = promisify(execFile);

/** 缺少凭证时就地报错，凭证只从环境读取。 */
function credential(key) {
  const value = process.env[key]?.trim();
  if (!value) throw new Error(`Missing ${key}`);
  return value;
}

/** 请求超时和取消由同一个信号控制；错误不包含请求头或响应原文。 */
async function requestJSON(service, url, options, signal) {
  const response = await fetch(url, {
    ...options,
    signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(90000)]) : AbortSignal.timeout(90000),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new WebError(`${service} returned HTTP ${response.status}`, "WEB_PROVIDER_ERROR");
  }
  return response.json();
}

/** Serper 执行实际搜索；此处只转换为 Harness 原生来源格式。 */
export class SerperProvider {
  id = "serper";
  available() { return Boolean(process.env.SERPER_KEY?.trim()); }
  async search(request, signal) {
    const data = await requestJSON("Serper", "https://google.serper.dev/search", {
      method: "POST",
      headers: { "X-API-KEY": credential("SERPER_KEY"), "Content-Type": "application/json" },
      body: JSON.stringify({ q: request.query, num: Math.min(request.maxResults ?? 5, 10) }),
    }, signal);
    if (!Array.isArray(data.organic)) throw new WebError("Serper response has no organic results", "WEB_PROVIDER_ERROR");
    return {
      sources: data.organic.map(item => ({ url: item.link, title: item.title, snippet: item.snippet })),
      truncated: false,
    };
  }
}

/** Jina Reader 返回已经整理的网页正文，原生 web_fetch 负责展示。 */
export class JinaProvider {
  id = "jina";
  available() { return Boolean(process.env.JINA_KEY?.trim()); }
  async fetch(request, signal) {
    const target = new URL(request.url);
    if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) {
      throw new WebError("Jina requires an HTTP(S) URL without embedded credentials", "WEB_INVALID_URL");
    }
    const data = await requestJSON("Jina", `https://r.jina.ai/${target.href}`, {
      headers: { Authorization: `Bearer ${credential("JINA_KEY")}`, Accept: "application/json", "X-Return-Format": "markdown" },
    }, signal);
    if (data.code !== 200 || typeof data.data?.content !== "string") {
      throw new WebError(`Jina returned code ${data.code}`, "WEB_PROVIDER_ERROR");
    }
    const content = data.data.content;
    return {
      url: target.href, statusCode: 200,
      body: { kind: "text", content: content.slice(0, 200000) },
      truncated: content.length > 200000,
    };
  }
}

/** 原生插件注册资料服务；PDF 只允许上传当前工作区中的文件。 */
export function apply(ctx) {
  for (const key of ["SERPER_KEY", "JINA_KEY", "MINERU_TOKEN"]) credential(key);
  ctx.web.registerSearchProvider(new SerperProvider());
  ctx.web.registerFetchProvider(new JinaProvider());
  ctx.tools.register(defineTool({
    name: "add_related_pdf",
    description: "Download a selected open prior-work PDF to your task/related_work directory. Supply a direct public HTTPS PDF URL. Returns the filename, source URL and SHA-256 for references.json.",
    parameters: {
      url: { type: "string", required: true },
      filename: { type: "string", required: true },
    },
    timeoutMs: 150000,
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      const cwd = await ctx.workingDirectory.ensure(exec.agent, exec.signal);
      try {
        const { stdout } = await runFile(credential("RESEARCHFACTORY_PYTHON"), [
          "-m", "researchfactory.taskgen.tasks.researchclawbench.hep_related_work",
          "--", args.url, args.filename,
        ], { cwd, signal: exec.signal });
        return stdout.trim();
      } catch (error) {
        throw new Error(`Related PDF download failed: ${String(error.stderr || error.message).trim().split("\n").at(-1)}`);
      }
    },
  }), { when: ({ agent }) => Boolean(agent) });
  ctx.tools.register(defineTool({
    name: "read_pdf",
    description: "Parse a local PDF with MinerU (text, formulas, figures and tables). Reuses the shared content-addressed cache. Read existing _paper_cache first; only parse a missing source. Returns local Markdown, structured JSON and image paths for native read/read_image tools.",
    parameters: {
      path: { type: "string", required: true, description: "PDF path inside this workspace" },
    },
    timeoutMs: 600000,
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(args, exec) {
      const root = await realpath(process.cwd());
      const pdf = await realpath(resolve(root, args.path));
      const rel = relative(root, pdf);
      if (!rel || rel === ".." || rel.startsWith("../") || isAbsolute(rel)) throw new Error("PDF must be inside the current workspace");
      const python = credential("RESEARCHFACTORY_PYTHON");
      try {
        const result = await runFile(python, [
          "-m", "researchfactory.recall.mineru_assets", "--pdf", pdf,
          "--cache-dir", credential("RF_CACHE_DIR"), "--workspace", root,
        ], { signal: exec.signal, timeout: 590000, maxBuffer: 1024 * 1024, env: process.env });
        return result.stdout.trim();
      } catch (error) {
        // execFile 的默认错误会包含环境外的整段输出；只报告 Python 的结构化错误。
        throw new Error(`MinerU parsing failed: ${String(error.stderr || error.code).slice(-1500)}`);
      }
    },
  }));
}
