import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import YAML from "yaml";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const sourcesDir = path.resolve(__dirname, "../data/sources");

export async function loadAllSources() {
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

export async function loadSourceById(id) {
  const all = await loadAllSources();
  return all.find((item) => item.id === id) ?? null;
}
