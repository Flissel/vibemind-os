const ACTION_NAME = /^[A-Z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$/u;
const FORBIDDEN_ACTION_NAMES = new Set(["constructor", "prototype", "__proto__"]);

export interface CompiledHookMatcher {
  readonly source: string;
  matches(actionName: unknown): boolean;
}

export function compileHookMatcher(source: unknown): CompiledHookMatcher {
  if (typeof source !== "string" || source.length === 0 || source.length > 512) {
    throw new Error("component_unsupported");
  }
  const alternatives = source.split("|");
  if (
    alternatives.length === 0
    || alternatives.length > 32
    || alternatives.some((name) => !ACTION_NAME.test(name) || FORBIDDEN_ACTION_NAMES.has(name))
    || new Set(alternatives).size !== alternatives.length
  ) {
    throw new Error("component_unsupported");
  }
  const captured = Object.freeze([...alternatives]);
  return Object.freeze({
    source,
    matches(actionName: unknown): boolean {
      return typeof actionName === "string" && captured.includes(actionName);
    },
  });
}
