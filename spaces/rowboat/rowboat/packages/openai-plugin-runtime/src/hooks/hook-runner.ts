import { createHash } from "node:crypto";
import { types as nodeTypes } from "node:util";

import { assertDirectoryIdentity, snapshotDirectoryIdentity } from "../import/directory-identity.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import type { PluginPolicy } from "../policy/default-policy.js";
import { capturePluginPolicy } from "../policy/policy-snapshot.js";
import {
  assertVerifiedProcessExecutionRoot,
  revealVerifiedProcessExecutionRoot,
  type VerifiedProcessExecutionRoot,
} from "../process/process-execution-root.js";
import {
  DEFAULT_PROCESS_OUTPUT_LIMIT_BYTES,
  NodeProcessSpawner,
  captureSafeEnvironment,
  collectBoundedOutput,
  resolveSafeWorkingDirectory,
  type ProcessSpawner,
  type SafeSpawnOptions,
  type SpawnCompletion,
  type SpawnedProcess,
} from "../process/safe-process.js";
import { normalizeHookEvent, type HookEventType } from "./hook-event.js";
import { compileHookMatcher, type CompiledHookMatcher } from "./hook-matcher.js";

const COMPONENT_DIGEST = /^[a-f0-9]{64}$/u;
const COMMAND = /^\.\/[A-Za-z0-9][A-Za-z0-9._/-]*$/u;
const MAX_TIMEOUT_MS = 300_000;
const MAX_OUTPUT_BYTES = 1024 * 1024;
const CLEANUP_SETTLE_MS = 25;
const PROCESS_CLEANUP_GRACE_MS = 1_000;

export interface HookCommand {
  readonly event: "PostToolUse" | "Stop";
  readonly matcher?: string;
  readonly command: string;
  readonly args: readonly string[];
  readonly componentDigest: string;
}

export interface HookExecutionEvent {
  readonly sourceEvent: "PostToolUse" | "Stop";
  readonly actionName?: string;
  readonly parentOutcome: "success" | "failed";
}

export interface HookExecutionReceipt {
  readonly event: HookEventType;
  readonly matcher: string | null;
  readonly status: "success" | "failed" | "denied" | "timed_out";
  readonly parentOutcome: "success" | "failed";
  readonly outputDigest?: string;
}

export interface HookRunnerOptions {
  readonly hook: HookCommand;
  readonly executionRoot: VerifiedProcessExecutionRoot;
  readonly parentLicense: string | undefined;
  readonly policy: PluginPolicy;
  readonly spawner?: ProcessSpawner;
  readonly timeoutMilliseconds?: number;
  readonly maxOutputBytes?: number;
}

interface CapturedHook {
  readonly event: HookEventType;
  readonly sourceEvent: "PostToolUse" | "Stop";
  readonly matcher: CompiledHookMatcher | null;
  readonly command: string;
  readonly args: readonly string[];
  readonly componentDigest: string;
}

function ownData(object: object, name: string): unknown {
  const descriptor = Object.getOwnPropertyDescriptor(object, name);
  if (descriptor === undefined || !("value" in descriptor) || !descriptor.enumerable) {
    throw new Error("component_unsupported");
  }
  return descriptor.value;
}

function assertPlainRecord(source: unknown): asserts source is Record<string, unknown> {
  if (typeof source !== "object" || source === null || Object.getPrototypeOf(source) !== Object.prototype) {
    throw new Error("component_unsupported");
  }
  const descriptors = Object.getOwnPropertyDescriptors(source);
  if (Object.values(descriptors).some((descriptor) => !("value" in descriptor))) {
    throw new Error("component_unsupported");
  }
}

function assertExactKeys(source: object, required: readonly string[], optional: readonly string[] = []): void {
  const keys = Object.keys(source);
  const allowed = new Set([...required, ...optional]);
  if (required.some((key) => !Object.hasOwn(source, key)) || keys.some((key) => !allowed.has(key))) {
    throw new Error("component_unsupported");
  }
}

