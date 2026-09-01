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
 * DIE PROJEKTBINDUNG entsteht NICHT hier. `projectId` ist ein Aufrufparameter,
 * aber `ProjectActionAuthorizationPolicy` im Use-Case prüft, ob der
 * mitgeschickte Schlüssel zu genau diesem Projekt gehört — dieselbe Kante, die
 * auch die Chat-Route trägt. Ein erratener Projektname nützt ohne den
 * passenden Schlüssel nichts.
 */
import { z } from "zod";

export const PROTOCOL_VERSION = "2025-06-18";
export const SERVER_NAME = "rowboat";
export const SERVER_VERSION = "0.1.0";

/** Werkzeuge, die dieser Endpunkt anbietet. Bewusst klein gehalten. */
export const TOOLS = [
    {
        name: "rowboat_wissensquellen",
        description:
            "Listet die Wissensquellen (Data Sources) eines Rowboat-Projekts mit Name, " +
            "Status und ob sie aktiv ist. Nur lesend.",
        inputSchema: {
            type: "object",
            properties: {
                projectId: {
                    type: "string",
                    description: "Kennung des Rowboat-Projekts.",
                },
            },
            required: ["projectId"],
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
 * Was ein Werkzeug ausführt. Die Route reicht hier die Anbindung an den
 * DI-Container herein — dadurch bleibt dieser Kern frei von Rowboat-Interna
 * und damit prüfbar.
 */
export interface WerkzeugKontext {
    apiKey: string;
    quellenAuflisten(projectId: string, apiKey: string): Promise<unknown>;
}

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
            const args = (params?.arguments ?? {}) as Record<string, unknown>;
            if (name !== "rowboat_wissensquellen") {
                return fehler(id, ERR.METHOD_NOT_FOUND, `unbekanntes Werkzeug: ${String(name)}`);
            }
            const projectId = args.projectId;
            if (typeof projectId !== "string" || projectId.length === 0) {
                return fehler(id, ERR.INVALID_PARAMS, "projectId fehlt oder ist leer");
            }
            try {
                const quellen = await kontext.quellenAuflisten(projectId, kontext.apiKey);
                return ergebnis(id, {
                    content: [{ type: "text", text: JSON.stringify(quellen, null, 2) }],
                    isError: false,
                });
            } catch (e: unknown) {
                // Der Text einer Autorisierungs-Ausnahme kann den Schluessel
                // enthalten; deshalb wird nur die Art gemeldet, nicht der Wortlaut.
                const art = e instanceof Error ? e.constructor.name : "Fehler";
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
