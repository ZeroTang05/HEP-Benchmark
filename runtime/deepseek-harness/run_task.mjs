#!/usr/bin/env node
/**
 * 运行 DeepSeek Harness headless profile，并把 NDJSON 事件归档成一次任务结果。
 * Agent loop 和原生工具由 dsh 提供；按请求启用资料服务插件。
 */
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { appendFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";

function argumentValue(argv, name) {
  const index = argv.indexOf(name);
  if (index < 0 || index + 1 >= argv.length) {
    throw new Error(`missing ${name}`);
  }
  return argv[index + 1];
}

async function writeResult(path, result) {
  await writeFile(path, `${JSON.stringify(result, null, 2)}\n`, "utf8");
}

function yamlString(value) {
  return JSON.stringify(String(value ?? ""));
}

async function main() {
  const nodeMajor = Number(process.versions.node.split(".")[0]);
  if (!Number.isInteger(nodeMajor) || nodeMajor < 24) {
    throw new Error(`DeepSeek Harness requires Node.js >= 24; found ${process.versions.node}`);
  }
  const requestPath = resolve(argumentValue(process.argv.slice(2), "--request"));
  const request = JSON.parse(await readFile(requestPath, "utf8"));
  const workspace = resolve(request.workspace);
  const traceDir = resolve(request.trace_dir);
  const resultPath = resolve(workspace, "deepseek_harness_result.json");
  const eventsPath = resolve(traceDir, "deepseek_harness_events.jsonl");
  const stderrPath = resolve(traceDir, "deepseek_harness_stderr.log");
  const patchPath = resolve(traceDir, "deepseek_harness_model.patch.yml");
  await mkdir(traceDir, { recursive: true });
  await writeFile(eventsPath, "", "utf8");

  const localBin = join(dirname(fileURLToPath(import.meta.url)), "node_modules", ".bin", "dsh");
  const command = process.env.DEEPSEEK_HARNESS_BIN || localBin;
  const profile = process.env.DEEPSEEK_HARNESS_PROFILE || "headless";
  const permissionMode = process.env.DEEPSEEK_HARNESS_PERMISSION_MODE || "workspace-write";
  const approvalPolicy = process.env.DEEPSEEK_HARNESS_APPROVAL_POLICY || "never";
  const provider = String(request.provider || process.env.DEEPSEEK_PROVIDER || "deepseek-official");
  const model = String(request.model || process.env.DEEPSEEK_MODEL || "");
  const inputModalities = request.input_modalities || ["text"];
  if (!Array.isArray(inputModalities) || inputModalities.some(value => !["text", "image"].includes(value))) {
    throw new Error("input_modalities must contain only text or image");
  }
  const patchLines = [
    "- id: agent-default-model",
    "  config:",
    `    provider: ${yamlString(provider)}`,
    `    model: ${yamlString(model)}`,
  ];
  if (provider !== "deepseek-official") {
    const baseURL = String(request.base_url || process.env.DEEPSEEK_BASE_URL || "");
    const api = String(request.api || process.env.DEEPSEEK_API_PROTOCOL || "anthropic-messages");
    const apiKeyEnv = String(
      request.api_key_env
      || process.env.DEEPSEEK_API_KEY_ENV
      || "DEEPSEEK_API_KEY",
    );
    if (!baseURL) {
      throw new Error(`custom provider ${provider} requires base_url or DEEPSEEK_BASE_URL`);
    }
    patchLines.push(
      "- id: llm-pi-ai",
      "  config:",
      "    providers:",
      `      ${provider}:`,
      `        displayName: ${yamlString(provider)}`,
      `        apiKeyEnv: ${yamlString(apiKeyEnv)}`,
      `        api: ${yamlString(api)}`,
      `        baseURL: ${yamlString(baseURL)}`,
      "        models:",
      `          - id: ${yamlString(model)}`,
      `            name: ${yamlString(model)}`,
      `            input: ${JSON.stringify(inputModalities)}`,
    );
  }
  const researchServices = request.research_services === true;
  const serviceKeys = ["SERPER_KEY", "JINA_KEY", "MINERU_TOKEN"];
  if (researchServices) {
    for (const key of [...serviceKeys, "RESEARCHFACTORY_PYTHON", "RF_CACHE_DIR"]) {
      if (!process.env[key]?.trim()) throw new Error(`research services require ${key}`);
    }
  }
  // 关闭其他 Web 后端，保证调用只使用本次明确选择的服务。
  for (const id of ["web-search-deepseek", "web-deepseek-api", "web-fetch-http"]) {
    patchLines.push(`- id: ${id}`, "  disabled: true");
  }
  patchLines.push(
    "- id: tool-web", "  config:",
    `    search: ${researchServices}`, `    fetch: ${researchServices}`,
    "    fetchTimeoutMs: 100000", "    searchTimeoutMs: 60000",
  );
  if (researchServices) {
    patchLines.push(
      "- id: web", "  config:", "    searchProvider: serper", "    fetchProvider: jina",
      "- insert:", "    - id: hep-research-services",
      `      name: ${yamlString(join(dirname(fileURLToPath(import.meta.url)), "research_services.mjs"))}`,
    );
  }
  if (request.delegate_task_series === true) {
    if (!process.env.RESEARCHFACTORY_PYTHON?.trim()) {
      throw new Error("task series requires RESEARCHFACTORY_PYTHON");
    }
    // 保留原生 spawn 后端，题目委派统一从清单工具进入。
    for (const id of ["tool-subagent", "tool-subagent-fork", "tool-workflow"]) {
      patchLines.push(`- id: ${id}`, "  disabled: true");
    }
    patchLines.push(
      "- insert:", "    - id: hep-build-task-series",
      `      name: ${yamlString(join(dirname(fileURLToPath(import.meta.url)), "build_task_series.mjs"))}`,
    );
  }
  await writeFile(patchPath, patchLines.join("\n") + "\n", "utf8");
  const args = [
    "--profile", profile,
    "--patch", patchPath,
    "--json",
    "--permission-mode", permissionMode,
    "--approval-policy", approvalPolicy,
    "-",
  ];
  if (request.resume_session_id) {
    args.splice(args.length - 1, 0, "--session-id", String(request.resume_session_id));
  }
  const nodeBinDir = dirname(process.execPath);
  const childPath = [nodeBinDir, process.env.PATH || ""].filter(Boolean).join(":" );
  const childEnv = { ...process.env };
  if (!researchServices) for (const key of serviceKeys) delete childEnv[key];
  if (request.harness_home) {
    // 项目独立保存新会话和工具配置，避免读取用户全局 MCP/插件配置。
    childEnv.DSH_HOME = resolve(request.harness_home);
    childEnv.DSH_AGENTS_HOME = join(childEnv.DSH_HOME, "agents");
    await mkdir(childEnv.DSH_AGENTS_HOME, { recursive: true });
  }
  const child = spawn(command, args, {
    cwd: workspace,
    env: {
      ...childEnv,
      PATH: childPath,
      DEEPSEEK_MODEL: request.model || process.env.DEEPSEEK_MODEL || "",
    },
    stdio: ["pipe", "pipe", "pipe"],
  });

  let eventCount = 0;
  let finalEvent = null;
  // 事件到达即落盘，长任务可以实时查看工具调用和当前进度。
  function recordEvent(event) {
    appendFileSync(eventsPath, `${JSON.stringify(event)}\n`, "utf8");
    eventCount += 1;
    if (event.type === "final") finalEvent = event;
  }
  let stdoutBuffer = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdoutBuffer += chunk;
    const lines = stdoutBuffer.split("\n");
    stdoutBuffer = lines.pop() || "";
    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line);
      recordEvent(event);
    }
  });
  const stderrChunks = [];
  child.stderr.on("data", (chunk) => stderrChunks.push(chunk));
  child.stdin.end(String(request.prompt || ""));

  const exit = await new Promise((resolveExit, rejectExit) => {
    child.once("error", rejectExit);
    child.once("close", (code, signal) => resolveExit({ code, signal }));
  });
  if (stdoutBuffer.trim()) {
    const event = JSON.parse(stdoutBuffer);
    recordEvent(event);
  }

  await writeFile(stderrPath, stderrChunks.join(""), "utf8");
  const completed = exit.code === 0 && finalEvent?.reason?.kind === "completed";
  await writeResult(resultPath, {
    schema_version: "researchfactory.deepseek_harness_result.v1",
    status: completed ? "completed" : "failed",
    return_code: exit.code,
    signal: exit.signal,
    final_message: finalEvent?.text || "",
    final_reason: finalEvent?.reason || null,
    message: completed ? "" : stderrChunks.join("").slice(-2000),
    event_count: eventCount,
    events_path: eventsPath,
    stderr_path: stderrPath,
    model_patch_path: patchPath,
  });
  if (!completed) process.exitCode = 1;
}

main().catch(async (error) => {
  const argv = process.argv.slice(2);
  let resultPath = "";
  try {
    const requestPath = argumentValue(argv, "--request");
    const request = JSON.parse(await readFile(requestPath, "utf8"));
    resultPath = resolve(request.workspace, "deepseek_harness_result.json");
    await mkdir(resolve(request.workspace), { recursive: true });
  } catch {
    // 请求文件损坏时没有可写的任务工作区，直接让 Node 进程失败。
  }
  if (resultPath) {
    await writeResult(resultPath, {
      schema_version: "researchfactory.deepseek_harness_result.v1",
      status: "failed",
      error_type: error.name || "Error",
      message: String(error.message || error),
    });
  }
  console.error(error.stack || error);
  process.exitCode = 1;
});