function captureArguments(source: unknown): readonly string[] {
  if (!Array.isArray(source) || Object.getPrototypeOf(source) !== Array.prototype || source.length > 128) {
    throw new Error("component_unsupported");
  }
  const descriptors = Object.getOwnPropertyDescriptors(source);
  const allowedKeys = new Set(["length", ...Array.from({ length: source.length }, (_, index) => String(index))]);
  if (Object.keys(descriptors).some((key) => !allowedKeys.has(key))) {
    throw new Error("component_unsupported");
  }
  const captured: string[] = [];
  for (let index = 0; index < source.length; index += 1) {
    const descriptor = descriptors[String(index)];
    if (
      descriptor === undefined
      || !("value" in descriptor)
      || !descriptor.enumerable
      || typeof descriptor.value !== "string"
      || descriptor.value.length > 4_096
      || descriptor.value.includes("\0")
    ) {
      throw new Error("component_unsupported");
    }
    captured.push(descriptor.value);
  }
  return Object.freeze(captured);
}

function captureHook(source: HookCommand): CapturedHook {
  assertPlainRecord(source);
  assertExactKeys(source, ["event", "command", "args", "componentDigest"], ["matcher"]);
  const sourceEvent = ownData(source, "event");
  const event = normalizeHookEvent(sourceEvent);
  const matcherSource = Object.hasOwn(source, "matcher") ? ownData(source, "matcher") : undefined;
  if ((event === "post_tool_use" && matcherSource === undefined) || (event === "stop" && matcherSource !== undefined)) {
    throw new Error("component_unsupported");
  }
  const command = ownData(source, "command");
  const args = captureArguments(ownData(source, "args"));
  const componentDigest = ownData(source, "componentDigest");
  if (
    typeof command !== "string"
    || command.length === 0
    || command.length > 1_024
    || command.includes("\0")
    || !COMMAND.test(command)
    || command.replace(/^\.\//u, "").split("/").some((segment) => segment === "" || segment === "." || segment === "..")
    || typeof componentDigest !== "string"
    || !COMPONENT_DIGEST.test(componentDigest)
  ) {
    throw new Error("component_unsupported");
  }
  return Object.freeze({
    event,
    sourceEvent: sourceEvent as "PostToolUse" | "Stop",
    matcher: matcherSource === undefined ? null : compileHookMatcher(matcherSource),
    command,
    args,
    componentDigest,
  });
}

type CapturedHookEvent = Readonly<{
  sourceEvent: "PostToolUse" | "Stop";
  event: HookEventType;
  actionName: string | null;
  parentOutcome: "success" | "failed";
}>;

function captureEvent(source: unknown): CapturedHookEvent {
  if (
    typeof source !== "object"
    || source === null
    || nodeTypes.isProxy(source)
    || Object.getPrototypeOf(source) !== Object.prototype
  ) {
    throw new Error("component_unsupported");
  }
  const descriptors = Object.getOwnPropertyDescriptors(source);
  const keys = Reflect.ownKeys(descriptors);
  const allowed = new Set(["sourceEvent", "parentOutcome", "actionName"]);
  if (
    !Object.hasOwn(descriptors, "sourceEvent")
    || !Object.hasOwn(descriptors, "parentOutcome")
    || keys.some((key) => typeof key !== "string" || !allowed.has(key))
    || Object.values(descriptors).some((descriptor) => !("value" in descriptor) || !descriptor.enumerable)
  ) {
    throw new Error("component_unsupported");
  }
  const sourceEvent = descriptors.sourceEvent?.value;
  const event = normalizeHookEvent(sourceEvent);
  const parentOutcome = descriptors.parentOutcome?.value;
  const actionName = descriptors.actionName?.value;
  if (
    (parentOutcome !== "success" && parentOutcome !== "failed")
    || (event === "post_tool_use" && (typeof actionName !== "string" || actionName.length === 0))
    || (event === "stop" && actionName !== undefined)
  ) {
    throw new Error("component_unsupported");
  }
  return Object.freeze({
    sourceEvent: sourceEvent as "PostToolUse" | "Stop",
    event,
    actionName: typeof actionName === "string" ? actionName : null,
    parentOutcome,
  });
}

function receipt(input: HookExecutionReceipt): HookExecutionReceipt {
  return Object.freeze({ ...input });
}

function delay(milliseconds: number): Promise<"timed_out"> {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve("timed_out"), milliseconds);
    timer.unref?.();
  });
}

async function settleCleanup(operation: Promise<unknown>): Promise<void> {
  await Promise.race([operation.then(() => undefined, () => undefined), delay(CLEANUP_SETTLE_MS)]);
  void operation.catch(() => undefined);
}

interface CapturedSpawnedProcess {
  readonly stdout: AsyncIterable<Uint8Array | string>;
  readonly stderr: AsyncIterable<Uint8Array | string>;
  readonly completion: Promise<SpawnCompletion>;
  readonly terminate: () => Promise<void>;
}

