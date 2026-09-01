"use client";

import React, { useState, useTransition, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { Modal, ModalBody, ModalContent, ModalFooter, ModalHeader } from "@heroui/react";
import { installPluginAction, previewPluginInstallationAction } from "@/app/actions/plugin.actions";
import type { PluginUiPreview } from "@/src/interface-adapters/actions/plugin-action-runtime";

/**
 * Installation is component-scoped: the operator picks the components, and only
 * a component the server reports as available and pinned by a digest can be
 * picked. The rest stay visible with their policy reason, so the dialog never
 * hides what the catalog decided.
 */
export function toPluginInstallDialogView(preview: PluginUiPreview) {
  const components = preview.components.map(({ componentDigest, name, kind, status, reason }) => Object.freeze({
    ...(componentDigest === undefined ? {} : { componentDigest }),
    name, kind, status,
    selectable: componentDigest !== undefined && status === "available",
    ...(reason === undefined ? {} : { reason }),
  }));
  const defaultSelection = components
    .filter((component) => component.selectable)
    .map((component) => component.componentDigest as string)
    .sort();
  return Object.freeze({
    pluginName: preview.pluginName,
    canInstall: defaultSelection.length > 0,
    components: Object.freeze(components),
    defaultSelection: Object.freeze(defaultSelection),
    credentialSlots: Object.freeze(preview.credentialSlots.map(({ name, configured }) => Object.freeze({ name, configured }))),
  });
}

export function PluginInstallDialogFrame({ onClose, children }: {
  readonly onClose: () => void;
  readonly children: ReactNode;
}) {
  return (
    <Modal
      isOpen
      onClose={onClose}
      isDismissable
      isKeyboardDismissDisabled={false}
      shouldBlockScroll
      placement="center"
      scrollBehavior="inside"
    >
      {children}
    </Modal>
  );
}

function safeError(error: unknown): string {
  return error instanceof Error && Object.getPrototypeOf(error) === Error.prototype ? error.message : "internal_error";
}

export function PluginInstallDialog({ projectId, preview, onClose }: {
  readonly projectId: string;
  readonly preview: PluginUiPreview;
  readonly onClose: () => void;
}) {
  const view = toPluginInstallDialogView(preview);
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<readonly string[]>(view.defaultSelection);

  const toggle = (componentDigest: string) => setSelected((current) => (current.includes(componentDigest)
    ? current.filter((value) => value !== componentDigest)
    : [...current, componentDigest].sort()));

  // The envelope in hand authorizes the selection the dialog opened with, so a
  // changed selection needs its own server-signed preview before it can install -
  // and that re-signing is handed the reviewed envelope, so it refuses whenever a
  // decision drifted while the dialog was open instead of quietly re-pinning it.
  const install = () => startTransition(async () => {
    setError(null);
    try {
      const authorized = await previewPluginInstallationAction({
        projectId, pluginName: preview.pluginName, catalogDigest: preview.catalogDigest, componentDigests: selected,
        priorPreviewToken: preview.previewToken,
      });
      await installPluginAction({ previewToken: authorized.previewToken, componentDigests: selected });
      router.refresh();
      onClose();
    } catch (caught) { setError(safeError(caught)); }
  });

  return (
    <PluginInstallDialogFrame onClose={onClose}>
      <ModalContent>
        <ModalHeader>Install {view.pluginName}</ModalHeader>
        <ModalBody>
          <p className="text-sm text-zinc-500">Review the server-authorized component decisions and credential requirements before installing. Only the components you select are installed.</p>
          <h3 className="mt-2 font-medium">Component decisions</h3>
        <ul className="mt-2 space-y-2">
          {view.components.map((component) => (
            <li key={component.componentDigest ?? `${component.kind}:${component.name}`} className="rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-800">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  disabled={!component.selectable || pending}
                  checked={component.componentDigest !== undefined && selected.includes(component.componentDigest)}
                  onChange={() => component.componentDigest !== undefined && toggle(component.componentDigest)}
                />
                <span>
                  <span className="font-medium">{component.name}</span> · {component.kind} · {component.status}
                  {component.reason !== undefined && <span className="block break-all text-amber-700 dark:text-amber-300">{component.reason}</span>}
                </span>
              </label>
            </li>
          ))}
        </ul>
        <h3 className="mt-5 font-medium">Credential requirements</h3>
        {view.credentialSlots.length === 0 ? <p className="mt-2 text-sm text-zinc-500">No credential slots required.</p> : (
          <ul className="mt-2 space-y-2">
            {view.credentialSlots.map((slot) => <li key={slot.name} className="text-sm"><code>{slot.name}</code> — {slot.configured ? "configured" : "required"}</li>)}
          </ul>
        )}
        {error !== null && <p role="alert" className="mt-4 break-all text-sm text-red-700 dark:text-red-300">{error}</p>}
        </ModalBody>
        <ModalFooter>
          <button type="button" onClick={onClose} disabled={pending} className="rounded-md border px-3 py-2 text-sm">Cancel</button>
          <button type="button" onClick={install} disabled={pending || !view.canInstall || selected.length === 0} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-zinc-400">
            {pending ? "Installing…" : "Install"}
          </button>
        </ModalFooter>
      </ModalContent>
    </PluginInstallDialogFrame>
  );
}
