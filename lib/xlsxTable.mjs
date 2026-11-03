import AdmZip from "adm-zip";

/**
 * Read the first worksheet of an .xlsx into the { headers, rows } a CSV produces.
 * Same approach as read_xlsx in pythonscripts/export_previews.py: title rows, then one
 * or more header rows (merged across columns), then data rows, then footnotes.
 * Cells come back as Excel stores them, so a date is its serial number.
 */

const XML_ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

function decodeXml(text) {
  return text.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, code) => {
    if (code[0] !== "#") return XML_ENTITIES[code.toLowerCase()];
    const hex = code[1].toLowerCase() === "x";
    return String.fromCodePoint(parseInt(code.slice(hex ? 2 : 1), hex ? 16 : 10));
  });
}

/** All the text runs in an <si> or <is> element, leaving out phonetic guides. */
function runText(xml) {
  const text = xml.replace(/<rPh\b[\s\S]*?<\/rPh>/g, "");
  return [...text.matchAll(/<t\b[^>]*?(?:\/>|>([\s\S]*?)<\/t>)/g)].map((m) => decodeXml(m[1] ?? "")).join("");
}

function attribute(attrs, name) {
  return attrs.match(new RegExp(`(?:^|\\s)${name}="([^"]*)"`))?.[1];
}

function columnIndex(ref) {
  let n = 0;
  for (const ch of ref.match(/[A-Z]+/)[0]) n = n * 26 + (ch.charCodeAt(0) - 64);
  return n - 1;
}

function rowIndex(ref) {
  return Number(ref.match(/\d+/)[0]) - 1;
}

/** Excel stores 78.9 as 78.900000000000006; show the shortest form. */
function formatNumber(text) {
  const number = Number(text);
  return text.trim() !== "" && Number.isFinite(number) ? String(number) : text;
}

/** An XML part as text, without namespace prefixes on its tags (<x:c> becomes <c>). */
function readXml(zip, name) {
  return zip.readAsText(name).replace(/<(\/?)[A-Za-z0-9]+:/g, "<$1");
}

/** The first sheet in the workbook's own order, which need not be sheet1.xml. */
function firstSheetPath(zip) {
  const id = attribute(readXml(zip, "xl/workbook.xml").match(/<sheet\b([^>]*)>/)?.[1] ?? "", "r:id");
  const rel = [...readXml(zip, "xl/_rels/workbook.xml.rels").matchAll(/<Relationship\b([^>]*)>/g)]
    .map((m) => m[1])
    .find((attrs) => attribute(attrs, "Id") === id);
  const target = rel ? attribute(rel, "Target") : null;
  if (target) return target.startsWith("/") ? target.slice(1) : `xl/${target}`;
  const sheets = zip
    .getEntries()
    .map((e) => e.entryName)
    .filter((name) => /^xl\/worksheets\/sheet\d+\.xml$/.test(name))
    .sort();
  if (!sheets.length) throw new Error("Workbook has no worksheets");
  return sheets[0];
}

function readGrid(data) {
  const zip = new AdmZip(data);
  const shared = [...readXml(zip, "xl/sharedStrings.xml").matchAll(/<si\b[^>]*?(?:\/>|>([\s\S]*?)<\/si>)/g)].map((m) =>
    runText(m[1] ?? ""),
  );
  const sheet = readXml(zip, firstSheetPath(zip));

  const cells = new Map();
  let height = 0;
  let width = 0;
  const put = (r, c, value) => {
    cells.set(`${r},${c}`, value);
    height = Math.max(height, r + 1);
    width = Math.max(width, c + 1);
  };

  for (const [, attrs, body = ""] of sheet.matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
    const ref = attribute(attrs, "r");
    if (!ref) continue;
    const kind = attribute(attrs, "t");
    let value = decodeXml(body.match(/<v\b[^>]*>([\s\S]*?)<\/v>/)?.[1] ?? "");
    if (kind === "s" && value) value = shared[Number(value)] ?? "";
    else if (kind === "inlineStr") value = runText(body);
    else if ((!kind || kind === "n") && value) value = formatNumber(value);
    put(rowIndex(ref), columnIndex(ref), value.split(/\s+/).filter(Boolean).join(" "));
  }

  for (const [, ref] of sheet.matchAll(/<mergeCell\b[^>]*?ref="([^"]+)"/g)) {
    const [first, last] = ref.split(":");
    if (!last) continue;
    const r = rowIndex(first);
    const value = cells.get(`${r},${columnIndex(first)}`) ?? "";
    for (let c = columnIndex(first) + 1; c <= columnIndex(last); c += 1) put(r, c, value);
  }

  return Array.from({ length: height }, (_, r) =>
    Array.from({ length: width }, (_, c) => cells.get(`${r},${c}`) ?? ""),
  );
}

function isNumber(value) {
  return value.trim() !== "" && Number.isFinite(Number(value));
}

function filled(row) {
  return row.filter(Boolean).length;
}

// A data row has a label in its first cell and mostly numbers after it (a header row
// of years has no label, so it is not mistaken for data).
function isDataRow(row) {
  const rest = row.slice(1).filter(Boolean);
  return (
    Boolean(row[0]) &&
    rest.length >= Math.max(1, (row.length - 1) / 2) &&
    rest.filter(isNumber).length >= rest.length * 0.6
  );
}

function headerRowsAbove(grid, start) {
  const rows = [];
  for (const row of grid.slice(0, start).reverse()) {
    // A title row (one value, possibly merged across the width) ends the header block.
    if (new Set(row.filter(Boolean)).size <= 1 && rows.length > 0) break;
    if (filled(row) > 0) rows.unshift(row);
  }
  return rows;
}

export function parseXlsxBytes(data) {
  const grid = readGrid(data);
  let start = grid.findIndex(isDataRow);
  if (start === -1) {
    // No numeric rows: a plain table of text, with its first filled row as the header.
    const header = grid.findIndex((row) => filled(row) >= 2);
    if (header === -1) throw new Error("No table found in the first worksheet");
    start = header + 1;
  }

  const headerRows = headerRowsAbove(grid, start);
  const headers = grid[0].map((_, c) => {
    const parts = [];
    for (const row of headerRows) {
      if (row[c] && !parts.includes(row[c])) parts.push(row[c]);
    }
    return parts.join(" / ") || `Column ${c + 1}`;
  });

  const rows = [];
  for (const row of grid.slice(start)) {
    if (filled(row) < 2) break; // footnotes
    rows.push(row.map((value, i) => (i === 0 ? value.replace(/^\.+/, "") : value)));
  }
  return { headers, rows };
}
