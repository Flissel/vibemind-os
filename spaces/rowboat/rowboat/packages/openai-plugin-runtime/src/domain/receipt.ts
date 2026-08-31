import type { PluginComponentKind, PluginReasonCode } from "./plugin.js";

export type PluginReceiptType = "import" | "install" | "migration" | "execution";
export type PluginReceiptStatus = "success" | "failed" | "denied" | "timed_out";

export interface TruncatedReceiptOutput {
  readonly truncated: true;
  readonly digest: string;
}

export interface PluginReceipt {
  readonly type: PluginReceiptType;
  readonly receiptId: string;
  readonly projectId: string;
  readonly pluginName: string;
  readonly status: PluginReceiptStatus;
  readonly componentKind?: PluginComponentKind;
  readonly componentName?: string;
  readonly reason?: PluginReasonCode;
  readonly temporaryAdapter?: true;
  readonly output?: unknown | TruncatedReceiptOutput;
  readonly redactions: readonly string[];
}
