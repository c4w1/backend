import http from "node:http";
import { URL } from "node:url";
import { buildChart, buildPreview, listFilterOptions } from "./lib/dataPreview.mjs";
import {
  buildChartWithPandas,
  buildPreviewWithPandas,
  isPandasPreviewAvailable,
  listFilterOptionsWithPandas,
} from "./lib/pandasPreview.mjs";
import { buildDatasheet } from "./lib/datasheet.mjs";
import { getDataSource, loadAllSources, loadSourceById } from "./lib/sourceStore.mjs";

const port = Number(process.env.BACKEND_PORT ?? 4323);
const usePandas = process.env.PREVIEW_ENGINE !== "node" && isPandasPreviewAvailable();

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  });
  res.end(body);
}

function parseSourcePath(pathname) {
  const prefix = "/api/sources/";
  if (!pathname.startsWith(prefix)) return null;
  const rest = decodeURIComponent(pathname.slice(prefix.length));
  const slash = rest.indexOf("/");
  if (slash === -1) return { id: rest, action: null };
  return { id: rest.slice(0, slash), action: rest.slice(slash + 1) };
}

const server = http.createServer(async (req, res) => {
  try {
    const method = req.method ?? "GET";
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

    if (method === "OPTIONS") {
      res.writeHead(204, {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
      });
      return res.end();
    }

    if (method === "GET" && url.pathname === "/api/health") {
      return sendJson(res, 200, {
        ok: true,
        service: "backend",
        port,
        dataSource: getDataSource(),
        previewEngine: usePandas ? "pandas" : "node",
      });
    }

    if (method === "GET" && url.pathname === "/api/sources") {
      const sources = await loadAllSources();
      return sendJson(res, 200, { count: sources.length, data: sources });
    }

    const parsed = parseSourcePath(url.pathname);
    if (method === "GET" && parsed?.id) {
      const source = await loadSourceById(parsed.id);
      if (!source) {
        return sendJson(res, 404, { error: "Source not found", id: parsed.id });
      }

      if (!parsed.action) {
        return sendJson(res, 200, source);
      }

      if (parsed.action === "datasheet") {
        return sendJson(res, 200, { id: parsed.id, datasheet: buildDatasheet(source) });
      }

      if (parsed.action === "preview") {
        const options = {
          state: url.searchParams.get("state") ?? undefined,
          county: url.searchParams.get("county") ?? undefined,
          limit: Number(url.searchParams.get("limit") ?? 25),
          offset: Number(url.searchParams.get("offset") ?? 0),
          columns: url.searchParams.get("columns") ?? undefined,
        };
        try {
          const preview = usePandas
            ? await buildPreviewWithPandas(source, options)
            : await buildPreview(source, options);
          return sendJson(res, 200, preview);
        } catch (pandasError) {
          const preview = await buildPreview(source, options);
          return sendJson(res, 200, { ...preview, engine: "node", pandasFallback: String(pandasError) });
        }
      }

      if (parsed.action === "chart") {
        const options = {
          variable: url.searchParams.get("variable") ?? undefined,
          state: url.searchParams.get("state") ?? undefined,
          county: url.searchParams.get("county") ?? undefined,
          limit: Number(url.searchParams.get("limit") ?? 50),
        };
        try {
          const chart = usePandas
            ? await buildChartWithPandas(source, options)
            : await buildChart(source, options);
          return sendJson(res, 200, chart);
        } catch (pandasError) {
          const chart = await buildChart(source, options);
          return sendJson(res, 200, { ...chart, engine: "node", pandasFallback: String(pandasError) });
        }
      }

      if (parsed.action === "filters") {
        const filterType = url.searchParams.get("type") ?? "state";
        const state = url.searchParams.get("state") ?? undefined;
        try {
          const options = usePandas
            ? await listFilterOptionsWithPandas(source, filterType, { state })
            : await listFilterOptions(source, filterType, { state });
          return sendJson(res, 200, { id: parsed.id, type: filterType, options, engine: usePandas ? "pandas" : "node" });
        } catch (pandasError) {
          const options = await listFilterOptions(source, filterType, { state });
          return sendJson(res, 200, { id: parsed.id, type: filterType, options, engine: "node", pandasFallback: String(pandasError) });
        }
      }

      return sendJson(res, 404, { error: "Not found", action: parsed.action });
    }

    return sendJson(res, 404, { error: "Not found" });
  } catch (error) {
    return sendJson(res, 500, {
      error: "Internal server error",
      details: error instanceof Error ? error.message : String(error),
    });
  }
});

server.listen(port, () => {
  console.log(`Backend listening on http://localhost:${port}`);
});
