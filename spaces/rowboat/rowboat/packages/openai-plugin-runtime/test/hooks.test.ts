import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  ContentStore,
  DEFAULT_POLICY,
  HookRunner,
  compileHookMatcher,
  digestTree,
  normalizeHookEvent,
  verifyProcessExecutionRoot,
  type HookCommand,
  type HookExecutionEvent,
  type PluginPolicy,
  type ProcessSpawner,
  type SafeSpawnOptions,
  type SpawnedProcess,
} from "../src/index.js";
import { cleanupRegisteredTestRoots, createOwnedTestRoot } from "./test-temp.js";

const COMPONENT_DIGEST = "c".repeat(64);
const ADMITTED_POLICY: PluginPolicy = Object.freeze({
  ...DEFAULT_POLICY,
  allowCommandHooks: true,
  allowWriteCapabilities: true,
});

afterEach(cleanupRegisteredTestRoots);

function emptyStream(): AsyncIterable<Uint8Array> {
  return (async function* stream() {})();
}

function processResult(options: {
  readonly exitCode?: number;
  readonly stdout?: string;
  readonly stderr?: string;
  readonly completion?: Promise<{ readonly exitCode: number | null; readonly signal: string | null }>;
  readonly onKill?: (signal: "SIGTERM" | "SIGKILL") => void;
} = {}): SpawnedProcess {
  const output = options.stdout ?? "";
  const error = options.stderr ?? "";
  return Object.freeze({
    stdout: (async function* stdout() { if (output.length > 0) yield output; })(),
    stderr: (async function* stderr() { if (error.length > 0) yield error; })(),
    completion: options.completion ?? Promise.resolve(Object.freeze({ exitCode: options.exitCode ?? 0, signal: null })),
    kill: options.onKill ?? (() => undefined),
  });
}

class RecordingSpawner implements ProcessSpawner {
  calls = 0;
  lastCommand: string | undefined;
  lastArgs: readonly string[] | undefined;
  lastOptions: SafeSpawnOptions | undefined;
  constructor(private readonly result: SpawnedProcess | Promise<SpawnedProcess> = processResult()) {}
  spawn(command: string, args: readonly string[], options: SafeSpawnOptions): SpawnedProcess | Promise<SpawnedProcess> {
    this.calls += 1;
    this.lastCommand = command;
    this.lastArgs = args;
    this.lastOptions = options;
    return this.result;
  }
}

async function fixture(spawner: ProcessSpawner, options: { readonly timeoutMilliseconds?: number; readonly maxOutputBytes?: number } = {}) {
  const repositoryRoot = await createOwnedTestRoot("hooks-repository");
  const pluginRoot = join(repositoryRoot, "plugin");
  await mkdir(join(pluginRoot, "scripts"), { recursive: true });
  await writeFile(join(pluginRoot, "scripts", "hook"), "safe\n", "utf8");
  const digest = await digestTree(pluginRoot);
  const storeContainer = await createOwnedTestRoot("hooks-store");
  const content = await new ContentStore({
    repositoryRoot,
    storeRoot: join(storeContainer, "store"),
  }).put(pluginRoot, digest);
  const executionRoot = await verifyProcessExecutionRoot(content, COMPONENT_DIGEST);
  const runnerOptions = {
    hook: Object.freeze({
      event: "PostToolUse",
      matcher: "Write|Edit",
      command: "./scripts/hook",
      args: Object.freeze(["--check"]),
      componentDigest: COMPONENT_DIGEST,
    } satisfies HookCommand),
    executionRoot,
    parentLicense: "MIT",
    policy: ADMITTED_POLICY,
    spawner,
    timeoutMilliseconds: options.timeoutMilliseconds ?? 100,
    ...(options.maxOutputBytes === undefined ? {} : { maxOutputBytes: options.maxOutputBytes }),
  } as const;
  return Object.freeze({ runner: new HookRunner(runnerOptions), runnerOptions });
}

