/**
 * JSON-RPC-Kern des MCP-Endpunkts — bewusst ohne HTTP, damit er testbar ist.
 *
 * WARUM HANDGESCHRIEBEN UND NICHT DAS SDK-TRANSPORT. `@modelcontextprotocol/sdk`
 * liegt im Projekt und bringt `StreamableHTTPServerTransport` mit, der aber auf
 * Node-`req`/`res` gebaut ist. Der App-Router von Next.js reicht Web-Standard-
 * `Request`/`Response` durch; die beiden Welten zu verheiraten kostet mehr
 * Fläche als die drei Methoden, die ein reiner Werkzeug-Server braucht. Der
 * stdio-Server des Spaces (`spaces/rowboat/mcp_server.py`) ist aus demselben
 * Grund handgeschrieben.
 *
 * WAS DIESER SERVER NICHT KANN, und zwar absichtlich: keine server-initiierten
 * Nachrichten, kein SSE, keine Sitzungen. Ein Werkzeug-Server antwortet auf
 * Anfragen und schweigt sonst — damit genügt POST -> JSON, und Clients, die
 * `streamable-http` sprechen (openclaw tut das gegen `sales-mcp:8765/mcp`),
 * kommen damit zurecht.
 *
 * DIE PROJEKTBINDUNG entsteht NICHT hier. `projectId` bzw. `sourceId`/`fileId`
 * sind Aufrufparameter, aber `ProjectActionAuthorizationPolicy` im Use-Case
 * prüft, ob der mitgeschickte Schlüssel zum Projekt der Entität gehört —
 * dieselbe Kante, die auch die Chat-Route trägt. Eine erratene Quell-ID nützt
 * ohne den passenden Schlüssel nichts (geprüft am 01.09.2026: alle drei
 * Lese-Use-Cases holen erst die Entität und autorisieren gegen DEREN projectId).
 *
 * FEHLERARTEN. Rowboats Fehlerklassen setzen kein `name`; im Produktions-Build
 * bleibt vom Klassennamen nur ein Buchstabe übrig (live gesehen: „(a)"). Die
 * Route übersetzt deshalb in `Ablehnung` mit einer festen Art — nur die kommt
 * nach außen, nie ein fremder Fehlertext, der den Schlüssel enthalten könnte.
 */
import { z } from "zod";

export const PROTOCOL_VERSION = "2025-06-18";
export const SERVER_NAME = "rowboat";
export const SERVER_VERSION = "0.2.0";

/** Die Arten, in denen ein Werkzeug scheitern darf. Sonst nichts. */
export type AblehnungsArt = "nicht gefunden" | "nicht berechtigt" | "ungueltige Anfrage" | "kontingent";

export class Ablehnung extends Error {
    readonly art: AblehnungsArt;
    constructor(art: AblehnungsArt) {
        super(art);
        this.art = art;
    }
}

/** Werkzeuge, die dieser Endpunkt anbietet. Alle lesend. */
export const TOOLS = [
    {
        name: "rowboat_wissensquellen",
        description:
            "Listet die Wissensquellen (Data Sources) eines Rowboat-Projekts mit Name, " +
            "Status und ob sie aktiv ist. Nur lesend.",
        inputSchema: {
            type: "object",
            properties: {
                projectId: { type: "string", description: "Kennung des Rowboat-Projekts." },
            },
            required: ["projectId"],
            additionalProperties: false,
        },
    },
    {
        name: "rowboat_wissensquelle",
        description:
            "Liest eine einzelne Wissensquelle: Name, Beschreibung, Typ, Status, Fehler, " +
            "Zeitstempel. Nur lesend.",
        inputSchema: {
            type: "object",
            properties: {
                sourceId: { type: "string", description: "Kennung der Wissensquelle (aus rowboat_wissensquellen)." },
            },
            required: ["sourceId"],
            additionalProperties: false,
        },
    },
    {
        name: "rowboat_dokumente",
        description:
            "Listet die Dokumente einer Wissensquelle: Kennung, Name, Typ, Status. " +
            "Mit mitInhalt=true auch den Text von Textdokumenten. Nur lesend.",
        inputSchema: {
            type: "object",
            properties: {
                sourceId: { type: "string", description: "Kennung der Wissensquelle." },
                mitInhalt: { type: "boolean", description: "Textinhalt mitliefern (Vorgabe: nein)." },
            },
            required: ["sourceId"],
            additionalProperties: false,
        },
    },
    {
        name: "rowboat_datei_url",
        description:
            "Liefert eine Download-Adresse fuer ein hochgeladenes Dokument (Datei-Typ). " +
            "Fuer Text- oder URL-Dokumente gibt es keine. Nur lesend.",
        inputSchema: {
            type: "object",
            properties: {
                fileId: { type: "string", description: "Kennung des Dokuments (aus rowboat_dokumente)." },
            },
            required: ["fileId"],
            additionalProperties: false,
        },
    },
] as const;

const RequestSchema = z.object({
    jsonrpc: z.literal("2.0"),
    id: z.union([z.string(), z.number()]).optional(),
    method: z.string(),
    params: z.record(z.unknown()).optional(),
});

export type RpcRequest = z.infer<typeof RequestSchema>;

export interface RpcResponse {
    jsonrpc: "2.0";
    id: string | number | null;
    result?: unknown;
    error?: { code: number; message: string };
}

