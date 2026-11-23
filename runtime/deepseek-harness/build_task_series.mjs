/** 将论文清单中的每道题交给一个 DSH 原生子会话，再统一校验和提交。 */
import { execFile } from "node:child_process";
import { appendFile, readFile, rename, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { promisify } from "node:util";
import { defineTool } from "@deepseek-ai/dsh-tools";

export const name = "hep-build-task-series";
export const inject = ["subagents", "tools"];

const runFile = promisify(execFile);
const validator = "researchfactory.taskgen.tasks.researchclawbench.validate_package";
const parallelism = 8; // 与 DSH 同时运行的子会话上限一致。

/** 复用 Python 的题包合同，错误直接返回给定题会话。 */
async function validate(workspace, flag, signal) {
  try {
    const { stdout } = await runFile(process.env.RESEARCHFACTORY_PYTHON, ["-m", validator, flag], {
      cwd: workspace, signal,
    });
    return JSON.parse(stdout);
  } catch (error) {
    throw new Error(`${flag}: ${error.stderr?.trim() || error.stdout?.trim() || error.message}`, { cause: error });
  }
}

/** 一个题目使用一个新会话，取回结果后释放该会话占用的运行资源。 */
async function buildOne({ ctx, workspace, parent, row, instructions, signal, logPath }) {
  signal.throwIfAborted();
  const activation = await ctx.subagents.startActivation({
    provider: "spawn",
    label: row.id,
    delivery: "caller",
    signal,
    request: {
      parent,
      cwd: join(workspace, "tasks", row.id),
      maxDepth: 1,
      prompt: [{ type: "text", text: `${instructions}\n\nYour assigned plan entry:\n${JSON.stringify(row, null, 2)}` }],
    },
  });
  const record = { task_id: row.id, session_id: activation.childId, started_at: new Date().toISOString() };
  const stop = () => { void activation.dispose().catch(() => {}); };
  signal.addEventListener("abort", stop, { once: true });
  try {
    signal.throwIfAborted();
    await appendFile(logPath, `${JSON.stringify({ ...record, event: "started" })}\n`);
    const result = await activation.result;
    const completed = { ...record, finished_at: new Date().toISOString(), stop_reason: result.stopReason };
    await appendFile(logPath, `${JSON.stringify({ ...completed, event: "finished" })}\n`);
    if (result.stopReason !== "completed") {
      throw new Error(`${row.id}: DSH builder ${result.stopReason}${result.diagnostic ? ` (${result.diagnostic})` : ""}`);
    }
    return completed;
  } finally {
    signal.removeEventListener("abort", stop);
    await activation.dispose();
  }
}

/** 主会话看到的工具；启动、等待和校验都由程序负责。 */
export function apply(ctx) {
  const workspace = process.cwd();
  ctx.tools.register(defineTool({
    name: "build_task_series",
    description: "Read the finalized task_series.json, delegate each selected question to one fresh DSH subagent, wait for the builders and submit the validated task series. Takes no arguments.",
    parameters: {},
    output: { schema: { type: "string" }, render: (_args, value) => [{ type: "text", text: value }] },
    async execute(_args, { agent, signal }) {
      const plan = await validate(workspace, "--prepare-delegation", signal);
      const brief = await readFile(join(workspace, "_protocol", "hep_task_builder.md"), "utf8");
      const skill = await readFile(join(workspace, ".agents", "skills", "researchfactory-taskgen", "SKILL.md"), "utf8");
      const instructions = `${brief}\n\n${skill}`;
      const logPath = join(workspace, "agent_trace", "task_series_subagents.jsonl");
      const controller = new AbortController();
      const childSignal = AbortSignal.any([signal, controller.signal]);
      const tasks = [];
      // 分批使用 DSH 的并行名额。每一条清单记录只启动一次，失败就结束本组。
      for (let offset = 0; offset < plan.length; offset += parallelism) {
        const batch = plan.slice(offset, offset + parallelism).map(row => buildOne({
          ctx, workspace, parent: agent, row, instructions, signal: childSignal, logPath,
        }));
        try {
          tasks.push(...await Promise.all(batch));
        } catch (error) {
          controller.abort(error);
          await Promise.allSettled(batch);
          throw error;
        }
      }
      signal.throwIfAborted();
      const receiptPath = join(workspace, "task_series.delegation.json");
      const receipt = { parent_session_id: agent.session.id, plan, tasks };
      await writeFile(`${receiptPath}.tmp`, `${JSON.stringify(receipt, null, 2)}\n`);
      await rename(`${receiptPath}.tmp`, receiptPath);
      await validate(workspace, "--submit-series", signal);
      return `Submitted ${tasks.length} validated task packages: ${plan.map(row => row.id).join(", ")}`;
    },
  }), { when: ({ agent }) => !agent.session.header.parentSession });
}