const successEvent: HookExecutionEvent = Object.freeze({
  sourceEvent: "PostToolUse",
  actionName: "Write",
  parentOutcome: "success",
});

describe("hook event and matcher capture", () => {
  it("maps only pinned PostToolUse and Stop events", () => {
    expect(normalizeHookEvent("PostToolUse")).toBe("post_tool_use");
    expect(normalizeHookEvent("Stop")).toBe("stop");
    for (const source of ["PreToolUse", "post_tool_use", "", null, { toString: () => "Stop" }]) {
      expect(() => normalizeHookEvent(source)).toThrow("component_unsupported");
    }
  });

  it("rejects accessor and prototype event tricks without invoking them", () => {
    let calls = 0;
    const source = Object.create(null) as Record<string, unknown>;
    Object.defineProperty(source, "event", { enumerable: true, get: () => { calls += 1; return "Stop"; } });
    expect(() => normalizeHookEvent(source)).toThrow("component_unsupported");
    expect(calls).toBe(0);
  });

  it("compiles exact canonical action alternatives into immutable matchers", () => {
    const matcher = compileHookMatcher("Write|Edit");
    expect(matcher.source).toBe("Write|Edit");
    expect(matcher.matches("Write")).toBe(true);
    expect(matcher.matches("Bash")).toBe(false);
    expect(Object.isFrozen(matcher)).toBe(true);
  });

  it.each(["Write||Edit", "Write|Write", "Write.*", " Write", "", "constructor", 7])(
    "rejects unknown or ambiguous matcher %p",
    (source) => expect(() => compileHookMatcher(source)).toThrow("component_unsupported"),
  );
});

