import { statSync } from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const backendRoot = path.resolve(__dirname, "..");
const previewScript = path.join(backendRoot, "pythonscripts/preview_data.py");
const PANDAS_TIMEOUT_MS = Number(process.env.PANDAS_TIMEOUT_MS ?? 120_000);

let pandasQueue = Promise.resolve();

function resolvePython() {
  const candidates = [
    path.join(backendRoot, ".venv/bin/python3"),
    process.env.BACKEND_ROOT
      ? path.join(path.resolve(process.env.BACKEND_ROOT), ".venv/bin/python3")
      : null,
    path.resolve(backendRoot, "../../backend/.venv/bin/python3"),
  ].filter(Boolean);

  for (const candidate of candidates) {
    try {
      statSync(candidate);
      return candidate;
    } catch {
      // try next
    }
  }
  return "python3";
}

function runPandas(action, source, options = {}) {
  const python = resolvePython();
  const payload = JSON.stringify({ action, source, options });

  return new Promise((resolve, reject) => {
    const child = spawn(python, [previewScript], {
      cwd: backendRoot,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let settled = false;

    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };

    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      finish(new Error(`pandas preview timed out after ${PANDAS_TIMEOUT_MS}ms`));
    }, PANDAS_TIMEOUT_MS);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("error", (error) => finish(error));
    child.on("close", (code) => {
      let parsed;
      try {
        parsed = JSON.parse(stdout || "{}");
      } catch {
        finish(new Error(stderr || "Invalid JSON from pandas preview script"));
        return;
      }

      if (code !== 0 || parsed.error) {
        finish(new Error(parsed.error || stderr || `pandas preview failed (${code})`));
        return;
      }

      finish(undefined, parsed);
    });

    // If python exits before reading stdin, the "close" handler reports the failure.
    child.stdin.on("error", () => {});
    child.stdin.write(payload);
    child.stdin.end();
  });
}

function enqueuePandas(task) {
  const run = pandasQueue.then(task, task);
  pandasQueue = run.catch(() => {});
  return run;
}

export function isPandasPreviewAvailable() {
  try {
    statSync(previewScript);
    return true;
  } catch {
    return false;
  }
}

export async function buildPreviewWithPandas(source, options) {
  return enqueuePandas(() => runPandas("preview", source, options));
}

export async function buildChartWithPandas(source, options) {
  return enqueuePandas(() => runPandas("chart", source, options));
}

export async function listFilterOptionsWithPandas(source, filterType, parentFilter = {}) {
  const result = await enqueuePandas(() =>
    runPandas("filters", source, { type: filterType, ...parentFilter }),
  );
  return result.options ?? [];
}
