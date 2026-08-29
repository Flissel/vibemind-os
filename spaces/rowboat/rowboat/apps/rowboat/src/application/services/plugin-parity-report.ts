import { buildReceipt, type PluginReceipt } from "@rowboat/openai-plugin-runtime";
import { migrationDigest } from "../use-cases/plugins/plugin-migration.shared";
import { captureMigrationJson } from "./legacy-plugin-migration";

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const MAX_DESCRIPTORS = 512;
const MAX_DESCRIPTOR_DEPTH = 16;
const SOURCE_KEYS = Object.freeze(["agents", "prompts", "providers", "tools"]);

/**
 * Receipt paths whose values are never persisted. The evidence keeps a digest
 * of each so a later reader can prove which values were compared without the
 * receipt ever carrying an argument, a result, or a credential derived from one.
 */
export const PARITY_SENSITIVE_PATHS: readonly string[] = Object.freeze([
  "output.activeResult", "output.arguments", "output.shadowResult",
]);

export class PluginParityError extends Error {
  readonly code: "parity_source_invalid" | "parity_request_invalid";

  constructor(code: PluginParityError["code"]) {
    super(code);
    this.name = "PluginParityError";
    this.code = code;
  }
}

export type ParityDimension = "agent" | "prompt" | "tool" | "provider";
export type ParityDifferenceCode = "descriptor_mismatch" | "missing_in_legacy" | "missing_in_plugin";

export const PARITY_DIMENSIONS: readonly ParityDimension[] = Object.freeze(["agent", "prompt", "tool", "provider"] as const);
const DIMENSION_SOURCE = Object.freeze({ agent: "agents", prompt: "prompts", tool: "tools", provider: "providers" } as const);
const DIFFERENCE_ORDER = Object.freeze(["descriptor_mismatch", "missing_in_legacy", "missing_in_plugin"] as const);

export type ParityDescriptorInput = Readonly<Record<string, unknown>>;

export interface ParityRepresentationInput {
  readonly agents: readonly ParityDescriptorInput[];
  readonly prompts: readonly ParityDescriptorInput[];
  readonly tools: readonly ParityDescriptorInput[];
  readonly providers: readonly ParityDescriptorInput[];
}

export interface ParityDescriptor {
  readonly id: string;
  readonly digest: string;
}

export interface ParityRepresentation {
  readonly dimensions: Readonly<Record<ParityDimension, readonly ParityDescriptor[]>>;
  readonly digest: string;
}

export interface ParityDifference {
  readonly dimension: ParityDimension;
  readonly code: ParityDifferenceCode;
  readonly id: string;
}

export interface ParityComparison {
  readonly dimensions: readonly ParityDimension[];
  readonly differences: readonly ParityDifference[];
  readonly matched: boolean;
  readonly legacyDigest: string;
  readonly pluginDigest: string;
}

export const EMPTY_PARITY_REPRESENTATION: ParityRepresentationInput = Object.freeze({
  agents: Object.freeze([]), prompts: Object.freeze([]), tools: Object.freeze([]), providers: Object.freeze([]),
});

function invalid(): never {
  throw new PluginParityError("parity_source_invalid");
}

/**
 * Reuses the migration capture boundary: own enumerable data properties only,
 * no getters, proxies, cycles, or prototype pollution, bounded in size.
 */
export function captureParityJson(input: unknown): unknown {
  try {
    return captureMigrationJson(input);
  } catch {
    return invalid();
  }
}

/**
 * Order-insensitive normalization. Identifier lists (agent tool names, provider
 * capabilities) are descriptor sets, so they are sorted and must be duplicate
 * free; arrays holding structures keep their declared order.
 */
function normalizeValue(value: unknown, depth: number): unknown {
  if (depth > MAX_DESCRIPTOR_DEPTH) invalid();
  if (Array.isArray(value)) {
    if (value.length > MAX_DESCRIPTORS) invalid();
    const items = value.map(item => normalizeValue(item, depth + 1));
    if (items.every(item => typeof item === "string")) {
      const strings = items as string[];
      if (new Set(strings).size !== strings.length) invalid();
      return Object.freeze([...strings].sort());
    }
    return Object.freeze(items);
  }
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<Record<string, unknown>>;
    const normalized: Record<string, unknown> = {};
    for (const key of Object.keys(record).sort()) normalized[key] = normalizeValue(record[key], depth + 1);
    return Object.freeze(normalized);
  }
  return value;
}

function normalizeDescriptor(value: unknown): ParityDescriptor {
  if (value === null || typeof value !== "object" || Array.isArray(value)) invalid();
  const record = value as Readonly<Record<string, unknown>>;
  const id = record.id;
  if (typeof id !== "string" || !IDENTIFIER.test(id)) invalid();
  return Object.freeze({ id, digest: migrationDigest("rowboat:plugin-parity-descriptor:v1", normalizeValue(record, 0)) });
}

