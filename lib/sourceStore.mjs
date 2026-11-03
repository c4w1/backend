import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
import { isNationalLevel } from "./catalogScope.mjs";
import {
  getDefaultDbPath,
  isSqliteAvailable,
  loadAllSourcesFromSqlite,
  loadSourceByIdFromSqlite,
} from "./sourceFromSqlite.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sourcesDir = path.resolve(__dirname, "../data/sources");

let cachedDataSource = null;

function resolveBackend() {
  const dbPath = getDefaultDbPath();
  return isSqliteAvailable(dbPath) ? "sqlite" : "yaml";
}

export function getDataSource() {
  if (cachedDataSource === null) {
    cachedDataSource = resolveBackend();
  }
  return cachedDataSource;
}

export { getDefaultDbPath };

export async function loadAllSourcesFromYaml() {
  const files = await readdir(sourcesDir);
  const ymlFiles = files.filter((name) => name.endsWith(".yml") || name.endsWith(".yaml"));

  const records = await Promise.all(
    ymlFiles.map(async (name) => {
      const fullPath = path.join(sourcesDir, name);
      const raw = await readFile(fullPath, "utf8");
      const parsed = YAML.parse(raw) ?? {};
      return {
        file: name,
        ...parsed,
      };
    }),
  );

  return records.sort((a, b) => String(a.id ?? "").localeCompare(String(b.id ?? "")));
}

export async function loadSourceByIdFromYaml(id) {
  const all = await loadAllSourcesFromYaml();
  return all.find((item) => item.id === id) ?? null;
}

const flaggedSources = new Set();

// National-level sources stay in SQLite and the YAML, but the API serves only the catalog (web#42).
function inCatalog(source) {
  if (!source.filters && !flaggedSources.has(source.id)) {
    flaggedSources.add(source.id);
    console.warn(`WARNING: ${source.id} has no filters, so the local-data rule can't check it (web#42)`);
  }
  return !isNationalLevel(source.filters);
}

export async function loadAllSources() {
  const sources =
    getDataSource() === "sqlite" ? await loadAllSourcesFromSqlite() : await loadAllSourcesFromYaml();
  return sources.filter(inCatalog);
}

export async function loadSourceById(id) {
  const source =
    getDataSource() === "sqlite" ? await loadSourceByIdFromSqlite(id) : await loadSourceByIdFromYaml(id);
  return source && inCatalog(source) ? source : null;
}
