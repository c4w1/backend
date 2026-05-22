-- Source metadata imported from data/sources/*.yml

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    yml_filename TEXT NOT NULL,
    version TEXT,
    title TEXT,
    notes TEXT,
    provider_name TEXT,
    provider_agency TEXT,
    website_url TEXT,
    download_url TEXT,
    download_description TEXT,
    download_file_size TEXT,
    requires_account INTEGER NOT NULL DEFAULT 0,
    sensitive INTEGER NOT NULL DEFAULT 0,
    pipeline_ready INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_filters (
    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    year INTEGER,
    state INTEGER,
    county INTEGER,
    zipcode INTEGER
);

CREATE TABLE IF NOT EXISTS source_variables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    variable_name TEXT NOT NULL,
    sort_order INTEGER NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'frontend',
    UNIQUE (source_id, variable_name, visibility)
);

CREATE TABLE IF NOT EXISTS variable_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    type TEXT,
    total_rows INTEGER,
    non_empty_count INTEGER,
    missing_count INTEGER,
    missing_pct REAL,
    unique_count INTEGER,
    min_value REAL,
    max_value REAL,
    mean_value REAL,
    sample_values TEXT
);

CREATE TABLE IF NOT EXISTS description_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE (source_id, tag_name)
);

CREATE TABLE IF NOT EXISTS analysis_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    tag_set TEXT NOT NULL,
    tag_name TEXT NOT NULL,
    value INTEGER NOT NULL,
    UNIQUE (source_id, tag_set, tag_name)
);

CREATE INDEX IF NOT EXISTS idx_source_variables_source_id ON source_variables(source_id);
CREATE INDEX IF NOT EXISTS idx_variable_report_source_id ON variable_report(source_id);
CREATE INDEX IF NOT EXISTS idx_description_tags_source_id ON description_tags(source_id);
CREATE INDEX IF NOT EXISTS idx_analysis_tags_source_id ON analysis_tags(source_id);
