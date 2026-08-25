import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { deserializeMessage, serializeMessage } from "@modelcontextprotocol/sdk/shared/stdio.js";
import type { Transport } from "@modelcontextprotocol/sdk/shared/transport.js";
import type { JSONRPCMessage } from "@modelcontextprotocol/sdk/types.js";

import type { NormalizedProcessMcp } from "../components/mcp-normalizer.js";
import { MAX_PROCESS_TIMEOUT_MS } from "../components/mcp-normalizer.js";
import { evaluateComponentAdmission } from "../policy/capability-policy.js";
import type { PluginPolicy } from "../policy/default-policy.js";
import { capturePluginPolicy } from "../policy/policy-snapshot.js";
import {
  NodeProcessSpawner,
  captureSafeEnvironment,
  collectBoundedOutput,
  resolveSafeWorkingDirectory,
  terminateSpawnedProcess,
  type ProcessSpawner,
  type SpawnedProcess,
} from "../process/safe-process.js";
import {
  assertCredentialRequest,
  revealSecretValue,
  type CredentialResolver,
} from "./credential-resolver.js";
import { assertBinding } from "./provider-registry.js";
import { validateMcpInvocation } from "./mcp-request.js";
import type {
  PluginProvider,
  ProviderBinding,
  ProviderContext,
  ProviderDescriptor,
  ProviderRequest,
  ProviderResult,
} from "./provider.js";

const PROVIDER_ID = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/;
const DEFAULT_PROCESS_TIMEOUT_MS = 30_000;
const DEFAULT_MCP_PROTOCOL_FRAME_BYTES = 1024 * 1024;
const DEFAULT_MCP_PROTOCOL_BYTES = 8 * 1024 * 1024;
const MAX_MCP_PROTOCOL_FRAME_BYTES = 1024 * 1024;
const MAX_MCP_PROTOCOL_BYTES = 8 * 1024 * 1024;
const MAX_PROCESS_OUTPUT_BYTES = 1024 * 1024;
const PROCESS_PROVIDER_CLEANUP_MS = 25;

type BoundedSettlement<T> =
  | Readonly<{ readonly status: "resolved"; readonly value: T }>
  | Readonly<{ readonly status: "failed" | "timed_out" }>;

async function settleWithin<T>(promise: Promise<T>, milliseconds: number): Promise<BoundedSettlement<T>> {
  let timer: NodeJS.Timeout | undefined;
  const timeout = new Promise<BoundedSettlement<T>>((resolveTimeout) => {
    timer = setTimeout(
      () => resolveTimeout(Object.freeze({ status: "timed_out" })),
      milliseconds,
    );
  });
  try {
    return await Promise.race([
      promise.then(
        (value) => Object.freeze({ status: "resolved" as const, value }),
        () => Object.freeze({ status: "failed" as const }),
      ),
      timeout,
    ]);
  } finally {
    if (timer !== undefined) clearTimeout(timer);
  }
}

export interface ProcessMcpClient {
  connect(process: SpawnedProcess, maxFrameBytes: number, maxProtocolBytes?: number): Promise<void>;
  callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }): Promise<unknown>;
  close(): Promise<void>;
}

export interface ProcessMcpClientFactory {
  create(): ProcessMcpClient;
}

function captureProcessClient(client: ProcessMcpClient): ProcessMcpClient {
  try {
    const connect = client.connect;
    const callTool = client.callTool;
    const close = client.close;
    if (typeof connect !== "function" || typeof callTool !== "function" || typeof close !== "function") {
      throw new Error("invalid client");
    }
    return Object.freeze({
      connect: connect.bind(client),
      callTool: callTool.bind(client),
      close: close.bind(client),
    });
  } catch {
    throw new Error("mcp_client_invalid");
  }
}

class SpawnedProcessTransport implements Transport {
  onclose?: () => void;
  onerror?: (error: Error) => void;
  onmessage?: (message: JSONRPCMessage) => void;
  readonly #process: SpawnedProcess;
  readonly #maxFrameBytes: number;
  readonly #maxProtocolBytes: number;
  #protocolBytes = 0;
  #started = false;
  #closed = false;

  constructor(process: SpawnedProcess, maxFrameBytes: number, maxProtocolBytes: number) {
    this.#process = process;
    this.#maxFrameBytes = maxFrameBytes;
    this.#maxProtocolBytes = maxProtocolBytes;
  }

