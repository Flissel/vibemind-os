import { Ablehnung, beantworte, TOOLS, ERR, type WerkzeugKontext } from "./rpc";

let ok = 0, fehlgeschlagen = 0;
function pruefe(name: string, bedingung: boolean, detail = "") {
  if (bedingung) { ok++; console.log("  ok   " + name); }
  else { fehlgeschlagen++; console.log("  FEHL " + name + (detail ? "  -> " + detail : "")); }
}

/** Ein Kontext, dessen vier Funktionen alle dasselbe liefern — oder dasselbe werfen. */
const kontextMit = (schluessel: string, liefert: unknown, wirft?: unknown): WerkzeugKontext => {
  const lauf = async (...gesehen: unknown[]) => {
    if (wirft !== undefined) throw wirft;
    return { liefert, gesehen };
  };
  return {
    apiKey: schluessel,
    quellenAuflisten: lauf,
    quelleLesen: lauf,
    dokumenteAuflisten: lauf,
    dateiUrl: lauf,
  };
};

const K = () => kontextMit("schluessel-x", [{ id: "1", name: "Quelle A", status: "ready", active: true }]);
const aufruf = (id: number, name: string, args: Record<string, unknown>) =>
  ({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } });
const inhalt = (r: unknown) => JSON.parse((r as any).result.content[0].text);

console.log("=== Auth ===");
let r = await beantworte({ jsonrpc: "2.0", id: 1, method: "tools/list" }, kontextMit("", []));
pruefe("ohne Schluessel keine Werkzeugliste", r.error?.code === ERR.UNAUTHORIZED, JSON.stringify(r));

console.log("=== Protokoll ===");
r = await beantworte({ jsonrpc: "2.0", id: 2, method: "initialize" }, K());
pruefe("initialize liefert serverInfo", (r.result as any)?.serverInfo?.name === "rowboat");
r = await beantworte({ jsonrpc: "2.0", id: 3, method: "tools/list" }, K());
pruefe("tools/list nennt alle Werkzeuge", (r.result as any)?.tools?.length === TOOLS.length);
pruefe("es sind vier", TOOLS.length === 4, String(TOOLS.length));
pruefe("jedes Werkzeug hat genau einen Pflichtparameter",
  TOOLS.every((t) => t.inputSchema.required.length === 1));
r = await beantworte({ jsonrpc: "2.0", id: 4, method: "gibtsnicht" }, K());
pruefe("unbekannte Methode -> METHOD_NOT_FOUND", r.error?.code === ERR.METHOD_NOT_FOUND);
r = await beantworte({ kaputt: true }, K());
pruefe("kein JSON-RPC -> INVALID_REQUEST", r.error?.code === ERR.INVALID_REQUEST);

console.log("=== Werkzeugaufruf: Verteilung ===");
r = await beantworte(aufruf(5, "rowboat_wissensquellen", { projectId: "p1" }), K());
pruefe("wissensquellen: Erfolg liefert content",
  (r.result as any)?.isError === false && (r.result as any)?.content?.[0]?.text?.includes("Quelle A"));
pruefe("wissensquellen: projectId und Schluessel kommen an",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["p1", "schluessel-x"]), JSON.stringify(inhalt(r).gesehen));

r = await beantworte(aufruf(6, "rowboat_wissensquelle", { sourceId: "s1" }), K());
pruefe("wissensquelle: sourceId kommt an",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["s1", "schluessel-x"]), JSON.stringify(r));

r = await beantworte(aufruf(7, "rowboat_dokumente", { sourceId: "s1" }), K());
pruefe("dokumente: ohne mitInhalt -> false",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["s1", "schluessel-x", false]), JSON.stringify(r));
r = await beantworte(aufruf(8, "rowboat_dokumente", { sourceId: "s1", mitInhalt: true }), K());
pruefe("dokumente: mitInhalt=true kommt an",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["s1", "schluessel-x", true]));
r = await beantworte(aufruf(9, "rowboat_dokumente", { sourceId: "s1", mitInhalt: "ja" }), K());
pruefe("dokumente: mitInhalt='ja' zaehlt NICHT als true",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["s1", "schluessel-x", false]));

r = await beantworte(aufruf(10, "rowboat_datei_url", { fileId: "f1" }), K());
pruefe("datei_url: fileId kommt an",
  JSON.stringify(inhalt(r).gesehen) === JSON.stringify(["f1", "schluessel-x"]));

console.log("=== Werkzeugaufruf: Pflichtparameter ===");
for (const [name, pflicht] of [
  ["rowboat_wissensquellen", "projectId"],
  ["rowboat_wissensquelle", "sourceId"],
  ["rowboat_dokumente", "sourceId"],
  ["rowboat_datei_url", "fileId"],
] as const) {
  r = await beantworte(aufruf(11, name, {}), K());
  pruefe(`${name}: fehlender ${pflicht} -> INVALID_PARAMS`,
    r.error?.code === ERR.INVALID_PARAMS && r.error.message.includes(pflicht), JSON.stringify(r.error));
  r = await beantworte(aufruf(12, name, { [pflicht]: "" }), K());
  pruefe(`${name}: leerer ${pflicht} -> INVALID_PARAMS`, r.error?.code === ERR.INVALID_PARAMS);
}
r = await beantworte(aufruf(13, "fremdes_werkzeug", { projectId: "p1" }), K());
pruefe("unbekanntes Werkzeug abgelehnt", r.error?.code === ERR.METHOD_NOT_FOUND);

console.log("=== Fehlerarten ===");
r = await beantworte(aufruf(14, "rowboat_wissensquelle", { sourceId: "s1" }),
  kontextMit("k", null, new Ablehnung("nicht gefunden")));
pruefe("Ablehnung nennt ihre Art",
  (r.result as any)?.isError === true && (r.result as any)?.content?.[0]?.text === "Aufruf abgelehnt (nicht gefunden)",
  JSON.stringify(r));
r = await beantworte(aufruf(15, "rowboat_wissensquelle", { sourceId: "s1" }),
  kontextMit("k", null, new TypeError("Cannot read properties of undefined")));
pruefe("fremder Fehler wird nur als 'Fehler' gemeldet, ohne Wortlaut",
  (r.result as any)?.content?.[0]?.text === "Aufruf abgelehnt (Fehler)", JSON.stringify(r));

console.log("=== Der wichtigste Test: Schluessel darf nie in einer Meldung stehen ===");
for (const name of ["rowboat_wissensquellen", "rowboat_wissensquelle", "rowboat_dokumente", "rowboat_datei_url"]) {
  r = await beantworte(aufruf(16, name, { projectId: "p1", sourceId: "s1", fileId: "f1" }),
    kontextMit("geheim-123", null, new Error("Bearer geheim-123 abgelehnt")));
  const text = JSON.stringify(r);
  pruefe(`${name}: Ausnahme -> isError`, (r.result as any)?.isError === true);
  pruefe(`${name}: Schluessel steht NICHT in der Antwort`, !text.includes("geheim-123"), text);
}

console.log(`\n${ok} ok, ${fehlgeschlagen} fehlgeschlagen`);
process.exit(fehlgeschlagen === 0 ? 0 : 1);
