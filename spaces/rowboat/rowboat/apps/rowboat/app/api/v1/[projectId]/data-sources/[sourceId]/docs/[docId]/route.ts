import { NextRequest } from "next/server";
import { container } from "@/di/container";
import { IDeleteDocFromDataSourceController } from "@/src/interface-adapters/controllers/data-sources/delete-doc-from-data-source.controller";
import { PrefixLogger } from "../../../../../../../lib/utils";

function extractApiKey(req: NextRequest): string | undefined {
    return req.headers.get("Authorization")?.split(" ")[1];
}

export async function DELETE(
    req: NextRequest,
    { params }: { params: Promise<{ projectId: string; sourceId: string; docId: string }> }
): Promise<Response> {
    const { docId } = await params;
    const apiKey = extractApiKey(req);
    if (!apiKey) {
        return Response.json({ error: "Missing Authorization bearer token" }, { status: 401 });
    }
    const logger = new PrefixLogger(`delete-doc ${docId}`);
    try {
        const controller = container.resolve<IDeleteDocFromDataSourceController>("deleteDocFromDataSourceController");
        await controller.execute({ caller: "api", apiKey, docId });
        return Response.json({ ok: true });
    } catch (e: any) {
        logger.log(`delete-doc error: ${e?.message || e}`);
        return Response.json({ error: e?.message || "delete-doc failed" }, { status: 500 });
    }
}
