import { parse } from "csv-parse/sync";
import AdmZip from "adm-zip";

const CACHE_TTL_MS = 15 * 60 * 1000;
const cache = new Map();

function isZip(data) {
  return data.length >= 4 && data[0] === 0x50 && data[1] === 0x4b && data[2] === 0x03 && data[3] === 0x04;
}

function isXlsx(data) {
  if (!isZip(data)) return false;
  try {
    const zip = new AdmZip(data);
    return zip.getEntries().some((e) => e.entryName === "xl/workbook.xml");
  } catch {
    return false;
  }
}

function pickFileFromZip(data) {
  const zip = new AdmZip(data);
  const entries = zip
    .getEntries()
    .filter(
      (e) =>
        !e.isDirectory &&
        (e.entryName.toLowerCase().endsWith(".csv") || e.entryName.toLowerCase().endsWith(".tsv")),
    )
    .sort((a, b) => a.entryName.localeCompare(b.entryName));

  if (entries.length === 0) {
    throw new Error("ZIP archive contains no CSV or TSV files");
  }

  return { data: entries[0].getData(), filename: entries[0].entryName };
}

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

async function loadTabularData(downloadUrl) {
  let raw = await downloadBytes(downloadUrl);
  const urlLower = downloadUrl.split("?")[0].toLowerCase();

  if (isZip(raw) && !isXlsx(raw)) {
    const extracted = pickFileFromZip(raw);
    raw = extracted.data;
  }

  if (urlLower.endsWith(".xlsx") || isXlsx(raw)) {
    throw new Error("XLSX files are not supported in preview API v1");
  }

  return parseCsvBytes(raw);
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
