import http from "node:http";
import { URL } from "node:url";
import { loadAllSources, loadSourceById } from "./lib/sourceStore.mjs";

const port = Number(process.env.BACKEND_PORT ?? 4322);

function sendJson(res, statusCode, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
  });
  res.end(body);
}

const server = http.createServer(async (req, res) => {
  try {
    const method = req.method ?? "GET";
    const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);

    if (method === "GET" && url.pathname === "/api/health") {
      return sendJson(res, 200, { ok: true, service: "backend", port });
    }

    if (method === "GET" && url.pathname === "/api/sources") {
      const sources = await loadAllSources();
      return sendJson(res, 200, { count: sources.length, data: sources });
    }

    if (method === "GET" && url.pathname.startsWith("/api/sources/")) {
      const id = decodeURIComponent(url.pathname.replace("/api/sources/", ""));
      const source = await loadSourceById(id);
      if (!source) {
        return sendJson(res, 404, { error: "Source not found", id });
      }
      return sendJson(res, 200, source);
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
