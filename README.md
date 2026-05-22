# backend

## Requirements

- **Node.js 22.5+** (uses built-in `node:sqlite` for the API data store)
- Python 3 with dependencies from `pythonscripts/requirements.txt`

Install Python dependencies:

```bash
pip install -r pythonscripts/requirements.txt
```

Install the Node `yaml` package (used when falling back to YAML files):

```bash
npm install yaml
```

## Data workflow

Source metadata is authored as YAML in `data/sources/`. For the HTTP API, import into SQLite first:

```bash
python pythonscripts/import_sources_to_sqlite.py all
node server.mjs
```

Re-run the import after editing YAML or running pipeline scripts (`tagdataset.py`, `variableextraction.py`, etc.) so the API reflects changes.

## Server

On Node.js 22.5 through 22.12, SQLite requires the experimental flag:

```bash
npm start
```

Or directly:

```bash
node --experimental-sqlite server.mjs
```

Optional environment variables:

- `BACKEND_PORT` — listen port (default `4322`)
- `SOURCES_DB_PATH` — path to SQLite database (default `database/sources.db`)

If `database/sources.db` is missing, the server falls back to reading `data/sources/*.yml` directly.

## API

- `GET /api/health` — includes `dataSource`: `"sqlite"` or `"yaml"`
- `GET /api/sources` — list all sources
- `GET /api/sources/:id` — single source by id
