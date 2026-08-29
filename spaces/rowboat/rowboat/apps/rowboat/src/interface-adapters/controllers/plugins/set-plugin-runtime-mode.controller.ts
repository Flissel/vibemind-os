import type { PluginApiIdentity, IPluginApiAuthorizationPolicy } from "@/src/application/policies/plugin-api-authorization.policy";
import type { SetPluginRuntimeModeUseCase } from "@/src/application/use-cases/plugins/set-plugin-runtime-mode.use-case";
import { captureCallerSignal, captureRecord } from "./plugin-controller.shared";

const INPUT_KEYS = Object.freeze(["projectId", "mode", "expectedRevision", "catalogDigest", "migrationRecordId", "parityReceiptId", "callerSignal"]);

function requestInvalid(): never {
  throw new Error("plugin_runtime_request_invalid");
}

/**
 * Evidence references are explicit: a transition that carries none sends null,
 * so an omitted field can never be mistaken for absent evidence that was meant
 * to be supplied.
 */
function optionalReference(value: unknown): string | undefined {
  if (value === null) return undefined;
  if (typeof value !== "string") requestInvalid();
  return value;
}

export class SetPluginRuntimeModeController {
  constructor(private readonly dependencies: { readonly authorization: IPluginApiAuthorizationPolicy; readonly useCase: SetPluginRuntimeModeUseCase }) {}

  async execute(request: Request, input: unknown): Promise<unknown> {
    const selected = captureRecord(input, INPUT_KEYS);
    if (typeof selected.projectId !== "string") throw new Error("project_id_invalid");
    const mode = selected.mode;
    if (mode !== "legacy" && mode !== "shadow" && mode !== "openai") requestInvalid();
    const expectedRevision = selected.expectedRevision;
    if (typeof expectedRevision !== "number") requestInvalid();
    const catalogDigest = optionalReference(selected.catalogDigest);
    const migrationRecordId = optionalReference(selected.migrationRecordId);
    const parityReceiptId = optionalReference(selected.parityReceiptId);
    captureCallerSignal(selected.callerSignal);
    const identity: PluginApiIdentity = await this.dependencies.authorization.authenticate(request);
    return this.dependencies.useCase.execute({
      identity, projectId: selected.projectId, mode, expectedRevision,
      ...(catalogDigest === undefined ? {} : { catalogDigest }),
      ...(migrationRecordId === undefined ? {} : { migrationRecordId }),
      ...(parityReceiptId === undefined ? {} : { parityReceiptId }),
    });
  }
}
