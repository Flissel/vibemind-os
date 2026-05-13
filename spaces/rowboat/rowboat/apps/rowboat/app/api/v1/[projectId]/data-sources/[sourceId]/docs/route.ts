import { NextRequest } from "next/server";
import { z } from "zod";
import { container } from "@/di/container";
import { IAddDocsToDataSourceController } from "@/src/interface-adapters/controllers/data-sources/add-docs-to-data-source.controller";
import { IListDocsInDataSourceController } from "@/src/interface-adapters/controllers/data-sources/list-docs-in-data-source.controller";
import { PrefixLogger } from "../../../../../../lib/utils";

function extractApiKey(req: NextRequest): string | undefined {
    return req.headers.get("Authorization")?.split(" ")[1];
}

const TextDoc = z.object({
    name: z.string(),
    data: z.object({
        type: z.literal("text"),
        content: z.string(),
    }),
});

const AddDocsBody = z.object({
    docs: z.array(TextDoc),
});

export async function GET(
    req: NextRequest,
    { params }: { params: Promise<{ projectId: string; sourceId: string }> }
): Promise<Response> {
    const { sourceId } = await params;
    const apiKey = extractApiKey(req);
    if (!apiKey) {
        return Response.json({ error: "Missing Authorization bearer token" }, { status: 401 });
    }
    const logger = new PrefixLogger(`list-docs ${sourceId}`);
    try {
        const controller = container.resolve<IListDocsInDataSourceController>("listDocsInDataSourceController");
        const items = await controller.execute({ caller: "api", apiKey, sourceId });
        return Response.json({ items });
    } catch (e: any) {
        logger.log(`list-docs error: ${e?.message || e}`);
        return Response.json({ error: e?.message || "list-docs failed" }, { status: 500 });
    }
}

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ projectId: string; sourceId: string }> }
): Promise<Response> {
    const { sourceId } = await params;
    const apiKey = extractApiKey(req);
    if (!apiKey) {
        return Response.json({ error: "Missing Authorization bearer token" }, { status: 401 });
    }
    const logger = new PrefixLogger(`add-docs ${sourceId}`);
    let body;
    try {
        const parsed = await req.json();
        body = AddDocsBody.parse(parsed);
    } catch (e) {
        return Response.json({ error: `Invalid body: ${e}` }, { status: 400 });
    }
    try {
        const controller = container.resolve<IAddDocsToDataSourceController>("addDocsToDataSourceController");
        await controller.execute({ caller: "api", apiKey, sourceId, docs: body.docs });
        return Response.json({ ok: true, count: body.docs.length });
    } catch (e: any) {
        logger.log(`add-docs error: ${e?.message || e}`);
        return Response.json({ error: e?.message || "add-docs failed" }, { status: 500 });
    }
}