class SpawnedProcessInvalidError extends Error {
  readonly terminate: () => Promise<void>;

  constructor(terminate: () => Promise<void>) {
    super("process_failed");
    this.name = "SpawnedProcessInvalidError";
    this.terminate = terminate;
  }
}

function createCapturedTerminator(
  source: object,
  descriptors: PropertyDescriptorMap,
): () => Promise<void> {
  const killDescriptor = descriptors.kill;
  const kill = killDescriptor !== undefined
    && "value" in killDescriptor
    && typeof killDescriptor.value === "function"
    ? (signal: "SIGTERM" | "SIGKILL"): void => {
        Reflect.apply(killDescriptor.value as (...args: unknown[]) => unknown, source, [signal]);
      }
    : undefined;
  const completionDescriptor = descriptors.completion;
  const completion = completionDescriptor !== undefined
    && "value" in completionDescriptor
    && completionDescriptor.value instanceof Promise
    && !nodeTypes.isProxy(completionDescriptor.value)
    && Object.getPrototypeOf(completionDescriptor.value) === Promise.prototype
    ? completionDescriptor.value as Promise<unknown>
    : undefined;
  const settled = completion?.then(() => true, () => true);
  let termination: Promise<void> | undefined;
  return (): Promise<void> => {
    termination ??= (async (): Promise<void> => {
      if (kill === undefined) return;
      try {
        kill("SIGTERM");
      } catch {
        return;
      }
      if (settled !== undefined) {
        if (await Promise.race([settled, delay(PROCESS_CLEANUP_GRACE_MS)]) !== "timed_out") return;
      } else {
        await delay(PROCESS_CLEANUP_GRACE_MS);
      }
      try {
        kill("SIGKILL");
      } catch {
        return;
      }
      if (settled !== undefined) await Promise.race([settled, delay(PROCESS_CLEANUP_GRACE_MS)]);
    })();
    return termination;
  };
}

function inspectSpawnedProcess(source: unknown): Readonly<{
  readonly descriptors: PropertyDescriptorMap;
  readonly terminate: () => Promise<void>;
}> {
  if (
    typeof source !== "object"
    || source === null
    || nodeTypes.isProxy(source)
    || Object.getPrototypeOf(source) !== Object.prototype
  ) {
    throw new SpawnedProcessInvalidError(async () => undefined);
  }
  const descriptors = Object.getOwnPropertyDescriptors(source);
  return Object.freeze({
    descriptors,
    terminate: createCapturedTerminator(source, descriptors),
  });
}

function captureSpawnedProcess(source: unknown): CapturedSpawnedProcess {
  const inspected = inspectSpawnedProcess(source);
  const { descriptors, terminate } = inspected;
  const keys = Reflect.ownKeys(descriptors);
  const allowed = new Set(["stdout", "stderr", "completion", "kill", "writeStdin", "closeStdin"]);
  const stdout = descriptors.stdout;
  const stderr = descriptors.stderr;
  const completion = descriptors.completion;
  const kill = descriptors.kill;
  const optionalMethodsValid = [descriptors.writeStdin, descriptors.closeStdin].every(
    (descriptor) => descriptor === undefined || ("value" in descriptor && typeof descriptor.value === "function"),
  );
  if (
    keys.some((key) => typeof key !== "string" || !allowed.has(key))
    || stdout === undefined || !("value" in stdout) || typeof stdout.value !== "object" || stdout.value === null
    || stderr === undefined || !("value" in stderr) || typeof stderr.value !== "object" || stderr.value === null
    || completion === undefined || !("value" in completion)
    || !(completion.value instanceof Promise)
    || nodeTypes.isProxy(completion.value)
    || Object.getPrototypeOf(completion.value) !== Promise.prototype
    || kill === undefined || !("value" in kill) || typeof kill.value !== "function"
    || !optionalMethodsValid
  ) {
    throw new SpawnedProcessInvalidError(terminate);
  }
  return Object.freeze({
    stdout: stdout.value as AsyncIterable<Uint8Array | string>,
    stderr: stderr.value as AsyncIterable<Uint8Array | string>,
    completion: completion.value as Promise<SpawnCompletion>,
    terminate,
  });
}

async function terminateUnknownSpawnedProcess(source: unknown): Promise<void> {
  try {
    await inspectSpawnedProcess(source).terminate();
  } catch (error: unknown) {
    if (error instanceof SpawnedProcessInvalidError) await error.terminate();
  }
}

