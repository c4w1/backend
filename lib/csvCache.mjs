import { parse } from "csv-parse/sync";
import { detectFileFormat, readFromZip } from "./fileFormat.mjs";
import { parseXlsxBytes } from "./xlsxTable.mjs";

const CACHE_TTL_MS = 15 * 60 * 1000;
const cache = new Map();

function detectDelimiter(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.trim()).slice(0, 5);
  if (lines.length === 0) return ",";
  const delimiters = [",", "\t", ";", "|"];
  for (const delim of delimiters) {
    const counts = lines.map((l) => l.split(delim).length - 1);
    if (counts.every((c) => c === counts[0] && c > 0)) return delim;
  }
  return ",";
}

function decodeText(data) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(data);
  } catch {
    return new TextDecoder("latin1").decode(data);
  }
}

function parseCsvBytes(data) {
  const text = decodeText(data);
  const delimiter = detectDelimiter(text);
  const records = parse(text, {
    delimiter,
    relax_column_count: true,
    skip_empty_lines: true,
    trim: true,
  });
  if (!records.length) {
    throw new Error("File appears to be empty");
  }
  const headers = records[0].map((h) => String(h ?? "").trim());
  const rows = records.slice(1).map((row) => row.map((cell) => String(cell ?? "").trim()));
  return { headers, rows };
}

async function downloadBytes(url) {
  const response = await fetch(url, {
    headers: { "User-Agent": "Mozilla/5.0 (compatible; CDL-Backend/1.0)" },
    signal: AbortSignal.timeout(120_000),
  });
  if (!response.ok) {
    throw new Error(`Download failed: HTTP ${response.status}`);
  }
  return Buffer.from(await response.arrayBuffer());
}

function parseTable(data, format) {
  if (format === "xlsx") return parseXlsxBytes(data);
  if (format === "xls") {
    throw new Error("XLS files are not supported in the preview API; download the file to open it");
  }
  if (format === "csv" || format === "tsv") return parseCsvBytes(data);
  throw new Error(`ZIP archive contains no CSV, TSV or XLSX files (${format})`);
}

async function loadTabularData(downloadUrl) {
  const raw = await downloadBytes(downloadUrl);
  const format = detectFileFormat(raw, downloadUrl.split("?")[0]);
  if (!format.startsWith("zip/")) return parseTable(raw, format);
  const inner = format.slice("zip/".length);
  return parseTable(readFromZip(raw, inner), inner);
}

export async function getSourceTable(source) {
  const sourceId = source.id;
  const cached = cache.get(sourceId);
  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
    return cached.table;
  }

  const downloadUrl = source.download?.url;
  if (!downloadUrl) {
    throw new Error("Source has no download URL");
  }

  const table = await loadTabularData(downloadUrl);
  cache.set(sourceId, { table, fetchedAt: Date.now() });
  return table;
}

export function clearCache(sourceId) {
  if (sourceId) cache.delete(sourceId);
  else cache.clear();
}