function normalizeDimension(value: unknown): readonly ParityDescriptor[] {
  if (!Array.isArray(value) || value.length > MAX_DESCRIPTORS) invalid();
  const descriptors = value.map(descriptor => normalizeDescriptor(descriptor))
    .sort((left, right) => left.id < right.id ? -1 : left.id > right.id ? 1 : 0);
  for (let index = 1; index < descriptors.length; index += 1) {
    if (descriptors[index]!.id === descriptors[index - 1]!.id) invalid();
  }
  return Object.freeze(descriptors);
}

export function captureParityRepresentation(input: unknown): ParityRepresentation {
  const captured = captureParityJson(input);
  if (captured === null || typeof captured !== "object" || Array.isArray(captured)) invalid();
  const source = captured as Readonly<Record<string, unknown>>;
  if (Object.keys(source).sort().join("\0") !== SOURCE_KEYS.join("\0")) invalid();
  const dimensions: Record<ParityDimension, readonly ParityDescriptor[]> = Object.create(null) as Record<ParityDimension, readonly ParityDescriptor[]>;
  for (const dimension of PARITY_DIMENSIONS) dimensions[dimension] = normalizeDimension(source[DIMENSION_SOURCE[dimension]]);
  const frozen = Object.freeze(dimensions);
  const digest = migrationDigest("rowboat:plugin-parity-representation:v1",
    PARITY_DIMENSIONS.map(dimension => [dimension, frozen[dimension].map(descriptor => [descriptor.id, descriptor.digest])]));
  return Object.freeze({ dimensions: frozen, digest });
}

export function compareParityRepresentations(legacy: unknown, plugin: unknown): ParityComparison {
  const left = captureParityRepresentation(legacy);
  const right = captureParityRepresentation(plugin);
  const differences: ParityDifference[] = [];
  for (const dimension of PARITY_DIMENSIONS) {
    const legacyById = new Map(left.dimensions[dimension].map(descriptor => [descriptor.id, descriptor.digest]));
    const pluginById = new Map(right.dimensions[dimension].map(descriptor => [descriptor.id, descriptor.digest]));
    for (const [id, digest] of legacyById) {
      const counterpart = pluginById.get(id);
      if (counterpart === undefined) differences.push(Object.freeze({ dimension, code: "missing_in_plugin" as const, id }));
      else if (counterpart !== digest) differences.push(Object.freeze({ dimension, code: "descriptor_mismatch" as const, id }));
    }
    for (const id of pluginById.keys()) {
      if (!legacyById.has(id)) differences.push(Object.freeze({ dimension, code: "missing_in_legacy" as const, id }));
    }
  }
  differences.sort((left_, right_) => {
    const dimension = PARITY_DIMENSIONS.indexOf(left_.dimension) - PARITY_DIMENSIONS.indexOf(right_.dimension);
    if (dimension !== 0) return dimension;
    const code = DIFFERENCE_ORDER.indexOf(left_.code) - DIFFERENCE_ORDER.indexOf(right_.code);
    if (code !== 0) return code;
    return left_.id < right_.id ? -1 : left_.id > right_.id ? 1 : 0;
  });
  return Object.freeze({
    dimensions: PARITY_DIMENSIONS,
    differences: Object.freeze(differences),
    matched: differences.length === 0,
    legacyDigest: left.digest,
    pluginDigest: right.digest,
  });
}

export function parityOutputDigest(value: unknown): string | null {
  try {
    return migrationDigest("rowboat:plugin-parity-output:v1", captureMigrationJson(value));
  } catch {
    return null;
  }
}

export interface ParityReceiptInput {
  readonly projectId: string;
  readonly pluginName: string;
  readonly reportDigest: string;
  readonly matched: boolean;
  readonly evidence: Readonly<Record<string, unknown>>;
}

/**
 * Builds the persisted comparison evidence. The receipt identity is derived
 * from the report digest, so the same comparison is the same receipt, and every
 * argument or result path is declared and redacted before persistence.
 */
export function buildParityReceipt(input: ParityReceiptInput): PluginReceipt {
  if (typeof input.reportDigest !== "string" || !/^[a-f0-9]{64}$/.test(input.reportDigest)) throw new PluginParityError("parity_request_invalid");
  return buildReceipt(Object.freeze({
    type: "execution",
    receiptId: `parity:${input.reportDigest}`,
    projectId: input.projectId,
    pluginName: input.pluginName,
    status: input.matched ? "success" : "failed",
    ...(input.matched ? {} : { reason: "parity_failed" }),
    output: input.evidence,
  }), PARITY_SENSITIVE_PATHS, { maxOutputBytes: 16_384 });
}