export class HookRunner {
  readonly #hook: CapturedHook;
  readonly #executionRoot: VerifiedProcessExecutionRoot;
  readonly #executionRootDetails: ReturnType<typeof revealVerifiedProcessExecutionRoot>;
  readonly #parentLicense: string | undefined;
  readonly #policy: PluginPolicy;
  readonly #spawner: ProcessSpawner;
  readonly #baselineEnvironment: Readonly<Record<string, string>>;
  readonly #timeoutMilliseconds: number;
  readonly #maxOutputBytes: number;

  constructor(options: HookRunnerOptions) {
    assertPlainRecord(options);
    this.#hook = captureHook(ownData(options, "hook") as HookCommand);
    this.#executionRoot = ownData(options, "executionRoot") as VerifiedProcessExecutionRoot;
    this.#executionRootDetails = revealVerifiedProcessExecutionRoot(this.#executionRoot);
    if (this.#executionRootDetails.componentDigest !== this.#hook.componentDigest) {
      throw new Error("component_unsupported");
    }
    const policy = ownData(options, "policy") as PluginPolicy;
    this.#policy = capturePluginPolicy(policy);
    this.#parentLicense = ownData(options, "parentLicense") as string | undefined;
    this.#spawner = Object.hasOwn(options, "spawner")
      ? ownData(options, "spawner") as ProcessSpawner
      : new NodeProcessSpawner();
    this.#baselineEnvironment = captureSafeEnvironment(Object.freeze({ NO_COLOR: "1" }), Object.freeze({}));
    const timeout = Object.hasOwn(options, "timeoutMilliseconds")
      ? ownData(options, "timeoutMilliseconds")
      : 30_000;
    const outputLimit = Object.hasOwn(options, "maxOutputBytes")
      ? ownData(options, "maxOutputBytes")
      : DEFAULT_PROCESS_OUTPUT_LIMIT_BYTES;
    if (!Number.isSafeInteger(timeout) || (timeout as number) < 1 || (timeout as number) > MAX_TIMEOUT_MS) {
      throw new Error("component_unsupported");
    }
    if (!Number.isSafeInteger(outputLimit) || (outputLimit as number) < 1 || (outputLimit as number) > MAX_OUTPUT_BYTES) {
      throw new Error("component_unsupported");
    }
    this.#timeoutMilliseconds = timeout as number;
    this.#maxOutputBytes = outputLimit as number;
    Object.freeze(this);
  }

