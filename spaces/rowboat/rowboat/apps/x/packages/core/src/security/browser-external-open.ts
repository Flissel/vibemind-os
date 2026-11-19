// Modified for VibeMind (Flissel/rowboat vibemind): new module (ticket #330,
// fix round 3, review m5). The agent drives the embedded browser
// (browser-control); a page there that opens a non-web URL used to reach
// shell.openExternal without a prompt — rowboat:// deep links (e.g.
// take-meeting-notes), file:, ms-settings:, search-ms: and every other OS
// protocol handler. Only mailto: is still handed to the OS; everything else
// is dropped (the caller logs it).

export function externalOpenDecision(url: string): "open" | "drop" {
    let parsed: URL;
    try {
        parsed = new URL(url.trim());
    } catch {
        return "drop";
    }
    return parsed.protocol === "mailto:" ? "open" : "drop";
}
