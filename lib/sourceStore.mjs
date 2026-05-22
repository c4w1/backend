import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";
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

export async function loadAllSources() {
  if (getDataSource() === "sqlite") {
    return loadAllSourcesFromSqlite();
  }
  return loadAllSourcesFromYaml();
}

export async function loadSourceById(id) {
  if (getDataSource() === "sqlite") {
    return loadSourceByIdFromSqlite(id);
  }
  return loadSourceByIdFromYaml(id);
}