  async start(): Promise<void> {
    if (this.#started) throw new Error("mcp_transport_started");
    if (this.#process.writeStdin === undefined) throw new Error("mcp_transport_unavailable");
    this.#started = true;
    void this.#readMessages();
  }

  async #readMessages(): Promise<void> {
    let buffer = Buffer.alloc(0);
    try {
      for await (const chunk of this.#process.stdout) {
        const bytes = typeof chunk === "string" ? Buffer.from(chunk) : Buffer.from(chunk);
        this.#protocolBytes += bytes.byteLength;
        if (this.#protocolBytes > this.#maxProtocolBytes) {
          throw new Error("mcp_protocol_budget_exceeded");
        }
        buffer = Buffer.concat([buffer, bytes]);
        if (buffer.byteLength > this.#maxFrameBytes && buffer.indexOf("\n") === -1) {
          throw new Error("mcp_protocol_frame_too_large");
        }
        let newline = buffer.indexOf("\n");
        while (newline !== -1) {
          if (newline > this.#maxFrameBytes) throw new Error("mcp_protocol_frame_too_large");
          const line = buffer.toString("utf8", 0, newline).replace(/\r$/u, "");
          buffer = buffer.subarray(newline + 1);
          if (line.length !== 0) this.onmessage?.(deserializeMessage(line));
          newline = buffer.indexOf("\n");
        }
      }
      if (buffer.toString("utf8").trim().length !== 0) {
        throw new Error("mcp_protocol_incomplete_frame");
      }
    } catch {
      this.onerror?.(new Error("mcp_process_failed"));
    } finally {
      if (!this.#closed) this.onclose?.();
    }
  }

  async send(message: JSONRPCMessage): Promise<void> {
    const write = this.#process.writeStdin;
    if (!this.#started || this.#closed || write === undefined) {
      throw new Error("mcp_transport_unavailable");
    }
    await write(serializeMessage(message));
  }

  async close(): Promise<void> {
    if (this.#closed) return;
    this.#closed = true;
    try {
      await this.#process.closeStdin?.();
    } finally {
      this.onclose?.();
    }
  }
}

class SdkProcessMcpClient implements ProcessMcpClient {
  #client: Client | undefined;

  async connect(
    process: SpawnedProcess,
    maxFrameBytes: number,
    maxProtocolBytes = DEFAULT_MCP_PROTOCOL_BYTES,
  ): Promise<void> {
    const client = new Client({ name: "rowboat-openai-plugin-runtime", version: "1.0.0" });
    await client.connect(new SpawnedProcessTransport(process, maxFrameBytes, maxProtocolBytes));
    this.#client = client;
  }

  async callTool(input: {
    readonly name: string;
    readonly arguments: Readonly<Record<string, unknown>>;
  }): Promise<unknown> {
    if (this.#client === undefined) throw new Error("mcp_client_not_connected");
    return this.#client.callTool({ name: input.name, arguments: input.arguments });
  }

  async close(): Promise<void> {
    const client = this.#client;
    this.#client = undefined;
    if (client !== undefined) await client.close();
  }
}

class SdkProcessMcpClientFactory implements ProcessMcpClientFactory {
  create(): ProcessMcpClient {
    return new SdkProcessMcpClient();
  }
}

function denialReason(
  parentLicense: string | undefined,
  capability: "mcp_process" | "read" | "write",
  policy: PluginPolicy,
): string | undefined {
  const decision = evaluateComponentAdmission(parentLicense, { kind: capability }, policy);
  return decision.status === "admitted" ? undefined : decision.reason;
}

export interface ProcessMcpProviderOptions {
  readonly id: string;
  readonly binding: ProviderBinding;
  readonly server: NormalizedProcessMcp;
  readonly pluginRoot: string;
  readonly parentLicense: string | undefined;
  readonly policy: PluginPolicy;
  readonly credentialResolver: CredentialResolver;
  readonly spawner?: ProcessSpawner;
  readonly safeBaselineEnvironment?: Readonly<Record<string, string>>;
  readonly maxOutputBytes?: number;
  readonly maxProtocolFrameBytes?: number;
  readonly maxProtocolBytes?: number;
  readonly clientFactory?: ProcessMcpClientFactory;
}

export class ProcessMcpProvider implements PluginProvider {
  readonly id: string;
  readonly #descriptor: ProviderDescriptor;
  readonly #server: NormalizedProcessMcp;
  readonly #pluginRoot: string;
  readonly #parentLicense: string | undefined;
  readonly #policy: PluginPolicy;
  readonly #credentialResolver: CredentialResolver;
  readonly #spawner: ProcessSpawner;
  readonly #safeBaselineEnvironment: Readonly<Record<string, string>>;
  readonly #maxOutputBytes: number | undefined;
  readonly #maxProtocolFrameBytes: number;
  readonly #maxProtocolBytes: number;
  readonly #clientFactory: ProcessMcpClientFactory;

  constructor(options: ProcessMcpProviderOptions) {
    assertBinding(options.binding);
    if (
      !PROVIDER_ID.test(options.id)
      || options.binding.providerKind !== "mcp-process"
      || options.binding.componentDigest !== options.server.componentDigest
      || options.server.kind !== "mcp-process"
      || options.server.command.length === 0
      || options.server.command.includes("\0")
      || options.server.args.some((argument) => argument.includes("\0"))
      || new Set(options.server.environmentReferences).size !== options.server.environmentReferences.length
    ) {
      throw new Error("provider_invalid:descriptor_mismatch");
    }
    const timeout = options.server.timeoutMilliseconds ?? DEFAULT_PROCESS_TIMEOUT_MS;
    if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > MAX_PROCESS_TIMEOUT_MS) {
      throw new Error("provider_invalid:process_timeout");
    }
    const maxProtocolFrameBytes = options.maxProtocolFrameBytes ?? DEFAULT_MCP_PROTOCOL_FRAME_BYTES;
    const maxProtocolBytes = options.maxProtocolBytes ?? DEFAULT_MCP_PROTOCOL_BYTES;
    if (
      !Number.isSafeInteger(maxProtocolFrameBytes)
      || maxProtocolFrameBytes < 1
      || maxProtocolFrameBytes > MAX_MCP_PROTOCOL_FRAME_BYTES
    ) {
      throw new Error("provider_invalid:protocol_frame_limit");
    }
    if (
      !Number.isSafeInteger(maxProtocolBytes)
      || maxProtocolBytes < 1
      || maxProtocolBytes > MAX_MCP_PROTOCOL_BYTES
      || maxProtocolFrameBytes > maxProtocolBytes
    ) {
      throw new Error("provider_invalid:protocol_budget");
    }
    if (
      options.maxOutputBytes !== undefined
      && (!Number.isSafeInteger(options.maxOutputBytes)
        || options.maxOutputBytes < 1
        || options.maxOutputBytes > MAX_PROCESS_OUTPUT_BYTES)
    ) {
      throw new Error("provider_invalid:output_limit");
    }

    this.id = options.id;
    this.#descriptor = Object.freeze({ id: options.id, kind: "mcp-process", temporaryAdapter: false });
    this.#server = Object.freeze({
      ...options.server,
      args: Object.freeze([...options.server.args]),
      environmentReferences: Object.freeze([...options.server.environmentReferences]),
      timeoutMilliseconds: timeout,
    });
    this.#pluginRoot = options.pluginRoot;
    this.#parentLicense = options.parentLicense;
    this.#policy = capturePluginPolicy(options.policy);
    this.#credentialResolver = options.credentialResolver;
    this.#spawner = options.spawner ?? new NodeProcessSpawner();
    this.#safeBaselineEnvironment = captureSafeEnvironment(
      options.safeBaselineEnvironment ?? Object.freeze({ NO_COLOR: "1" }),
      Object.freeze({}),
    );
    this.#maxOutputBytes = options.maxOutputBytes;
    this.#maxProtocolFrameBytes = maxProtocolFrameBytes;
    this.#maxProtocolBytes = maxProtocolBytes;
    this.#clientFactory = options.clientFactory ?? new SdkProcessMcpClientFactory();
    Object.freeze(this);
  }

  describe(): ProviderDescriptor {
    return this.#descriptor;
  }

  async invoke(request: ProviderRequest, _context: ProviderContext): Promise<ProviderResult> {
    const providerDenial = denialReason(this.#parentLicense, "mcp_process", this.#policy);
    if (providerDenial !== undefined) throw new Error(providerDenial);
    const capabilityDenial = denialReason(this.#parentLicense, request.capability, this.#policy);
    if (capabilityDenial !== undefined) throw new Error(capabilityDenial);
    const operationName = validateMcpInvocation(this.#server.name, request);

    const resolvedEnvironment: Record<string, string> = Object.create(null) as Record<string, string>;
    const secrets: string[] = [];
    for (const referenceName of this.#server.environmentReferences) {
      const reference = Object.freeze({ kind: "environment" as const, reference: referenceName });
      assertCredentialRequest(reference, request.projectId);
      try {
        const secret = await this.#credentialResolver.resolve(reference, request.projectId);
        const value = revealSecretValue(secret);
        resolvedEnvironment[referenceName] = value;
        secrets.push(value);
      } catch {
        throw new Error("credential_missing");
      }
    }

    const cwd = await resolveSafeWorkingDirectory(
      this.#pluginRoot,
      this.#server.workingDirectory ?? ".",
    );
    const environment = captureSafeEnvironment(this.#safeBaselineEnvironment, resolvedEnvironment);
    const revalidatedCwd = await resolveSafeWorkingDirectory(
      this.#pluginRoot,
      this.#server.workingDirectory ?? ".",
    );
    if (cwd !== revalidatedCwd) throw new Error("path_escape");

    let spawned: SpawnedProcess;
    try {
      spawned = this.#spawner.spawn(
        this.#server.command,
        this.#server.args,
        Object.freeze({ shell: false, cwd: revalidatedCwd, env: environment }),
      );
    } catch {
      return Object.freeze({ status: "failed", reason: "process_failed" });
    }

    let client: ProcessMcpClient;
    try {
      client = captureProcessClient(this.#clientFactory.create());
    } catch {
      await terminateSpawnedProcess(spawned);
      return Object.freeze({ status: "failed", reason: "process_failed" });
    }
    const stderrPromise = collectBoundedOutput(
      spawned.stderr,
      this.#maxOutputBytes ?? 64 * 1024,
      Object.freeze(secrets),
    );
    let timer: NodeJS.Timeout | undefined;
    const timeout = new Promise<"timeout">((resolveTimeout) => {
      timer = setTimeout(() => resolveTimeout("timeout"), this.#server.timeoutMilliseconds);
    });
    const operation = (async (): Promise<unknown> => {
      await client.connect(spawned, this.#maxProtocolFrameBytes, this.#maxProtocolBytes);
      return client.callTool({ name: operationName, arguments: request.arguments });
    })();

    let output: unknown;
    let status: "success" | "failed" | "timed_out" = "failed";
    try {
      const outcome = await Promise.race([
        operation.then(
          (value) => Object.freeze({ kind: "success" as const, value }),
          () => Object.freeze({ kind: "failed" as const }),
        ),
        timeout.then(() => Object.freeze({ kind: "timed_out" as const })),
      ]);
      if (outcome.kind === "success") {
        status = "success";
        output = outcome.value;
      } else {
        status = outcome.kind;
      }
    } finally {
      if (timer !== undefined) clearTimeout(timer);
      if (status !== "success") await terminateSpawnedProcess(spawned);
      const close = await settleWithin(
        Promise.resolve().then(() => client.close()),
        PROCESS_PROVIDER_CLEANUP_MS,
      );
      if (close.status !== "resolved" && status === "success") {
        status = "failed";
      }
      if (status === "success") await terminateSpawnedProcess(spawned);
    }

    const stderrSettlement = await settleWithin(stderrPromise, PROCESS_PROVIDER_CLEANUP_MS);
    const stderr = stderrSettlement.status === "resolved" ? stderrSettlement.value : undefined;
    if (stderrSettlement.status !== "resolved" && status === "success") {
      status = "failed";
    }
    if (status === "timed_out") {
      return Object.freeze({ status: "failed", reason: "process_timed_out" });
    }
    if (status === "failed") {
      return Object.freeze({ status: "failed", reason: "process_failed" });
    }
    return Object.freeze({
      status: "success",
      output: Object.freeze({
        result: output,
        ...(stderr === undefined
          ? {}
          : { stderr: Object.freeze({
              present: stderr.text.length > 0,
              truncated: stderr.truncated,
              digest: stderr.digest,
            }) }),
      }),
    });
  }
}

export type { ProcessSpawner, SafeSpawnOptions, SpawnedProcess } from "../process/safe-process.js";
