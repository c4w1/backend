# Backend API Reference

`server.mjs` is a single-file Node HTTP server (no framework, built on `node:http`). It listens on `process.env.BACKEND_PORT ?? 4322` and serves all responses as JSON (`Content-Type: application/json; charset=utf-8`).

## Data source

Every endpoint is backed by `loadAllSources()` / `loadSourceById(id)` in `lib/sourceStore.mjs`, which picks a backend automatically:

- **SQLite** (`lib/sourceFromSqlite.mjs`), if `database/sources.db` (or `SOURCES_DB_PATH`) exists — read-only `node:sqlite` `DatabaseSync`.
- **YAML** (`data/sources/*.yml`, parsed with the `yaml` npm package), as a fallback if the database file is missing.

Both paths return the identical JSON shape — the SQLite path reconstructs (`rehydrateSource()`) `provider`, `download`, `filters`, `variables`, `variable_report`, `description_tags`, and every `<name>_tags` block back into the same structure the YAML files use. Which backend is active is reported by `GET /api/health`.

## Endpoints

### `GET /api/health`

Liveness check and backend indicator.

```json
{ "ok": true, "service": "backend", "port": 4322, "dataSource": "sqlite" }
```

`dataSource` is `"sqlite"` or `"yaml"`.

### `GET /api/sources`

All sources.

```json
{ "count": 17, "data": [ { "id": "ebird-observation-dataset", ... }, ... ] }
```

### `GET /api/sources/:id`

Single source by id (URL-decoded path segment after `/api/sources/`).

- **200** — the source object (same shape as an item in `/api/sources`'s `data` array)
- **404** — `{ "error": "Source not found", "id": "<id>" }`

### Fallthrough

- Any other path — **404** `{ "error": "Not found" }`
- Any uncaught exception — **500** `{ "error": "Internal server error", "details": "<message>" }`

## Source object shape

```json
{
  "id": "ebird-observation-dataset",
  "version": 1,
  "title": "eBird Observation Dataset",
  "provider": { "name": "...", "agency": "...", "url": "..." },
  "filters": { "year": true, "state": true, "county": false, "zipcode": false },
  "requires_account": false,
  "download": { "url": "...", "description": "...", "file_size": "...", "sheet": "Data", "header_row": 4 },
  "sensitive": false,
  "variables": ["column_a", "column_b", "..."],
  "variable_report": [ { "name": "column_a", "type": "float", "missing_pct": 0.0, "...": "..." } ],
  "notes": "...",
  "description_tags": ["Wildlife & Nature"],
  "who": "...", "how": "...", "where": "...", "why": "...", "when": "...",
  "geographic_coverage": { "available_levels": { "...": true }, "url_template": { "...": "..." } },
  "anova_tags": { "good-for-one-way-anova": true, "...": false },
  "normality_tags": { "...": true },
  "...": "one block per statistical tagger — see PIPELINE.md"
}
```

Optional Excel defaults, set in the source YAML under `download:` (omitted from the object when unset; blank = auto-detect):

```yaml
download:
  url: https://example.gov/data.xlsx
  sheet: Data      # sheet name, or a 0-based index (e.g. 1); blank = first sheet
  header_row: 4    # 1-based row holding column names (integer >= 1); blank = auto-detect
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `BACKEND_PORT` | `4322` | HTTP listen port |
| `SOURCES_DB_PATH` | `database/sources.db` (relative to `lib/`) | SQLite database path. If the resolved path doesn't exist, the server falls back to reading YAML directly. |

## Running

```bash
npm start   # node --experimental-sqlite server.mjs
```

Node 22.5–22.12 requires the `--experimental-sqlite` flag for the built-in `node:sqlite` module (already included in `npm start`).