  async run(sourceEvent: HookExecutionEvent): Promise<HookExecutionReceipt> {
    const event = captureEvent(sourceEvent);
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.#timeoutMilliseconds);
    const operation = Promise.resolve().then(() => this.#run(event, controller.signal));
    try {
      return await Promise.race([
        operation,
        new Promise<HookExecutionReceipt>((resolve) => {
          controller.signal.addEventListener("abort", () => {
            resolve(receipt({
              event: this.#hook.event,
              matcher: this.#hook.matcher?.source ?? null,
              status: "timed_out",
              parentOutcome: event.parentOutcome,
            }));
          }, { once: true });
        }),
      ]);
    } finally {
      clearTimeout(timer);
      if (controller.signal.aborted) await settleCleanup(operation);
    }
  }

  async #run(event: CapturedHookEvent, signal: AbortSignal): Promise<HookExecutionReceipt> {
    if (event.event !== this.#hook.event || event.sourceEvent !== this.#hook.sourceEvent) {
      throw new Error("component_unsupported");
    }
    if (this.#hook.matcher !== null && !this.#hook.matcher.matches(event.actionName)) {
      return receipt({ event: event.event, matcher: null, status: "denied", parentOutcome: event.parentOutcome });
    }

    for (const capability of ["hook_command", "write"] as const) {
      const decision = evaluateComponentAdmission(this.#parentLicense, { kind: capability }, this.#policy);
      if (decision.status !== "admitted") throw new Error(decision.reason);
    }
    if (signal.aborted) return receipt({ event: event.event, matcher: this.#hook.matcher?.source ?? null, status: "timed_out", parentOutcome: event.parentOutcome });

    let ownedCleanup: (() => Promise<void>) | undefined;
    try {
      await assertVerifiedProcessExecutionRoot(this.#executionRoot);
      const cwd = await resolveSafeWorkingDirectory(this.#executionRootDetails.path, ".");
      const workingDirectoryIdentity = await snapshotDirectoryIdentity(cwd);
      await assertVerifiedProcessExecutionRoot(this.#executionRoot);
      await assertDirectoryIdentity(workingDirectoryIdentity);
      if (signal.aborted) throw new Error("operation_aborted");

      const spawnOptions: SafeSpawnOptions = Object.freeze({
        shell: false,
        cwd,
        env: this.#baselineEnvironment,
        executionRootIdentity: this.#executionRootDetails.identity,
        trustedStoreIdentity: this.#executionRootDetails.trustedStoreIdentity,
        workingDirectoryIdentity,
        componentDigest: this.#hook.componentDigest,
        executionRootDigest: this.#executionRootDetails.executionRootDigest,
        executionRootInventory: this.#executionRootDetails.inventory,
        signal,
      });
      await assertVerifiedProcessExecutionRoot(this.#executionRoot);
      await assertDirectoryIdentity(workingDirectoryIdentity);
      if (signal.aborted) throw new Error("operation_aborted");

      const spawnPromise = Promise.resolve(this.#spawner.spawn(this.#hook.command, this.#hook.args, spawnOptions));
      let owned = false;
      const lateCleanup = spawnPromise.then(async (spawned) => {
        if (!owned && signal.aborted) {
          owned = true;
          await terminateUnknownSpawnedProcess(spawned);
        }
      }, () => undefined);
      void lateCleanup.catch(() => undefined);
      const spawned = await Promise.race([
        spawnPromise,
        new Promise<never>((_, reject) => signal.addEventListener("abort", () => reject(new Error("operation_aborted")), { once: true })),
      ]);
      if (signal.aborted) {
        if (!owned) {
          owned = true;
          await terminateUnknownSpawnedProcess(spawned);
        }
        throw new Error("operation_aborted");
      }
      owned = true;
      let captured: CapturedSpawnedProcess;
      try {
        captured = captureSpawnedProcess(spawned);
        ownedCleanup = captured.terminate;
      } catch (error: unknown) {
        if (error instanceof SpawnedProcessInvalidError) ownedCleanup = error.terminate;
        throw error;
      }
      return await this.#collect(captured, event, signal);
    } catch {
      if (ownedCleanup !== undefined) await settleCleanup(ownedCleanup());
      return receipt({
        event: event.event,
        matcher: this.#hook.matcher?.source ?? null,
        status: signal.aborted ? "timed_out" : "failed",
        parentOutcome: event.parentOutcome,
      });
    }
  }

  async #collect(
    spawned: CapturedSpawnedProcess,
    event: CapturedHookEvent,
    signal: AbortSignal,
  ): Promise<HookExecutionReceipt> {
    const stdoutLimit = Math.ceil(this.#maxOutputBytes / 2);
    const stderrLimit = Math.floor(this.#maxOutputBytes / 2);
    const execution = Promise.all([
      spawned.completion,
      collectBoundedOutput(spawned.stdout, stdoutLimit, Object.freeze([])),
      collectBoundedOutput(spawned.stderr, stderrLimit, Object.freeze([])),
    ]);
    const outcome = await Promise.race([
      execution.then((value) => Object.freeze({ kind: "settled" as const, value }), () => Object.freeze({ kind: "failed" as const })),
      new Promise<Readonly<{ readonly kind: "timed_out" }>>((resolve) => {
        if (signal.aborted) resolve(Object.freeze({ kind: "timed_out" }));
        else signal.addEventListener("abort", () => resolve(Object.freeze({ kind: "timed_out" })), { once: true });
      }),
    ]);
    if (outcome.kind === "timed_out") {
      const cleanup = spawned.terminate();
      await settleCleanup(cleanup);
      void execution.catch(() => undefined);
      return receipt({ event: event.event, matcher: this.#hook.matcher?.source ?? null, status: "timed_out", parentOutcome: event.parentOutcome });
    }
    if (outcome.kind === "failed") {
      await settleCleanup(spawned.terminate());
      return receipt({ event: event.event, matcher: this.#hook.matcher?.source ?? null, status: "failed", parentOutcome: event.parentOutcome });
    }
    const [completion, stdout, stderr] = outcome.value;
    const outputDigest = createHash("sha256")
      .update(`${stdout.digest}\0${stderr.digest}\0${String(completion.exitCode)}\0${completion.signal ?? ""}`, "utf8")
      .digest("hex");
    return receipt({
      event: event.event,
      matcher: this.#hook.matcher?.source ?? null,
      status: completion.exitCode === 0 ? "success" : "failed",
      parentOutcome: event.parentOutcome,
      outputDigest,
    });
  }
}
