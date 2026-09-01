/**
 * MCP-Endpunkt — Rowboat von aussen bedienbar, ohne fuer jede Faehigkeit eine
 * eigene REST-Route zu bauen.
 *
 * Die Logik liegt nicht hier: Rowboats Server-Actions sind duenne Huellen ueber
 * Controller aus dem DI-Container (`data-source.actions.ts` loest zwoelf davon
 * auf, bevor die erste Funktion beginnt). Dieser Endpunkt loest dieselben
 * Controller auf und uebersetzt nur zwischen JSON-RPC und ihnen.
 *
 * Das Auth-Muster ist das der Chat-Route: Schluessel im Kopf, Projektkennung im
 * Aufruf, und `ProjectActionAuthorizationPolicy` im Use-Case entscheidet. Diese
 * Route trifft keine eigene Berechtigungsentscheidung — sie reicht nur weiter.
 */
import { NextRequest } from "next/server";
import { container } from "@/di/container";
import { IListDataSourcesController } from "@/src/interface-adapters/controllers/data-sources/list-data-sources.controller";
import { beantworte, ERR, type RpcResponse } from "./rpc";

export const dynamic = "force-dynamic";

function schluesselAus(req: NextRequest): string {
    const kopf = req.headers.get("Authorization") ?? "";
    const teile = kopf.split(" ");
    return teile.length === 2 && teile[0].toLowerCase() === "bearer" ? teile[1] : "";
}

export async function POST(req: NextRequest): Promise<Response> {
    let roh: unknown;
    try {
        roh = await req.json();
    } catch {
        const antwort: RpcResponse = {
            jsonrpc: "2.0",
            id: null,
            error: { code: ERR.PARSE, message: "Rumpf ist kein JSON" },
        };
        return Response.json(antwort, { status: 200 });
    }

    const apiKey = schluesselAus(req);

    const antwort = await beantworte(roh, {
        apiKey,
        async quellenAuflisten(projectId: string, schluessel: string) {
            const controller = container.resolve<IListDataSourcesController>(
                "listDataSourcesController",
            );
            const quellen = await controller.execute({
                caller: "api",
                apiKey: schluessel,
                projectId,
            });
            // Nur was ein Aufrufer braucht — keine internen Felder nach aussen.
            return quellen.map((q) => ({
                id: q.id,
                name: q.name,
                status: q.status,
                active: q.active,
            }));
        },
    });

    // JSON-RPC transportiert seine Fehler im Rumpf, nicht im HTTP-Status.
    // Ein 500 hier wuerde einen Client glauben lassen, der Server sei defekt.
    return Response.json(antwort, { status: 200 });
}
