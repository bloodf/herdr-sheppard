import type { ExtensionAPI, ExtensionContext } from "@oh-my-pi/pi-coding-agent";
import { Database } from "bun:sqlite";
import { existsSync, realpathSync } from "node:fs";
import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { join } from "node:path";
import { z } from "zod";
const digest = (value: string) => createHash("sha256").update(value).digest("hex");
async function projectRoot(run: ExtensionAPI["exec"], cwd: string): Promise<string> {
  try {
    const result = await run("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], { cwd, timeout: 10_000 });
    if (result.code !== 0 || result.killed) throw new Error("Git root unavailable");
    return realpathSync(result.stdout.trim());
  } catch { return realpathSync(cwd); }
}

const Snapshot = z.object({
  config: z.object({ enabled: z.boolean(), herdrWorkspaceId: z.string().nullable() }),
  plan: z.object({ owner: z.string() }).nullable(),
  tasks: z.array(z.object({ id: z.string(), owner: z.string(), phase: z.string() })),
  sessions: z.record(z.string(), z.object({ taskId: z.string().nullable(), role: z.string() })),
  decisions: z.object({ calls: z.number(), cached: z.number() }),
  gatewaySessions: z.record(z.string(), z.object({ status: z.string() })).default({}),
});

type ReporterContext = {
  agent: Pick<ExtensionContext["agent"], "kind" | "id">;
  model: Pick<NonNullable<ExtensionContext["model"]>, "provider" | "id"> | undefined;
  sessionManager: Pick<ExtensionContext["sessionManager"], "getSessionId" | "getSessionFile">;
};
export function tokens(raw: unknown, ctx: ReporterContext, workspace: string, now = Date.now()): Record<string, string> | undefined {
  if (process.env.OMPCODE === "1" || ctx.agent.kind !== "main") return;
  const parsed = Snapshot.safeParse(raw);
  if (!parsed.success || !parsed.data.config.enabled || parsed.data.config.herdrWorkspaceId !== workspace) return;
  const state = parsed.data;
  const sessionId = ctx.sessionManager.getSessionId();
  const sessionFile = ctx.sessionManager.getSessionFile();
  const owner = ctx.agent.kind === "main" && process.env.LAYA_FLOW_RUN_ID ? `supervised:${process.env.LAYA_FLOW_RUN_ID}:main` : `${sessionId}:${ctx.agent.id}`;
  const session = state.sessions[owner];
  if (!session) return;
  const task = state.tasks.find(task => task.id === session.taskId && task.owner === owner);
  return {
    crew_owner: owner, crew_parent: task && state.plan?.owner !== owner ? state.plan?.owner ?? "" : "",
    crew_role: state.plan?.owner === owner ? "orchestrator" : task ? "worker" : "unassigned",
    crew_route_role: session.role, crew_session: sessionId,
    crew_session_path: sessionFile ? digest(sessionFile) : "",
    crew_model: ctx.model ? `${ctx.model.provider}/${ctx.model.id}` : "unknown",
    crew_task: task?.id ?? "", crew_phase: task?.phase ?? "unassigned",
    crew_quota: state.gatewaySessions[owner]?.status ?? "unknown",
    crew_laya_calls: String(state.decisions.calls), crew_laya_cached: String(state.decisions.cached),
    crew_at: String(now),
  };
}

export default function extension(pi: ExtensionAPI) {
  if (process.env.OMPCODE === "1") return;
  let current: ExtensionContext | undefined;
  let timer: ReturnType<typeof setInterval> | undefined;
  let busy = false;
  let stopped = false;
  const paths = new Map<string, string>();
  async function publish() {
    if (busy || stopped || !current || process.env.HERDR_ENV !== "1" || !process.env.HERDR_PANE_ID || !process.env.HERDR_WORKSPACE_ID) return;
    busy = true;
    try {
      const ctx = current;
      let path = paths.get(ctx.cwd);
      if (!path) {
        const root = await projectRoot((command, args, options) => pi.exec(command, args, options), ctx.cwd);
        path = join(homedir(), ".omp", "agent", "laya-flow", digest(root).slice(0, 24), "state.sqlite");
        paths.set(ctx.cwd, path);
      }
      if (stopped || !existsSync(path)) return;
      const db = new Database(path, { readonly: true });
      let values: Record<string, string> | undefined;
      try {
        const row = z.object({ json: z.string() }).parse(db.query("SELECT json FROM state WHERE id=1").get());
        values = tokens(JSON.parse(row.json), ctx, process.env.HERDR_WORKSPACE_ID);
      } finally { db.close(); }
      if (!values || stopped || current !== ctx) return;
      const args = ["pane", "report-metadata", process.env.HERDR_PANE_ID, "--source", `crew-${digest(values.crew_owner + values.crew_session).slice(0, 16)}`, "--agent", "omp", "--ttl-ms", "15000"];
      for (const [key, value] of Object.entries(values)) args.push("--token", `${key}=${value.slice(0, 1000)}`);
      await pi.exec(process.env.HERDR_BIN_PATH ?? "herdr", args, { cwd: ctx.cwd, timeout: 2000 });
    } catch { /* Optional metadata never stops project execution. */ }
    finally { busy = false; }
  }
  const observe = (ctx: ExtensionContext) => {
    if (process.env.HERDR_ENV !== "1") return;
    current = ctx;
    stopped = false;
    if (!timer) { timer = setInterval(() => { void publish(); }, 5000); timer.unref(); }
  };
  pi.on("session_start", (_event, ctx) => { observe(ctx); });
  pi.on("session_switch", (_event, ctx) => { observe(ctx); });
  pi.on("before_agent_start", (_event, ctx) => { observe(ctx); });
  pi.on("agent_end", (_event, ctx) => { observe(ctx); void publish(); });
  pi.on("session_shutdown", () => { stopped = true; if (timer) clearInterval(timer); timer = undefined; current = undefined; });
}
