import { NextRequest } from "next/server";
import { z } from "zod";
import { container } from "@/di/container";
import { IListDataSourcesController } from "@/src/interface-adapters/controllers/data-sources/list-data-sources.controller";
import { ICreateDataSourceController } from "@/src/interface-adapters/controllers/data-sources/create-data-source.controller";
import { PrefixLogger } from "../../../../lib/utils";

function extractApiKey(req: NextRequest): string | undefined {
    return req.headers.get("Authorization")?.split(" ")[1];
}

const CreateBody = z.object({
    name: z.string(),
    description: z.string().optional(),
    type: z.enum(["urls", "files_local", "files_s3", "text"]).default("text"),
});

export async function GET(
    _req: NextRequest,
    { params }: { params: Promise<{ projectId: string }> }
): Promise<Response> {
    const { projectId } = await params;
    const apiKey = extractApiKey(_req);
    if (!apiKey) {
        return Response.json({ error: "Missing Authorization bearer token" }, { status: 401 });
    }
    const logger = new PrefixLogger(`list-data-sources ${projectId}`);
    try {
        const controller = container.resolve<IListDataSourcesController>("listDataSourcesController");
        const items = await controller.execute({ caller: "api", apiKey, projectId });
        return Response.json({ items });
    } catch (e: any) {
        logger.log(`list error: ${e?.message || e}`);
        return Response.json({ error: e?.message || "list failed" }, { status: 500 });
    }
}

export async function POST(
    req: NextRequest,
    { params }: { params: Promise<{ projectId: string }> }
): Promise<Response> {
    const { projectId } = await params;
    const apiKey = extractApiKey(req);
    if (!apiKey) {
        return Response.json({ error: "Missing Authorization bearer token" }, { status: 401 });
    }
    const logger = new PrefixLogger(`create-data-source ${projectId}`);
    let body;
    try {
        const parsed = await req.json();
        body = CreateBody.parse(parsed);
    } catch (e) {
        return Response.json({ error: `Invalid body: ${e}` }, { status: 400 });
    }
    try {
        const controller = container.resolve<ICreateDataSourceController>("createDataSourceController");
        const created = await controller.execute({
            caller: "api",
            apiKey,
            data: {
                projectId,
                name: body.name,
                description: body.description || "",
                status: "pending",
                data: { type: body.type },
            },
        });
        return Response.json(created);
    } catch (e: any) {
        logger.log(`create error: ${e?.message || e}`);
        return Response.json({ error: e?.message || "create failed" }, { status: 500 });
    }
}
