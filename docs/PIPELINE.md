# Data Pipeline

Every dataset in CDL is a single YAML file in `data/sources/`, progressively enriched by a chain of Python scripts in `pythonscripts/` before being imported into SQLite for the API to serve. This document describes each stage, in the order they should be run, and the gotchas that come with that order not being enforced by any single command.

## Source file shape

A source YAML has, at minimum:

```yaml
id: ebird-observation-dataset
version: 1
title: eBird Observation Dataset
provider:
  name: Cornell Lab of Ornithology
  url: https://ebird.org
filters:
  year: true
  state: true
  county: false
  zipcode: false
requires_account: false
download:
  url: https://...
  description: ...
notes: ...
```

Downstream stages add to this same file: `description_tags`, `variable_names`, `variable_report`, `who`/`how`/`where`/`why`/`when`, `geographic_coverage`, and one `<name>_tags` block per statistical tagger (e.g. `anova_tags`, `normality_tags`).

### Coverage and granularity

Every source also records **which places it covers** (`coverage`) and **what one row describes** (`geographic_granularity`). The website's coverage map and community catalogs use these:

```yaml
coverage:                 # FIPS codes only; no geometry
- level: nation           # every county
# - level: state
#   fips: '47'            # every county in Tennessee
# - level: county
#   fips: '47093'         # Knox County, TN
geographic_granularity: county   # nation | state | county | zip | point
```

- **Valid codes:** state and county codes must be in `data/reference/geo-vocabulary.json`, the Census 2025 county list the website draws. `pythonscripts/tests/test_coverage_vocabulary.py` checks every YAML. The file is written by the web repo's `npm run geo:boundaries -- --backend ../backend`; don't edit it by hand.
- **Not used for coverage:** `geographic_coverage.available_levels` (from `geographic_coverage.py`) is a heuristic and is not used for coverage.
- **`where`** is free text; keep it consistent with `coverage`.
- **Curated tags:** `pedagogical_tags` are teaching tags written by people. The website shows them first, and they are not checked against the statistical tag vocabulary.

`import_sources_to_sqlite.py` sets a `pipeline_ready` flag when both `provider.url` and `download.url` are present — several stages (variable extraction, the statistical taggers) require `download.url` and will skip or fail on sources that don't have it.

## Stage order

There is no single "run everything" command — `runall.py` deliberately excludes some stages. Run in this order:

1. **`tagdataset.py`** — keyword-based topic tagging. Lowercases `title`, `description`, `download.description`, and provider fields into one text blob and matches against an ~18-category `TAG_RULES` table (Agriculture, Health & Social Services, Wildlife & Nature, etc.) using word-boundary regex. Writes `description_tags` (a sorted list). No network access — this stage was rewritten (see `docs/superpowers/specs/2026-08-11-dataset-tagging-design.md`) specifically to remove URL-scraping, which had been causing false-positive tags from page boilerplate (e.g. FBI crime data incorrectly tagged "Energy & Environment").

   ```bash
   python pythonscripts/tagdataset.py all
   ```

2. **`variableextraction.py`** — requires `download.url`. Downloads the dataset file (CSV/TSV/XLSX, or a ZIP containing one), profiles every column (type, missing/unique counts, min/max/mean for numeric columns, up to 5 sample values), and writes two fields: `variable_names` (shown to end users) and `variable_report` (richer stats, backend-only — see `ProcessingPipeline.txt`).

   ```bash
   python pythonscripts/variableextraction.py all
   ```

3. **`geographic_coverage.py`** — infers which geography levels (state FIPS, county FIPS, zip, tract, school district, tribal area) a source supports, layering signals from weakest to strongest: title/notes keywords → provider name → download URL pattern → `variable_names` columns → the explicit `filters` field (authoritative). For Census-pattern sources it also emits a parameterized `url_template` per geography level.

   ```bash
   python pythonscripts/geographic_coverage.py all
   ```

4. **`runall.py`** — runs every remaining tagger script (the full statistical battery below) except `variableextraction.py`, `community_identifiers.py`, and `detect_five_ws.py`.

   ```bash
   python pythonscripts/runall.py all
   ```

5. **`import_sources_to_sqlite.py`** — reads every YAML in `data/sources/`, applies `database/schema.sql`, and upserts into `database/sources.db`. **Must be re-run after any YAML edit or pipeline re-run** — nothing auto-invalidates the database, and the API will silently serve stale data (or fall back to reading YAML directly if the `.db` file is deleted).

   ```bash
   python pythonscripts/import_sources_to_sqlite.py all
   ```

6. **`export_catalog.py`**: exports the database to `data/exports/catalog.json`, which the website builds from. Commit it. `--check` fails if the committed export is stale.

   ```bash
   python pythonscripts/export_catalog.py
   ```

7. **`export_previews.py`** (Python standard library only): writes the Tier 1 preview files the website's Data Preview page reads. For every source in `catalog.json` with a `download.url`, it writes `data/exports/previews/<id>/`:
   - `manifest.json`, `variables.json`
   - `sample.json` (first rows)
   - `summary.json` (per-column statistics over every row)
   - `county/<FIPS>.json` (every row for one county)

   `previews/index.json` records which sources have previews and why the others don't.
   - **Formats:** CSV, a zip with a CSV inside, and Census-style `.xlsx` tables.
   - **County detection:** a 5-digit FIPS column, Census `STATE` + `COUNTY` codes, or county names plus a state code matched against the reference vocabulary.
   - **Size:** every file stays under 250 KB; oversized county slices are cut short and marked `truncated`, and their source is flagged as a Tier 2 candidate.
   - **Commit:** commit the output. Re-runs only change files whose data changed. Downloads are cached in `data/.cache/downloads/` (ignored).

   ```bash
   python pythonscripts/export_previews.py            # all; --offline uses only cached downloads
   ```

