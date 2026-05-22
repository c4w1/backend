import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const defaultDbPath = path.resolve(__dirname, "../database/sources.db");

export function getDefaultDbPath() {
  return process.env.SOURCES_DB_PATH
    ? path.resolve(process.env.SOURCES_DB_PATH)
    : defaultDbPath;
}

export function isSqliteAvailable(dbPath = getDefaultDbPath()) {
  return existsSync(dbPath);
}

function openDatabase(dbPath) {
  return new DatabaseSync(dbPath, { readOnly: true });
}

function intToBool(value) {
  return value === 1;
}

function buildProvider(row) {
  const provider = {};
  if (row.provider_name) provider.name = row.provider_name;
  if (row.provider_agency) provider.agency = row.provider_agency;
  if (row.website_url) provider.url = row.website_url;
  return Object.keys(provider).length > 0 ? provider : null;
}

function buildDownload(row) {
  const download = {};
  if (row.download_url) download.url = row.download_url;
  if (row.download_description) download.description = row.download_description;
  if (row.download_file_size) download.file_size = row.download_file_size;
  return Object.keys(download).length > 0 ? download : null;
}

function buildFilters(db, sourceId) {
  const row = db
    .prepare(
      "SELECT year, state, county, zipcode FROM source_filters WHERE source_id = ?",
    )
    .get(sourceId);
  if (!row) return null;
  return {
    year: intToBool(row.year),
    state: intToBool(row.state),
    county: intToBool(row.county),
    zipcode: intToBool(row.zipcode),
  };
}

function buildVariables(db, sourceId) {
  const rows = db
    .prepare(
      `SELECT variable_name FROM source_variables
       WHERE source_id = ? AND visibility = 'frontend'
       ORDER BY sort_order`,
    )
    .all(sourceId);
  return rows.length > 0 ? rows.map((r) => r.variable_name) : null;
}

function buildVariableReport(db, sourceId) {
  const rows = db
    .prepare(
      `SELECT name, type, total_rows, non_empty_count, missing_count, missing_pct,
              unique_count, min_value, max_value, mean_value, sample_values
       FROM variable_report WHERE source_id = ? ORDER BY id`,
    )
    .all(sourceId);
  if (rows.length === 0) return null;

  return rows.map((row) => {
    const entry = {
      name: row.name,
      type: row.type,
      total_rows: row.total_rows,
      non_empty_count: row.non_empty_count,
      missing_count: row.missing_count,
      missing_pct: row.missing_pct,
      unique_count: row.unique_count,
    };
    if (row.min_value != null) entry.min = row.min_value;
    if (row.max_value != null) entry.max = row.max_value;
    if (row.mean_value != null) entry.mean = row.mean_value;
    if (row.sample_values != null) {
      try {
        entry.sample_values = JSON.parse(row.sample_values);
      } catch {
        entry.sample_values = row.sample_values;
      }
    }
    return entry;
  });
}

function buildDescriptionTags(db, sourceId) {
  const rows = db
    .prepare(
      "SELECT tag_name, active FROM description_tags WHERE source_id = ? ORDER BY tag_name",
    )
    .all(sourceId);
  if (rows.length === 0) return null;

  const allActive = rows.every((r) => r.active === 1);
  if (allActive) {
    return rows.map((r) => r.tag_name);
  }
  const tags = {};
  for (const row of rows) {
    tags[row.tag_name] = intToBool(row.active);
  }
  return tags;
}

function buildAnalysisTags(db, sourceId) {
  const rows = db
    .prepare(
      "SELECT tag_set, tag_name, value FROM analysis_tags WHERE source_id = ? ORDER BY tag_set, tag_name",
    )
    .all(sourceId);
  if (rows.length === 0) return {};

  const grouped = {};
  for (const row of rows) {
    if (!grouped[row.tag_set]) grouped[row.tag_set] = {};
    grouped[row.tag_set][row.tag_name] = intToBool(row.value);
  }
  return grouped;
}

function rehydrateSource(db, row) {
  const source = {
    file: row.yml_filename,
    id: row.id,
  };
  if (row.version) source.version = row.version;
  if (row.title) source.title = row.title;
  if (row.notes) source.notes = row.notes;

  const provider = buildProvider(row);
  if (provider) source.provider = provider;

  const download = buildDownload(row);
  if (download) source.download = download;

  source.requires_account = intToBool(row.requires_account);
  source.sensitive = intToBool(row.sensitive);

  const filters = buildFilters(db, row.id);
  if (filters) source.filters = filters;

  const variables = buildVariables(db, row.id);
  if (variables) source.variables = variables;

  const variableReport = buildVariableReport(db, row.id);
  if (variableReport) source.variable_report = variableReport;

  const descriptionTags = buildDescriptionTags(db, row.id);
  if (descriptionTags) source.description_tags = descriptionTags;

  Object.assign(source, buildAnalysisTags(db, row.id));

  return source;
}

function loadSources(db, id = null) {
  const rows = id
    ? db.prepare("SELECT * FROM sources WHERE id = ?").all(id)
    : db.prepare("SELECT * FROM sources ORDER BY id").all();

  return rows.map((row) => rehydrateSource(db, row));
}

export function loadAllSourcesFromSqlite(dbPath = getDefaultDbPath()) {
  const db = openDatabase(dbPath);
  try {
    return loadSources(db);
  } finally {
    db.close();
  }
}

export function loadSourceByIdFromSqlite(id, dbPath = getDefaultDbPath()) {
  const db = openDatabase(dbPath);
  try {
    const sources = loadSources(db, id);
    return sources[0] ?? null;
  } finally {
    db.close();
  }
}
