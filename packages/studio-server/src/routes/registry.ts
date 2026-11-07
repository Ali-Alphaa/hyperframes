import type { Hono } from "hono";
import type { StudioApiAdapter } from "../types.js";
import { projectDirMissing } from "../helpers/projectDirMissing.js";
import { folderGone, isProjectRootMissing } from "../helpers/safePath.js";

export function registerRegistryRoutes(api: Hono, adapter: StudioApiAdapter): void {
  api.get("/registry/blocks", async (c) => {
    if (!adapter.listRegistryCatalog) {
      return c.json({ error: "Registry not available" }, 501);
    }
    const items = await adapter.listRegistryCatalog();
    return c.json(items);
  });

  // Fork: thumbnails made by scripts/generate-catalog-previews.ts for items with no hosted poster.
  api.get("/registry/local-poster/:kind/:name", async (c) => {
    const kind = c.req.param("kind");
    const name = c.req.param("name");
    if ((kind !== "block" && kind !== "component") || !/^[a-z0-9][a-z0-9-]*$/.test(name)) {
      return c.json({ error: "Not found" }, 404);
    }
    const body = await adapter.readRegistryLocalPoster?.(kind, name);
    if (!body) return c.json({ error: "Not found" }, 404);
    const isJpeg = body[0] === 0xff && body[1] === 0xd8;
    return c.body(new Uint8Array(body), 200, {
      "Content-Type": isJpeg ? "image/jpeg" : "image/png",
      "Cache-Control": "public, max-age=86400",
    });
  });

  // fallow-ignore-next-line complexity
  api.post("/projects/:id/registry/install", async (c) => {
    if (!adapter.installRegistryBlock) {
      return c.json({ error: "Installing catalog items needs hyperframes preview" }, 501);
    }
    const project = await adapter.resolveProject(c.req.param("id"));
    if (!project) return c.json({ error: "Project not found" }, 404);
    if (folderGone(project.dir)) return projectDirMissing(c);

    const body = await c.req.json<{ blockName?: string }>().catch(() => null);
    if (!body?.blockName) {
      return c.json({ error: "blockName is required" }, 400);
    }

    try {
      const result = await adapter.installRegistryBlock({ project, blockName: body.blockName });
      return c.json(result);
    } catch (err) {
      if (isProjectRootMissing(err)) return projectDirMissing(c);
      const message = err instanceof Error ? err.message : "Install failed";
      return c.json({ error: message }, 500);
    }
  });
}
