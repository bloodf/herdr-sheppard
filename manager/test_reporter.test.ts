import { expect, test } from "bun:test";
import { tokens } from "./reporter";

const ctx = { agent: { kind: "main", id: "main" }, model: { provider: "durindoor", id: "fixture" }, sessionManager: { getSessionId: () => "fixture-session", getSessionFile: () => "/private/fixture-session.jsonl" } } satisfies Parameters<typeof tokens>[1];
function state() {
  return { config: {enabled:true, herdrWorkspaceId:"w1"}, plan:null,
    tasks:[], sessions:{"fixture-session:main":{taskId:null as string|null,role:"worker"}},
    decisions:{calls:0,cached:0},gatewaySessions:{} };
}
test("disabled, malformed and different-workspace state cannot publish task metadata", () => {
  const raw = state();
  expect(tokens(raw, ctx, "w2")).toBeUndefined();
  raw.config.enabled = false;
  expect(tokens(raw, ctx, "w1")).toBeUndefined();
  expect(tokens({ apiKey: "must-not-be-published" }, ctx, "w1")).toBeUndefined();
});
test("publication uses a bounded allowlist and leaves state unchanged", () => {
  const initial = state();
  const raw = { ...initial, config: { ...initial.config, durindoor: { apiKeyEnv: "PRIVATE_KEY_REFERENCE" } } };
  const before = JSON.stringify(raw);
  const values = tokens(raw, ctx, "w1", 1000);
  expect(values?.crew_model).toBe("durindoor/fixture");
  expect(values?.crew_role).toBe("unassigned");
  expect(values?.crew_session).toBe("fixture-session");
  expect(values?.crew_at).toBe("1000");
  expect(Object.keys(values ?? {}).length).toBeLessThanOrEqual(16);
  expect(JSON.stringify(values)).not.toContain("PRIVATE_KEY_REFERENCE");
  expect(values?.crew_session_path).toHaveLength(64);
  expect(JSON.stringify(values)).not.toContain("/private/");
  expect(JSON.stringify(raw)).toBe(before);
});
test("role and parent come from actual plan ownership and exclusive task binding", () => {
  const initial = state();
  const raw = { ...initial, plan: { owner: "fixture-session:main" }, tasks: [{ id: "task-a", owner: "another-worker", phase: "active" }] };
  expect(tokens(raw, ctx, "w1")?.crew_role).toBe("orchestrator");
  raw.plan.owner = "different-head";
  raw.sessions["fixture-session:main"]!.taskId = "task-a";
  expect(tokens(raw, ctx, "w1")?.crew_task).toBe("");
  raw.tasks[0]!.owner = "fixture-session:main";
  expect(tokens(raw, ctx, "w1")?.crew_role).toBe("worker");
  expect(tokens(raw, ctx, "w1")?.crew_parent).toBe("different-head");
});
test("nested OMP sessions cannot publish parent pane metadata", () => {
  const previous = process.env.OMPCODE;
  try {
    process.env.OMPCODE = "1";
    expect(tokens(state(), ctx, "w1")).toBeUndefined();
  } finally {
    if (previous === undefined) delete process.env.OMPCODE;
    else process.env.OMPCODE = previous;
  }
});
