/**
 * MCP-Endpunkt — Rowboat von aussen bedienbar, ohne fuer jede Faehigkeit eine
 * eigene REST-Route zu bauen.
 *
 * Die Logik liegt nicht hier: Rowboats Server-Actions sind duenne Huellen ueber
 * Controller aus dem DI-Container (`data-source.actions.ts` loest zwoelf davon
 * auf, bevor die erste Funktion beginnt). Dieser Endpunkt loest dieselben
 * Controller auf und uebersetzt nur zwischen JSON-RPC und ihnen.
 *
 * Das Auth-Muster ist das der Chat-Route: Schluessel im Kopf, Kennung im
 * Aufruf, und `ProjectActionAuthorizationPolicy` im Use-Case entscheidet. Diese
 * Route trifft keine eigene Berechtigungsentscheidung — sie reicht nur weiter.
 *
 * FEHLER werden hier in `Ablehnung` uebersetzt, bevor sie den Kern erreichen:
 * Rowboats Fehlerklassen tragen keinen `name`, und der Produktions-Build kuerzt
 * ihre Klassennamen auf einen Buchstaben (live gesehen: „Aufruf abgelehnt (a)").
 * Der Wortlaut der Fehler bleibt hier — er kann den Schluessel enthalten.
 */
import { NextRequest } from "next/server";
import { container } from "@/di/container";
import { IListDataSourcesController } from "@/src/interface-adapters/controllers/data-sources/list-data-sources.controller";
import { IFetchDataSourceController } from "@/src/interface-adapters/controllers/data-sources/fetch-data-source.controller";
import { IListDocsInDataSourceController } from "@/src/interface-adapters/controllers/data-sources/list-docs-in-data-source.controller";
import { IGetDownloadUrlForFileController } from "@/src/interface-adapters/controllers/data-sources/get-download-url-for-file.controller";
import { ICreateDataSourceController } from "@/src/interface-adapters/controllers/data-sources/create-data-source.controller";
import { IAddDocsToDataSourceController } from "@/src/interface-adapters/controllers/data-sources/add-docs-to-data-source.controller";
import {
    BadRequestError,
    BillingError,
    NotAuthorizedError,
    NotFoundError,
    QuotaExceededError,
} from "@/src/entities/errors/common";
import { Ablehnung, beantworte, ERR, type RpcResponse } from "./rpc";

export const dynamic = "force-dynamic";

function schluesselAus(req: NextRequest): string {
    const kopf = req.headers.get("Authorization") ?? "";
    const teile = kopf.split(" ");
    return teile.length === 2 && teile[0].toLowerCase() === "bearer" ? teile[1] : "";
}

/** Rowboat-Fehler -> feste Art. Unbekanntes bleibt unbekannt (kein Wortlaut). */
function uebersetzt(e: unknown): never {
    if (e instanceof NotFoundError) throw new Ablehnung("nicht gefunden");
    if (e instanceof NotAuthorizedError) throw new Ablehnung("nicht berechtigt");
    if (e instanceof BadRequestError) throw new Ablehnung("ungueltige Anfrage");
    if (e instanceof QuotaExceededError || e instanceof BillingError) throw new Ablehnung("kontingent");
    throw e;
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

        async quellenAuflisten(projectId, schluessel) {
            const controller = container.resolve<IListDataSourcesController>(
                "listDataSourcesController",
            );
            const quellen = await controller
                .execute({ caller: "api", apiKey: schluessel, projectId })
                .catch(uebersetzt);
            // Nur was ein Aufrufer braucht — keine internen Felder nach aussen.
            return quellen.map((q) => ({
                id: q.id,
                name: q.name,
                status: q.status,
                active: q.active,
            }));
        },

        async quelleLesen(sourceId, schluessel) {
            const controller = container.resolve<IFetchDataSourceController>(
                "fetchDataSourceController",
            );
            const q = await controller
                .execute({ caller: "api", apiKey: schluessel, sourceId })
                .catch(uebersetzt);
            return {
                id: q.id,
                name: q.name,
                description: q.description,
                typ: q.data.type,
                status: q.status,
                active: q.active,
                error: q.error,
                createdAt: q.createdAt,
                lastUpdatedAt: q.lastUpdatedAt,
            };
        },

        async dokumenteAuflisten(sourceId, schluessel, mitInhalt) {
            const controller = container.resolve<IListDocsInDataSourceController>(
                "listDocsInDataSourceController",
            );
            const docs = await controller
                .execute({ caller: "api", apiKey: schluessel, sourceId })
                .catch(uebersetzt);
            return docs.map((d) => ({
                id: d.id,
                name: d.name,
                typ: d.data.type,
                status: d.status,
                error: d.error,
                // Der Inhalt kann gross sein — nur auf ausdruecklichen Wunsch.
                ...(mitInhalt ? { content: d.content } : {}),
            }));
        },

        async dateiUrl(fileId, schluessel) {
            const controller = container.resolve<IGetDownloadUrlForFileController>(
                "getDownloadUrlForFileController",
            );
            const url = await controller
                .execute({ caller: "api", apiKey: schluessel, fileId })
                .catch(uebersetzt);
            return { url };
        },

        async quelleAnlegen(projectId, name, beschreibung, schluessel) {
            const controller = container.resolve<ICreateDataSourceController>(
                "createDataSourceController",
            );
            // Controller-Input traegt die Quelle unter `data` (CreateSchema);
            // description ist im Modell ein Pflicht-String, leer ist erlaubt.
            const q = await controller
                .execute({
                    caller: "api",
                    apiKey: schluessel,
                    data: {
                        projectId,
                        name,
                        description: beschreibung,
                        data: { type: "text" },
                        status: "pending",
                    },
                })
                .catch(uebersetzt);
            return { id: q.id, name: q.name, status: q.status };
        },

        async dokumenteSchreiben(sourceId, dokumente, schluessel) {
            const controller = container.resolve<IAddDocsToDataSourceController>(
                "addDocsToDataSourceController",
            );
            await controller
                .execute({
                    caller: "api",
                    apiKey: schluessel,
                    sourceId,
                    docs: dokumente.map((d) => ({
                        name: d.name,
                        data: { type: "text" as const, content: d.inhalt },
                    })),
                })
                .catch(uebersetzt);
            return { geschrieben: dokumente.length };
        },
    });

    // JSON-RPC transportiert seine Fehler im Rumpf, nicht im HTTP-Status.
    // Ein 500 hier wuerde einen Client glauben lassen, der Server sei defekt.
    return Response.json(antwort, { status: 200 });
}