describe("policy-controlled hook execution", () => {
  it("does not spawn a command hook without admission", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    const denied = new HookRunner({ ...runnerOptions, policy: DEFAULT_POLICY, spawner });
    await expect(denied.run(successEvent)).rejects.toThrow("hook_not_admitted");
    expect(spawner.calls).toBe(0);
  });

  it("does not spawn when command hooks are admitted but write authority is not", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    const hookOnlyPolicy = Object.freeze({ ...DEFAULT_POLICY, allowCommandHooks: true });
    const denied = new HookRunner({ ...runnerOptions, policy: hookOnlyPolicy, spawner });
    await expect(denied.run(successEvent)).rejects.toThrow("write_review_required");
    expect(spawner.calls).toBe(0);
  });

  it("rejects accessor-backed command arguments without invoking them", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    let calls = 0;
    const args: string[] = [];
    Object.defineProperty(args, "0", { enumerable: true, get: () => { calls += 1; return "--unsafe"; } });
    Object.defineProperty(args, "length", { value: 1 });
    expect(() => new HookRunner({
      ...runnerOptions,
      hook: Object.freeze({ ...runnerOptions.hook, args }),
    })).toThrow("component_unsupported");
    expect(calls).toBe(0);
    expect(spawner.calls).toBe(0);
  });

  it("rejects a command path that can traverse outside the trusted plugin root", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    expect(() => new HookRunner({
      ...runnerOptions,
      hook: Object.freeze({ ...runnerOptions.hook, command: "scripts/../../outside" }),
    })).toThrow("component_unsupported");
    expect(spawner.calls).toBe(0);
  });

  it("rejects a PATH-selected executable instead of allowing hook input to choose one", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    expect(() => new HookRunner({
      ...runnerOptions,
      hook: Object.freeze({ ...runnerOptions.hook, command: "powershell.exe" }),
    })).toThrow("component_unsupported");
    expect(spawner.calls).toBe(0);
  });

  it("executes exact command and args with shell false at the trusted plugin root", async () => {
    const spawner = new RecordingSpawner();
    const { runner } = await fixture(spawner);
    const receipt = await runner.run(successEvent);
    expect(receipt).toEqual({ event: "post_tool_use", matcher: "Write|Edit", status: "success", parentOutcome: "success", outputDigest: expect.stringMatching(/^[a-f0-9]{64}$/) });
    expect(spawner.calls).toBe(1);
    expect(spawner.lastCommand).toBe("./scripts/hook");
    expect(spawner.lastArgs).toEqual(["--check"]);
    expect(spawner.lastOptions).toMatchObject({ shell: false, env: { NO_COLOR: "1" } });
    expect(Object.isFrozen(receipt)).toBe(true);
  });

  it("runs a Stop hook without a matcher and captures mutable descriptor input", async () => {
    const spawner = new RecordingSpawner();
    const { runnerOptions } = await fixture(spawner);
    const args = ["--close"];
    const hook = {
      event: "Stop" as const,
      command: "./scripts/hook",
      args,
      componentDigest: COMPONENT_DIGEST,
    };
    const runner = new HookRunner({ ...runnerOptions, hook, spawner });
    hook.command = "./outside";
    args[0] = "--mutated";
    const receipt = await runner.run(Object.freeze({ sourceEvent: "Stop", parentOutcome: "success" }));
    expect(receipt).toMatchObject({ event: "stop", matcher: null, status: "success", parentOutcome: "success" });
    expect(spawner.lastCommand).toBe("./scripts/hook");
    expect(spawner.lastArgs).toEqual(["--close"]);
  });

  it("preserves failed parent outcome for success, failure, and non-matching hooks", async () => {
    for (const exitCode of [0, 7]) {
      const { runner } = await fixture(new RecordingSpawner(processResult({ exitCode })));
      const receipt = await runner.run(Object.freeze({ ...successEvent, parentOutcome: "failed" }));
      expect(receipt.parentOutcome).toBe("failed");
      expect(receipt.status).toBe(exitCode === 0 ? "success" : "failed");
    }
    const spawner = new RecordingSpawner();
    const { runner } = await fixture(spawner);
    const receipt = await runner.run(Object.freeze({ ...successEvent, actionName: "Bash", parentOutcome: "failed" }));
    expect(receipt).toEqual({ event: "post_tool_use", matcher: null, status: "denied", parentOutcome: "failed" });
    expect(spawner.calls).toBe(0);
  });

  it("fails closed before spawning for malformed captured event input", async () => {
    let calls = 0;
    const event = { ...successEvent } as Record<string, unknown>;
    Object.defineProperty(event, "actionName", { enumerable: true, get: () => { calls += 1; return "Write"; } });
    const spawner = new RecordingSpawner();
    const { runner } = await fixture(spawner);
    await expect(runner.run(event as unknown as HookExecutionEvent)).rejects.toThrow("component_unsupported");
    expect(calls).toBe(0);
    expect(spawner.calls).toBe(0);
  });

  it("rejects a proxied event without invoking its traps or spawning", async () => {
    let traps = 0;
    const event = new Proxy({ ...successEvent }, {
      getPrototypeOf(target) { traps += 1; return Reflect.getPrototypeOf(target); },
      ownKeys(target) { traps += 1; return Reflect.ownKeys(target); },
      getOwnPropertyDescriptor(target, property) { traps += 1; return Reflect.getOwnPropertyDescriptor(target, property); },
    });
    const spawner = new RecordingSpawner();
    const { runner } = await fixture(spawner);
    await expect(runner.run(event)).rejects.toThrow("component_unsupported");
    expect(traps).toBe(0);
    expect(spawner.calls).toBe(0);
  });

  it("captures parent outcome once before async work and preserves it after caller mutation", async () => {
    const completion = new Promise<{ readonly exitCode: number | null; readonly signal: string | null }>(() => undefined);
    const spawner = new RecordingSpawner(processResult({ completion }));
    const { runner } = await fixture(spawner, { timeoutMilliseconds: 800 });
    const event = { ...successEvent, parentOutcome: "failed" as "success" | "failed" };
    const pending = runner.run(event);
    while (spawner.calls === 0) await new Promise((resolve) => setTimeout(resolve, 5));
    event.parentOutcome = "success";
    const receipt = await pending;
    expect(receipt).toMatchObject({ status: "timed_out", parentOutcome: "failed" });
  });

  it("kills an owned child when its stdout handle is accessor-backed", async () => {
    let kills = 0;
    const child = Object.create(Object.prototype) as Record<string, unknown>;
    Object.defineProperties(child, {
      stdout: { enumerable: true, get: () => { throw new Error("stdout-secret"); } },
      stderr: { enumerable: true, value: emptyStream() },
      completion: { enumerable: true, value: new Promise(() => undefined) },
      kill: { enumerable: true, value: () => { kills += 1; } },
    });
    const { runner } = await fixture(new RecordingSpawner(child as unknown as SpawnedProcess));
    const started = Date.now();
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(kills).toBeGreaterThan(0);
    expect(Date.now() - started).toBeLessThan(500);
    expect(JSON.stringify(receipt)).not.toContain("stdout-secret");
  });

  it("kills an owned child without invoking a throwing completion getter", async () => {
    let completionReads = 0;
    let kills = 0;
    const child = Object.create(Object.prototype) as Record<string, unknown>;
    Object.defineProperties(child, {
      stdout: { enumerable: true, value: emptyStream() },
      stderr: { enumerable: true, value: emptyStream() },
      completion: { enumerable: true, get: () => { completionReads += 1; throw new Error("completion-secret"); } },
      kill: { enumerable: true, value: () => { kills += 1; } },
    });
    const { runner } = await fixture(new RecordingSpawner(child as unknown as SpawnedProcess));
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(completionReads).toBe(0);
    expect(kills).toBeGreaterThan(0);
  });

  it("kills an owned child when a captured stream errors", async () => {
    let kills = 0;
    const child = processResult({
      completion: new Promise(() => undefined),
      onKill: () => { kills += 1; },
    });
    const failing = Object.freeze({
      ...child,
      stdout: (async function* stdout() { throw new Error("stream-secret"); })(),
    });
    const { runner } = await fixture(new RecordingSpawner(failing));
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(kills).toBeGreaterThan(0);
    expect(JSON.stringify(receipt)).not.toContain("stream-secret");
  });

  it("does not invoke an own throwing then accessor while terminating an immediate child", async () => {
    let thenReads = 0;
    let kills = 0;
    const completion = new Promise<{ readonly exitCode: number | null; readonly signal: string | null }>(() => undefined);
    Object.defineProperty(completion, "then", {
      configurable: true,
      get: () => { thenReads += 1; throw new Error("then-secret"); },
    });
    const child = processResult({ completion, onKill: () => { kills += 1; } });
    const { runner } = await fixture(new RecordingSpawner(child));
    const started = Date.now();
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("timed_out");
    expect(thenReads).toBe(0);
    expect(kills).toBeGreaterThan(0);
    expect(Date.now() - started).toBeLessThan(500);
    expect(JSON.stringify(receipt)).not.toContain("then-secret");
  });

  it("does not invoke an own throwing then accessor while terminating a late child", async () => {
    let resolveSpawn!: (process: SpawnedProcess) => void;
    const spawn = new Promise<SpawnedProcess>((resolve) => { resolveSpawn = resolve; });
    let thenReads = 0;
    let kills = 0;
    const completion = new Promise<{ readonly exitCode: number | null; readonly signal: string | null }>(() => undefined);
    Object.defineProperty(completion, "then", {
      configurable: true,
      get: () => { thenReads += 1; throw new Error("late-then-secret"); },
    });
    const child = processResult({ completion, onKill: () => { kills += 1; } });
    const { runner } = await fixture(new RecordingSpawner(spawn), { timeoutMilliseconds: 800 });
    expect((await runner.run(successEvent)).status).toBe("timed_out");
    resolveSpawn(child);
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(thenReads).toBe(0);
    expect(kills).toBeGreaterThan(0);
  });

  it("best-effort terminates a rejected class child through its own data kill capability", async () => {
    let kills = 0;
    class NonPlainChild {
      readonly stdout = emptyStream();
      readonly stderr = emptyStream();
      readonly completion = new Promise<never>(() => undefined);
      readonly kill = (): void => { kills += 1; };
    }
    const { runner } = await fixture(new RecordingSpawner(new NonPlainChild() as unknown as SpawnedProcess));
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(kills).toBeGreaterThan(0);
  });

  it("best-effort terminates when an exact-prototype completion lacks Promise internal slots", async () => {
    let kills = 0;
    const fakeCompletion = Object.create(Promise.prototype) as Promise<never>;
    const child = Object.freeze({
      stdout: emptyStream(),
      stderr: emptyStream(),
      completion: fakeCompletion,
      kill: (): void => { kills += 1; },
    });
    const { runner } = await fixture(new RecordingSpawner(child));
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(kills).toBeGreaterThan(0);
  });

  it("falls back to SIGKILL exactly once when SIGTERM throws", async () => {
    const signals: string[] = [];
    const child = Object.create(Object.prototype) as Record<string, unknown>;
    Object.defineProperties(child, {
      stdout: { enumerable: true, get: () => { throw new Error("invalid-stream"); } },
      stderr: { enumerable: true, value: emptyStream() },
      completion: { enumerable: true, value: new Promise(() => undefined) },
      kill: { enumerable: true, value: (signal: string) => {
        signals.push(signal);
        if (signal === "SIGTERM") throw new Error("term-secret");
      } },
    });
    const { runner } = await fixture(new RecordingSpawner(child as unknown as SpawnedProcess));
    const started = Date.now();
    const receipt = await runner.run(successEvent);
    expect(receipt.status).toBe("failed");
    expect(signals).toEqual(["SIGTERM", "SIGKILL"]);
    expect(Date.now() - started).toBeLessThan(500);
  });

  it("times out promptly and terminates a process that resolves after the deadline", async () => {
    let resolveSpawn!: (process: SpawnedProcess) => void;
    const lateSpawn = new Promise<SpawnedProcess>((resolve) => { resolveSpawn = resolve; });
    const kills: string[] = [];
    const spawner = new RecordingSpawner(lateSpawn);
    const { runner } = await fixture(spawner, { timeoutMilliseconds: 800 });
    const receipt = await runner.run(Object.freeze({ ...successEvent, parentOutcome: "failed" }));
    expect(receipt.status).toBe("timed_out");
    expect(receipt.parentOutcome).toBe("failed");
    expect(spawner.calls).toBe(1);
    resolveSpawn(processResult({ onKill: (signal) => kills.push(signal) }));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(kills).toContain("SIGTERM");
  });

  it("returns a bounded timeout receipt even when process kill throws", async () => {
    let killCalls = 0;
    const spawned = processResult({
      completion: new Promise(() => undefined),
      onKill: () => { killCalls += 1; throw new Error("contains-secret"); },
    });
    const { runner } = await fixture(new RecordingSpawner(spawned), { timeoutMilliseconds: 800 });
    const receipt = await runner.run(Object.freeze({ ...successEvent, parentOutcome: "failed" }));
    expect(receipt).toEqual({ event: "post_tool_use", matcher: "Write|Edit", status: "timed_out", parentOutcome: "failed" });
    expect(killCalls).toBe(2);
  });

  it("bounds output and exposes only a digest in a secret-free receipt", async () => {
    const secret = "super-secret-command-value";
    const { runner } = await fixture(new RecordingSpawner(processResult({ stdout: `${secret}${"x".repeat(100)}`, stderr: secret })), { maxOutputBytes: 8 });
    const receipt = await runner.run(successEvent);
    const serialized = JSON.stringify(receipt);
    expect(receipt.outputDigest).toMatch(/^[a-f0-9]{64}$/);
    expect(serialized).not.toContain(secret);
    expect(serialized).not.toContain("scripts");
    expect(serialized).not.toContain("command");
  });
});