/** Fehlercodes nach JSON-RPC 2.0. */
export const ERR = {
    PARSE: -32700,
    INVALID_REQUEST: -32600,
    METHOD_NOT_FOUND: -32601,
    INVALID_PARAMS: -32602,
    INTERNAL: -32603,
    UNAUTHORIZED: -32001,
} as const;

function fehler(id: string | number | null, code: number, message: string): RpcResponse {
    return { jsonrpc: "2.0", id, error: { code, message } };
}

function ergebnis(id: string | number | null, result: unknown): RpcResponse {
    return { jsonrpc: "2.0", id, result };
}

/**
 * Was die Werkzeuge ausführen. Die Route reicht hier die Anbindung an den
 * DI-Container herein — dadurch bleibt dieser Kern frei von Rowboat-Interna
 * und damit prüfbar. Jede Funktion darf `Ablehnung` werfen; alles andere,
 * was sie wirft, wird als „Fehler" ohne Wortlaut gemeldet.
 */
export interface WerkzeugKontext {
    apiKey: string;
    quellenAuflisten(projectId: string, apiKey: string): Promise<unknown>;
    quelleLesen(sourceId: string, apiKey: string): Promise<unknown>;
    dokumenteAuflisten(sourceId: string, apiKey: string, mitInhalt: boolean): Promise<unknown>;
    dateiUrl(fileId: string, apiKey: string): Promise<unknown>;
}

type Argumente = Record<string, unknown>;

/** Werkzeugname -> Pflichtparameter und Ausführung. Eine Tabelle, kein switch. */
const AUSFUEHRUNG: Record<string, {
    pflicht: string;
    lauf: (args: Argumente, k: WerkzeugKontext) => Promise<unknown>;
}> = {
    rowboat_wissensquellen: {
        pflicht: "projectId",
        lauf: (a, k) => k.quellenAuflisten(a.projectId as string, k.apiKey),
    },
    rowboat_wissensquelle: {
        pflicht: "sourceId",
        lauf: (a, k) => k.quelleLesen(a.sourceId as string, k.apiKey),
    },
    rowboat_dokumente: {
        pflicht: "sourceId",
        lauf: (a, k) => k.dokumenteAuflisten(a.sourceId as string, k.apiKey, a.mitInhalt === true),
    },
    rowboat_datei_url: {
        pflicht: "fileId",
        lauf: (a, k) => k.dateiUrl(a.fileId as string, k.apiKey),
    },
};

/**
 * Beantwortet genau eine JSON-RPC-Nachricht. Wirft nie — jeder Fehler wird zu
 * einer Antwort, weil ein geworfener Fehler im Transport zu einer HTTP-500
 * würde und der Client dann nicht unterscheiden kann, ob der Server kaputt ist
 * oder das Werkzeug abgelehnt hat.
 */
export async function beantworte(
    roh: unknown,
    kontext: WerkzeugKontext,
): Promise<RpcResponse> {
    const geprueft = RequestSchema.safeParse(roh);
    if (!geprueft.success) {
        return fehler(null, ERR.INVALID_REQUEST, "kein gueltiger JSON-RPC-Aufruf");
    }
    const { id = null, method, params } = geprueft.data;

    // Ohne Schluessel gibt es nicht einmal eine Werkzeugliste. Wer nicht
    // berechtigt ist, soll auch nicht erfahren, was es hier gaebe.
    if (!kontext.apiKey) {
        return fehler(id, ERR.UNAUTHORIZED, "Authorization: Bearer <projekt-api-key> fehlt");
    }

    switch (method) {
        case "initialize":
            return ergebnis(id, {
                protocolVersion: PROTOCOL_VERSION,
                capabilities: { tools: {} },
                serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
            });

        case "notifications/initialized":
            // Benachrichtigung ohne id — der Aufrufer erwartet keine Antwort.
            return ergebnis(id, {});

        case "tools/list":
            return ergebnis(id, { tools: TOOLS });

        case "tools/call": {
            const name = params?.name;
            const args = (params?.arguments ?? {}) as Argumente;
            const eintrag = typeof name === "string" ? AUSFUEHRUNG[name] : undefined;
            if (!eintrag) {
                return fehler(id, ERR.METHOD_NOT_FOUND, `unbekanntes Werkzeug: ${String(name)}`);
            }
            const wert = args[eintrag.pflicht];
            if (typeof wert !== "string" || wert.length === 0) {
                return fehler(id, ERR.INVALID_PARAMS, `${eintrag.pflicht} fehlt oder ist leer`);
            }
            try {
                const daten = await eintrag.lauf(args, kontext);
                return ergebnis(id, {
                    content: [{ type: "text", text: JSON.stringify(daten, null, 2) }],
                    isError: false,
                });
            } catch (e: unknown) {
                // Nur die Art, nie der Wortlaut: ein fremder Fehlertext kann den
                // Schluessel enthalten, und er landet im Protokoll des Aufrufers.
                const art = e instanceof Ablehnung ? e.art : "Fehler";
                return ergebnis(id, {
                    content: [{ type: "text", text: `Aufruf abgelehnt (${art})` }],
                    isError: true,
                });
            }
        }

        default:
            return fehler(id, ERR.METHOD_NOT_FOUND, `unbekannte Methode: ${method}`);
    }
}
