import { beantworte, TOOLS, ERR, type WerkzeugKontext } from "./rpc";

let ok = 0, fehlgeschlagen = 0;
function pruefe(name: string, bedingung: boolean, detail = "") {
  if (bedingung) { ok++; console.log("  ok   " + name); }
  else { fehlgeschlagen++; console.log("  FEHL " + name + (detail ? "  -> " + detail : "")); }
}

const kontextMit = (schluessel: string, liefert: unknown, wirft = false): WerkzeugKontext => ({
  apiKey: schluessel,
  async quellenAuflisten() { if (wirft) throw new Error("Bearer geheim-123 abgelehnt"); return liefert; },
});

const K = () => kontextMit("schluessel-x", [{ id: "1", name: "Quelle A", status: "ready", active: true }]);

console.log("=== Auth ===");
let r = await beantworte({ jsonrpc: "2.0", id: 1, method: "tools/list" }, kontextMit("", []));
pruefe("ohne Schluessel keine Werkzeugliste", r.error?.code === ERR.UNAUTHORIZED, JSON.stringify(r));

console.log("=== Protokoll ===");
r = await beantworte({ jsonrpc: "2.0", id: 2, method: "initialize" }, K());
pruefe("initialize liefert serverInfo", (r.result as any)?.serverInfo?.name === "rowboat");
r = await beantworte({ jsonrpc: "2.0", id: 3, method: "tools/list" }, K());
pruefe("tools/list nennt 1 Werkzeug", (r.result as any)?.tools?.length === TOOLS.length);
r = await beantworte({ jsonrpc: "2.0", id: 4, method: "gibtsnicht" }, K());
pruefe("unbekannte Methode -> METHOD_NOT_FOUND", r.error?.code === ERR.METHOD_NOT_FOUND);
r = await beantworte({ kaputt: true }, K());
pruefe("kein JSON-RPC -> INVALID_REQUEST", r.error?.code === ERR.INVALID_REQUEST);

console.log("=== Werkzeugaufruf ===");
r = await beantworte({ jsonrpc: "2.0", id: 5, method: "tools/call",
  params: { name: "rowboat_wissensquellen", arguments: { projectId: "p1" } } }, K());
pruefe("Erfolg liefert content", (r.result as any)?.isError === false && (r.result as any)?.content?.[0]?.text?.includes("Quelle A"));
r = await beantworte({ jsonrpc: "2.0", id: 6, method: "tools/call",
  params: { name: "rowboat_wissensquellen", arguments: {} } }, K());
pruefe("fehlende projectId -> INVALID_PARAMS", r.error?.code === ERR.INVALID_PARAMS);
r = await beantworte({ jsonrpc: "2.0", id: 7, method: "tools/call",
  params: { name: "fremdes_werkzeug", arguments: { projectId: "p1" } } }, K());
pruefe("unbekanntes Werkzeug abgelehnt", r.error?.code === ERR.METHOD_NOT_FOUND);

console.log("=== Der wichtigste Test: Schluessel darf nie in einer Meldung stehen ===");
r = await beantworte({ jsonrpc: "2.0", id: 8, method: "tools/call",
  params: { name: "rowboat_wissensquellen", arguments: { projectId: "p1" } } },
  kontextMit("geheim-123", null, true));
const text = JSON.stringify(r);
pruefe("Ausnahme wird als isError gemeldet", (r.result as any)?.isError === true);
pruefe("Schluessel steht NICHT in der Antwort", !text.includes("geheim-123"), text);

console.log(`\n${ok} ok, ${fehlgeschlagen} fehlgeschlagen`);
process.exit(fehlgeschlagen === 0 ? 0 : 1);