Optional, not part of the standard chain:

- **`detect_five_ws.py`** — infers `who`/`how`/where`/`when` (never `why` — left for a human to fill in) from provider/title/filter keywords. Flags: `--dry-run`, `--file <path>`, `--overwrite`.
- **`community_identifiers.py`** — unrelated to `data/sources/`. Given `--fips`, `--zip`, or `--state`+`--county`, resolves a community's full geographic identifier set via Census Bureau APIs, writing to `data/communities/<name>.yml`.

## Statistical taggers (run via `runall.py`)

Each downloads the dataset again and writes one `<name>_tags` block:

| Script | Writes | What it flags |
|---|---|---|
| `anova.py` | `anova_tags` | Fitness for one-way/two-way ANOVA, chi-square, paired t-test |
| `association.py` | `association_tags` | Correlation sign/strength, linear/nonlinear relationships across numeric pairs |
| `centraltendency.py` | `central_tendency_tags` | Mean vs. median similarity, mode presence |
| `comparisoncapabilities.py` | `comparison_capabilities_tags` | 2-group vs. multi-group comparisons, Levene's test |
| `distributionshape.py` | `distribution_shape_tags` | Symmetric/skewed/normal/uniform/bimodal |
| `inferenceprocedures.py` | `inference_procedures_tags` | Fitness for t-tests, z-tests, proportion tests |
| `normality.py` | `normality_tags` | Shapiro-Wilk pass/fail, CLT eligibility (n≥30) |
| `outliers.py` | `outlier_tags` | IQR-fence outliers/extreme outliers |
| `regressionquality.py` | `regression_quality_tags` | R² buckets, Cook's-distance influential points |
| `statisticalprocedures.py` | `statistical_procedures_tags` | Transformation candidacy (skew + scale) |
| `structureandcomplexity.py` | `structure_complexity_tags` | Univariate/bivariate/multivariate, dataset size bucket |
| `twowaytable.py` | `two_way_table_tags` | Exactly-2-categorical-variable detection |
| `valuescale.py` | `value_scale_tags` | Digit-count/scale buckets |
| `variability.py` | `variability_tags` | Coefficient-of-variation, range vs. IQR |
| `variabletypes.py` | `variable_types_tags` | All-discrete / all-continuous / mixed |
| `automationrules.py` | `automation_rules_tags` | Meta-flags for which downstream tests are applicable at all |
| `labelregression.py` | (boolean, not a tag block) | Whether the dataset is fit for a regression lesson (≥10 rows, ≥2 numeric columns, variance present, <80% missing) — reads the already-extracted `variable_report` rather than re-downloading |

## Loading into SQLite

`import_sources_to_sqlite.py` maps each YAML into 5 tables (`backend/database/schema.sql`):

- `sources` — one row per dataset (id, title, provider, download info, `pipeline_ready`)
- `source_filters` — the `filters` block
- `source_variables` — `variable_names`, frontend-visible only
- `variable_report` — per-column profiling stats, `sample_values` stored as JSON text
- `description_tags` — topic tags from `tagdataset.py`
- `analysis_tags` — every `<name>_tags` block, flattened as `(tag_set, tag_name, value)` rows

`backend/lib/sourceFromSqlite.mjs` rehydrates these rows back into the same JSON shape as the original YAML, so the HTTP API returns identical shapes whether it's reading SQLite or (as a fallback, when `database/sources.db` doesn't exist) the raw YAML files via `backend/lib/sourceStore.mjs`.

## Gotchas

- **Extensionless source files.** `import_sources_to_sqlite.py` picks up extensionless files in `data/sources/` (it globs `*.yml`/`*.yaml` *and* files without an extension), but every other pipeline script only globs `*.yml`. The one such file (`annual-estimates-of-resident-population-…`, a mislabeled duplicate of `census-annual-pop-by-age-sex-2025`) was deleted in `8a946d5` and intentionally not restored.
- **Sources without a direct download** (provider page only) are kept in the catalog: the website shows them as info only, and `export_previews.py` records "no direct download" for them. 15 such or previously unpreviewable sources were restored after `8a946d5` removed them for the old runtime preview.
- **Two copies of `tagdataset.py` exist in the wider project.** This backend's copy is canonical; `web/backend/pythonscripts/tagdataset.py` is a separately-diverged fork used only by the web mirror's local preview, intentionally not unified (see `docs/superpowers/specs/2026-08-11-dataset-tagging-design.md`).
- **`geographicidentifiers/community_geographic_identifiers.xlsx`** is not read by any script in this repo — treat it as a manual reference asset, not a pipeline input, unless that changes.
- **Tests**: `python -m pytest pythonscripts -q` covers tagging, the importer, the catalog export, the tag and coverage vocabularies, and the preview exporter. There are no automated tests for the Node/HTTP layer. On Windows, a few importer tests fail when cleaning up their temporary SQLite file (the file is still open); they pass on Linux CI.
