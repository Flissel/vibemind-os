import { createHash } from "node:crypto";
import { isProxy } from "node:util/types";
import type {
  PluginReceipt,
  PluginReceiptStatus,
  PluginReceiptType,
} from "../domain/receipt.js";
import type { PluginComponentKind, PluginReasonCode } from "../domain/plugin.js";
import { canonicalSensitivePaths, captureRedactionLimits, redactBounded, type RedactionLimits } from "../security/redact.js";

export interface ReceiptLimits extends RedactionLimits {
  readonly maxOutputBytes?: number;
}

const TYPES = new Set<PluginReceiptType>(["import", "install", "migration", "execution"]);
const STATUSES = new Set<PluginReceiptStatus>(["success", "failed", "denied", "timed_out"]);
const KINDS = new Set<PluginComponentKind>(["skill", "agent", "command", "mcp", "app", "hook", "asset"]);
const REASONS = new Set<PluginReasonCode>([
  "source_mismatch", "manifest_invalid", "path_escape", "digest_mismatch",
  "license_review_required", "license_rejected", "provider_unavailable", "credential_missing",
  "http_mcp_not_admitted", "process_not_admitted", "hook_not_admitted", "write_review_required",
  "component_unsupported", "migration_conflict", "parity_failed", "rollback_unavailable",
]);
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

function invalid(): never {
  throw new Error("receipt_invalid");
}

function captureRecord(input: unknown): Readonly<Record<string, unknown>> {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) invalid();
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) invalid();
  const keys = Reflect.ownKeys(input);
  if (keys.length > 32 || keys.some((key) => typeof key !== "string")) invalid();
  const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined) invalid();
    if (!("value" in descriptor) || !descriptor.enumerable) invalid();
    result[key] = descriptor.value;
  }
  return Object.freeze(result);
}

function requiredIdentifier(record: Readonly<Record<string, unknown>>, key: string): string {
  const value = record[key];
  if (typeof value !== "string" || !IDENTIFIER.test(value)) invalid();
  return value;
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(value);
}

function captureReceiptLimits(input: unknown): Readonly<ReceiptLimits> {
  if (input === null || typeof input !== "object" || Array.isArray(input) || isProxy(input)) invalid();
  const prototype = Object.getPrototypeOf(input);
  if (prototype !== Object.prototype && prototype !== null) invalid();
  const allowed = new Set(["maxDepth", "maxKeys", "maxItems", "maxStringBytes", "maxTotalStringBytes", "maxOutputBytes"]);
  const keys = Reflect.ownKeys(input);
  if (keys.length > allowed.size || keys.some((key) => typeof key !== "string")) invalid();
  const captured: Record<string, number> = Object.create(null) as Record<string, number>;
  for (const key of keys as string[]) {
    const descriptor = Object.getOwnPropertyDescriptor(input, key);
    if (descriptor === undefined) invalid();
    if (!allowed.has(key) || !("value" in descriptor) || !descriptor.enumerable) invalid();
    if (!Number.isSafeInteger(descriptor.value) || (descriptor.value as number) < 0 || (descriptor.value as number) > 16 * 1024 * 1024) invalid();
    captured[key] = descriptor.value as number;
  }
  return Object.freeze(captured);
}

export function buildReceipt(
  input: unknown,
  sensitivePaths: readonly string[] = Object.freeze([]),
  limits: ReceiptLimits = {},
): PluginReceipt {
  const record = captureRecord(input);
  const capturedLimits = captureReceiptLimits(limits);
  const type = record.type;
  const status = record.status;
  if (typeof type !== "string" || !TYPES.has(type as PluginReceiptType)) invalid();
  if (typeof status !== "string" || !STATUSES.has(status as PluginReceiptStatus)) invalid();
  const receiptId = requiredIdentifier(record, "receiptId");
  const projectId = requiredIdentifier(record, "projectId");
  const pluginName = requiredIdentifier(record, "pluginName");
  const componentKind = record.componentKind;
  if (componentKind !== undefined && (typeof componentKind !== "string" || !KINDS.has(componentKind as PluginComponentKind))) invalid();
  const componentName = record.componentName;
  if (componentName !== undefined && (typeof componentName !== "string" || !IDENTIFIER.test(componentName))) invalid();
  const reason = record.reason;
  if (reason !== undefined && (typeof reason !== "string" || !REASONS.has(reason as PluginReasonCode))) invalid();
  const temporaryAdapter = record.temporaryAdapter;
  if (temporaryAdapter !== undefined && temporaryAdapter !== true) invalid();
  const allowlisted = Object.freeze({
    type, receiptId, projectId, pluginName, status,
    ...(record.componentKind === undefined ? {} : { componentKind: record.componentKind }),
    ...(record.componentName === undefined ? {} : { componentName: record.componentName }),
    ...(record.reason === undefined ? {} : { reason: record.reason }),
    ...(record.temporaryAdapter === undefined ? {} : { temporaryAdapter: record.temporaryAdapter }),
    ...(record.output === undefined ? {} : { output: record.output }),
  });
  const requestedRedactions = canonicalSensitivePaths(sensitivePaths);
  const redactionLimits = captureRedactionLimits(Object.freeze({
    ...(capturedLimits.maxDepth === undefined ? {} : { maxDepth: capturedLimits.maxDepth }),
    ...(capturedLimits.maxKeys === undefined ? {} : { maxKeys: capturedLimits.maxKeys }),
    ...(capturedLimits.maxItems === undefined ? {} : { maxItems: capturedLimits.maxItems }),
    ...(capturedLimits.maxStringBytes === undefined ? {} : { maxStringBytes: capturedLimits.maxStringBytes }),
    ...(capturedLimits.maxTotalStringBytes === undefined ? {} : { maxTotalStringBytes: capturedLimits.maxTotalStringBytes }),
  }));
  const redacted = redactBounded(allowlisted, requestedRedactions, redactionLimits);
  const redactedRecord = redacted.value as Readonly<Record<string, unknown>>;
  const output = redactedRecord.output;
  let boundedOutput: unknown = output;
  if (output !== undefined) {
    const fullBytes = Buffer.from(canonicalJson(output), "utf8");
    const maximum = capturedLimits.maxOutputBytes ?? 16_384;
    if (fullBytes.byteLength > maximum) {
      boundedOutput = Object.freeze({
        truncated: true as const,
        digest: createHash("sha256").update(fullBytes).digest("hex"),
      });
    }
  }
  return Object.freeze({
    type: type as PluginReceiptType,
    receiptId,
    projectId,
    pluginName,
    status: status as PluginReceiptStatus,
    ...(componentKind === undefined ? {} : { componentKind: componentKind as PluginComponentKind }),
    ...(componentName === undefined ? {} : { componentName }),
    ...(reason === undefined ? {} : { reason: reason as PluginReasonCode }),
    ...(temporaryAdapter === true ? { temporaryAdapter: true as const } : {}),
    ...(output === undefined ? {} : { output: boundedOutput }),
    redactions: Object.freeze([...new Set([...requestedRedactions, ...redacted.paths])].sort()),
  });
}
