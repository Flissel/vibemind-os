import { AppDeclarationSchema } from "../schema/component-schemas.js";

const DIGEST = /^[a-f0-9]{64}$/;
const COMPONENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._ -]*$/;

export interface NormalizedApp {
  readonly name: string;
  readonly kind: "app";
  readonly connectorId: string;
  readonly capabilities: readonly ("read" | "write")[];
  readonly componentDigest: string;
}

export function normalizeApp(name: string, input: unknown, componentDigest: string): NormalizedApp {
  const parsed = AppDeclarationSchema.safeParse(input);
  if (!COMPONENT_NAME.test(name)) throw new Error("component_invalid:app_name");
  if (!DIGEST.test(componentDigest)) throw new Error("component_invalid:component_digest");
  if (!parsed.success) throw new Error("component_invalid:app_descriptor");

  return Object.freeze({
    name,
    kind: "app",
    connectorId: parsed.data.id,
    capabilities: Object.freeze([...(parsed.data.capabilities ?? [])]),
    componentDigest,
  });
}
