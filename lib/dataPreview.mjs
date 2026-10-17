import { getSourceTable } from "./csvCache.mjs";

const GEO_COLUMNS = {
  state: ["STATE", "STATEFP", "STATE_CODE", "ST"],
  stateName: ["STNAME", "STATE_NAME", "STATENAME"],
  county: ["COUNTY", "COUNTYFP", "COUNTY_CODE"],
  countyName: ["CTYNAME", "COUNTY_NAME", "COUNTYNAME"],
};

function findColumn(headers, candidates) {
  const upper = headers.map((h) => h.toUpperCase());
  for (const candidate of candidates) {
    const idx = upper.indexOf(candidate);
    if (idx !== -1) return headers[idx];
  }
  return null;
}

function rowToObject(headers, row) {
  const obj = {};
  headers.forEach((h, i) => {
    obj[h] = row[i] ?? "";
  });
  return obj;
}

function parseNumeric(value) {
  const cleaned = String(value).replace(/,/g, "").trim();
  if (cleaned === "") return null;
  const num = Number(cleaned);
  return Number.isFinite(num) ? num : null;
}

function selectPreviewColumns(source, headers, overrideColumns) {
  if (overrideColumns?.length) {
    return overrideColumns.filter((c) => headers.includes(c));
  }

  const variables = source.variables ?? [];
  const selected = variables.filter((v) => headers.includes(v));
  if (selected.length > 0) return selected.slice(0, 8);

  return headers.slice(0, 8);
}

function matchesFilter(rowObj, col, filterValue) {
  if (!col || !filterValue) return true;
  const cell = String(rowObj[col] ?? "").trim();
  const target = String(filterValue).trim();
  if (cell === target) return true;
  if (cell.toLowerCase() === target.toLowerCase()) return true;
  if (cell.padStart(target.length, "0") === target.padStart(cell.length, "0")) return true;
  return false;
}

function filterRows(headers, rows, { state, county }) {
  const stateCol = findColumn(headers, GEO_COLUMNS.state);
  const stateNameCol = findColumn(headers, GEO_COLUMNS.stateName);
  const countyCol = findColumn(headers, GEO_COLUMNS.county);
  const countyNameCol = findColumn(headers, GEO_COLUMNS.countyName);

  return rows.filter((row) => {
    const obj = rowToObject(headers, row);
    if (state) {
      const stateMatch =
        matchesFilter(obj, stateCol, state) || matchesFilter(obj, stateNameCol, state);
      if (!stateMatch) return false;
    }
    if (county) {
      const countyMatch =
        matchesFilter(obj, countyCol, county) || matchesFilter(obj, countyNameCol, county);
      if (!countyMatch) return false;
    }
    return true;
  });
}

function defaultFocalVariable(source, headers) {
  const report = source.variable_report ?? [];
  const numeric = report.find(
    (v) => (v.type === "integer" || v.type === "float") && headers.includes(v.name),
  );
  if (numeric) return numeric.name;

  const preferred = ["POPESTIMATE2025", "POPESTIMATE2024", "VALUE", "TOTAL"];
  for (const name of preferred) {
    if (headers.includes(name)) return name;
  }

  for (const name of source.variables ?? []) {
    if (headers.includes(name)) return name;
  }

  return headers[headers.length - 1] ?? null;
}

export function getAvailableFilters(headers, source) {
  const hasState =
    Boolean(findColumn(headers, GEO_COLUMNS.state)) ||
    Boolean(findColumn(headers, GEO_COLUMNS.stateName));
  const hasCounty =
    Boolean(findColumn(headers, GEO_COLUMNS.county)) ||
    Boolean(findColumn(headers, GEO_COLUMNS.countyName));

  return {
    state: source.filters?.state ?? hasState,
    county: source.filters?.county ?? hasCounty,
    zipcode: source.filters?.zipcode ?? false,
    year: source.filters?.year ?? false,
  };
}

export async function buildPreview(source, options = {}) {
  const { state, county, limit = 25, offset = 0, columns: columnsOverride } = options;
  const { headers, rows } = await getSourceTable(source);

  const filtered = filterRows(headers, rows, { state, county });
  const columns = selectPreviewColumns(
    source,
    headers,
    columnsOverride ? columnsOverride.split(",").map((c) => c.trim()) : null,
  );

  const slice = filtered.slice(offset, offset + limit);
  const previewRows = slice.map((row) => {
    const obj = rowToObject(headers, row);
    return columns.map((col) => obj[col] ?? "");
  });

  return {
    id: source.id,
    columns,
    rows: previewRows,
    total: filtered.length,
    filters: { state: state ?? null, county: county ?? null },
    availableFilters: getAvailableFilters(headers, source),
  };
}

export async function buildChart(source, options = {}) {
  const { variable, state, county, limit = 50 } = options;
  const { headers, rows } = await getSourceTable(source);

  const focalVariable = variable || defaultFocalVariable(source, headers);
  if (!focalVariable || !headers.includes(focalVariable)) {
    throw new Error(`Focal variable not found: ${focalVariable ?? "none"}`);
  }

  const filtered = filterRows(headers, rows, { state, county });
  const labelCol =
    findColumn(headers, GEO_COLUMNS.countyName) ||
    findColumn(headers, GEO_COLUMNS.stateName) ||
    findColumn(headers, GEO_COLUMNS.county) ||
    headers[0];

  const series = [];
  for (const row of filtered) {
    const obj = rowToObject(headers, row);
    const value = parseNumeric(obj[focalVariable]);
    if (value === null) continue;
    const label = String(obj[labelCol] ?? `Row ${series.length + 1}`).trim();
    series.push({ label, value });
    if (series.length >= limit) break;
  }

  series.sort((a, b) => b.value - a.value);

  return {
    variable: focalVariable,
    label: focalVariable.replace(/_/g, " "),
    series,
  };
}

export async function listFilterOptions(source, filterType, parentFilter = {}) {
  const { headers, rows } = await getSourceTable(source);
  const { state } = parentFilter;

  if (filterType === "state") {
    const codeCol = findColumn(headers, GEO_COLUMNS.state);
    const nameCol = findColumn(headers, GEO_COLUMNS.stateName);
    const seen = new Map();
    for (const row of rows) {
      const obj = rowToObject(headers, row);
      const code = codeCol ? String(obj[codeCol] ?? "").trim() : "";
      const name = nameCol ? String(obj[nameCol] ?? "").trim() : "";
      if (code && name) seen.set(code, name);
      else if (name) seen.set(name, name);
      else if (code) seen.set(code, code);
    }
    return [...seen.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }

  if (filterType === "county") {
    const stateCol = findColumn(headers, GEO_COLUMNS.state);
    const stateNameCol = findColumn(headers, GEO_COLUMNS.stateName);
    const codeCol = findColumn(headers, GEO_COLUMNS.county);
    const nameCol = findColumn(headers, GEO_COLUMNS.countyName);
    const seen = new Map();
    for (const row of rows) {
      const obj = rowToObject(headers, row);
      if (state) {
        const stateMatch =
          matchesFilter(obj, stateCol, state) || matchesFilter(obj, stateNameCol, state);
        if (!stateMatch) continue;
      }
      const code = codeCol ? String(obj[codeCol] ?? "").trim() : "";
      const name = nameCol ? String(obj[nameCol] ?? "").trim() : "";
      if (code && name) seen.set(code, name);
      else if (name) seen.set(name, name);
      else if (code) seen.set(code, code);
    }
    return [...seen.entries()]
      .map(([value, label]) => ({ value, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }

  return [];
}
