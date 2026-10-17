# Metadata Editor with Magic-Link Auth — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let allowlisted editors sign in by magic link on the Data Preview page and submit dataset-metadata edits (including tags from a fixed vocabulary) as pull requests against `community-data-libraries/backend`.

**Architecture:** Netlify Functions (v2, ESM) in `web/netlify/functions/` handle login (HMAC-signed link tokens and session cookies, Netlify Blobs for nonces and rate limits, Resend for email) and editing (GitHub REST API: branch → commit → PR). Edits replace only the changed top-level YAML blocks so PR diffs are minimal. In `backend/`, a tag vocabulary file defines allowed tags, the pipeline preserves hand-set tags listed in `manual_tags`, and merges to `main` trigger a Netlify rebuild that syncs canonical YAML.

**Tech Stack:** Node 22 (`node:test`, `node:crypto`, global `fetch`), `yaml` 2.x, `@netlify/blobs`, Netlify Functions v2, Astro 6 (static), Python 3 + PyYAML + pytest, GitHub Actions.

**Spec:** `backend/docs/superpowers/specs/2026-10-14-metadata-editor-auth-design.md`

## Global Constraints

- Two repos: `backend/` = `/Users/arielthompson/Documents/CDL/backend`, `web/` = `/Users/arielthompson/Documents/CDL/web`. Each task says which. Work on branch `feat/metadata-editor` in each repo, created from that repo's `main`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Magic-link token TTL: 15 minutes. Session TTL: 7 days. Cookie: `cdl_session`, `HttpOnly; Secure; SameSite=Lax; Path=/`.
- Rate limits: 3 link requests per email per 15 min; 20 per IP per hour.
- `AUTH_SECRET` must be ≥ 32 characters.
- Environment variables: `EDITOR_EMAILS`, `AUTH_SECRET`, `RESEND_API_KEY`, `MAIL_FROM`, `GITHUB_TOKEN`, `GITHUB_REPO` (default `community-data-libraries/backend`), `SITE_ORIGIN`.
- `auth-request-link` returns the identical 200 body for allowed, unknown, and rate-limited emails.
- Dataset ids must match `^[a-z0-9-]+$`; files live at `data/sources/<id>.yml`.
- Vocabulary file: `backend/data/tag_vocabulary.yml`, keys `description_tags` (list) and `statistical_tags` (map of group → list of keys).
- Editing is Netlify-only; the editor UI hides itself when `/.netlify/functions/auth-me` is unreachable.
- YAML replacement blocks are emitted with `{ indentSeq: false, lineWidth: 80, minContentWidth: 0, version: '1.1' }`.
- Web function tests: `npm run test:functions` (`node --test`), no network.
- Never put test or helper files directly in `netlify/functions/`: every top-level file there is deployed as a public function. Tests of `_lib` modules sit next to them in `netlify/functions/_lib/`; tests of whole functions go in `netlify/tests/`.
- Backend tests: `python -m pytest pythonscripts -q` from `backend/`.

## Review Focus

1. **Email scanners pre-fetching the link** — opening the link with GET must never consume it; only the confirm-page POST does. Test in Task 7.
2. **Strings that PyYAML 1.1 reads as non-strings** (`yes`, `on`, `No`, `2025`, `null`, `0777`) entered as a title or `when` — must round-trip through PyYAML as strings. Test in Task 5.
3. **Email casing and whitespace** (`"  Alice@Example.ORG "`) in `EDITOR_EMAILS` or the form — must match the same person. Test in Task 3.
4. **Editing a tag group that the dataset file doesn't contain yet** (e.g. `anova_tags` missing) — form shows all vocabulary keys unchecked; saving appends the full group plus `manual_tags`. Test in Task 6.
5. **Form loaded, then `main` changed before submit** — only submitted fields are applied, onto the latest `main` content; other fields' newer values are kept. Test in Task 8.

---

## Part A — backend repo

### Task 1: Tag vocabulary file and consistency test

**Repo:** `backend/`

**Files:**
- Create: `data/tag_vocabulary.yml`
- Create: `pythonscripts/tag_vocabulary.py`
- Test: `pythonscripts/tests/test_tag_vocabulary.py`

**Interfaces:**
- Produces: `tag_vocabulary.load_vocabulary(path: Path = VOCABULARY_PATH) -> dict` returning `{"description_tags": list[str], "statistical_tags": dict[str, list[str]]}`; constant `VOCABULARY_PATH`. The YAML file layout is consumed by Task 8 (`metadata-edit`).

- [ ] **Step 1: Write the failing test**

`pythonscripts/tests/test_tag_vocabulary.py`:

```python
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tag_vocabulary
import tagdataset

SOURCES_DIR = Path(__file__).resolve().parents[2] / "data" / "sources"


def _sources():
    files = sorted(SOURCES_DIR.glob("*.yml"))
    assert files, "expected source YAML files"
    return [(f.name, yaml.safe_load(f.read_text(encoding="utf-8"))) for f in files]


def test_vocabulary_shape():
    vocab = tag_vocabulary.load_vocabulary()
    assert isinstance(vocab["description_tags"], list)
    assert isinstance(vocab["statistical_tags"], dict)
    for group, keys in vocab["statistical_tags"].items():
        assert group.endswith("_tags")
        assert keys and len(keys) == len(set(keys)), group


def test_description_vocabulary_matches_tag_rules():
    vocab = tag_vocabulary.load_vocabulary()
    assert sorted(vocab["description_tags"]) == sorted(tagdataset.TAG_RULES)


def test_sources_use_only_vocabulary_description_tags():
    allowed = set(tag_vocabulary.load_vocabulary()["description_tags"])
    for name, data in _sources():
        unknown = set(data.get("description_tags") or []) - allowed
        assert not unknown, f"{name}: unknown description_tags {sorted(unknown)}"


def test_sources_use_only_vocabulary_statistical_tags():
    groups = tag_vocabulary.load_vocabulary()["statistical_tags"]
    for name, data in _sources():
        for key, value in data.items():
            if not key.endswith("_tags") or key == "description_tags":
                continue
            assert key in groups, f"{name}: tag group {key} missing from vocabulary"
            unknown = set(value) - set(groups[key])
            assert not unknown, f"{name}: {key} has unknown keys {sorted(unknown)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest pythonscripts/tests/test_tag_vocabulary.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'tag_vocabulary'`

- [ ] **Step 3: Create the vocabulary file**

`data/tag_vocabulary.yml` (union of `TAG_RULES` keys, the keys each analysis script emits, and keys present in current data):

```yaml
# Allowed tag values for the metadata editor and the pipeline.
# The tagging redesign edits this file together with TAG_RULES in
# pythonscripts/tagdataset.py; pythonscripts/tests/test_tag_vocabulary.py
# keeps them, and every data/sources/*.yml, consistent.
description_tags:
- Agriculture
- Business & Economy
- Children & Families
- Elections & Politics
- PreK-12 Education
- Energy & Environment
- Government Administration
- Health & Social Services
- Higher Education
- Historic Preservation
- Housing & Buildings
- Labor & Workforce Development
- Law & Public Safety
- Parks & Recreation
- Population Data
- Public Utilities
- Restaurant and Food Service
- Transportation
- Wildlife & Nature
statistical_tags:
  anova_tags:
  - good-for-one-way-anova
  - good-for-two-way-anova
  - good-for-chi-square-gof
  - good-for-chi-square-independence
  - good-for-paired-t-test
  association_tags:
  - positive-correlation
  - negative-correlation
  - no-correlation
  - strong-correlation
  - moderate-correlation
  - weak-correlation
  - linear-relationship
  - nonlinear-relationship
  - curved-relationship
  - exponential-pattern
  automation_rules_tags:
  - has-categorical-variable
  - has-group-comparison-candidates
  - has-multiple-numeric-columns
  - has-paired-test-candidates
  - meets-minimum-sample-size
  central_tendency_tags:
  - has-mode
  - mean-median-similar
  - mean-median-different
  comparison_capabilities_tags:
  - two-groups-present
  - multiple-groups-present
  - groups-have-different-centers
  - groups-have-different-spreads
  - groups-overlap-substantially
  distribution_shape_tags:
  - approximately-normal
  - symmetric-distribution
  - left-skewed-distribution
  - right-skewed-distribution
  - bimodal-distribution
  - uniform-distribution
  inference_procedures_tags:
  - good-for-one-sample-t-test
  - good-for-two-sample-t-test
  - good-for-one-sample-z-test
  - good-for-two-sample-z-test
  - good-for-one-proportion-z-test
  - good-for-two-proportion-z-test
  - meets-equal-variance-assumption
  - violates-equal-variance-assumption
  normality_tags:
  - passes-normality-test
  - fails-normality-test
  - large-enough-for-clt
  outlier_tags:
  - no-outliers
  - contains-outliers
  - extreme-outliers
  regression_quality_tags:
  - regression-appropriate
  - good-linear-fit
  - moderate-linear-fit
  - poor-linear-fit
  - has-influential-points
  statistical_procedures_tags:
  - good-for-transformation
  structure_complexity_tags:
  - univariate
  - bivariate
  - multivariate
  - small-dataset
  - medium-dataset
  - large-dataset
  two_way_table_tags:
  - two-way-table
  value_scale_tags:
  - single-digit-values
  - double-digit-values
  - three-digit-values
  - large-scale-values
  variability_tags:
  - low-variability
  - moderate-variability
  - high-variability
  - restricted-range
  - wide-range
  variable_types_tags:
  - all-discrete
  - all-continuous
  - mixed-types
```

- [ ] **Step 4: Write the loader**

`pythonscripts/tag_vocabulary.py`:

```python
"""Load data/tag_vocabulary.yml, the allowed values for description and statistical tags."""

from __future__ import annotations

from pathlib import Path

import yaml

VOCABULARY_PATH = Path(__file__).resolve().parent.parent / "data" / "tag_vocabulary.yml"


def load_vocabulary(path: Path = VOCABULARY_PATH) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return {
        "description_tags": list(data.get("description_tags") or []),
        "statistical_tags": {k: list(v) for k, v in (data.get("statistical_tags") or {}).items()},
    }
```

Add `"tag_vocabulary.py"` to `EXCLUDED_FILES` in `pythonscripts/runall.py` (it is a library, not a pipeline step):

```python
EXCLUDED_FILES = {"variableextraction.py", "runall.py", "community_identifiers.py", "detect_five_ws.py",
                   "test_tagdataset.py", "tag_vocabulary.py"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest pythonscripts -q`
Expected: all pass (previous 21 + 4 new).

- [ ] **Step 6: Commit**

```bash
git add data/tag_vocabulary.yml pythonscripts/tag_vocabulary.py pythonscripts/tests/test_tag_vocabulary.py pythonscripts/runall.py
git commit -m "Add tag vocabulary file with consistency tests

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Preserve hand-set tags across pipeline runs

**Repo:** `backend/`

**Files:**
- Create: `pythonscripts/manual_tags.py`
- Modify: `pythonscripts/runall.py` (main loop; `EXCLUDED_FILES`)
- Modify: `pythonscripts/tagdataset.py:162-182` (`process_file`)
- Test: `pythonscripts/tests/test_manual_tags.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `manual_tags.snapshot(sources_dir: Path) -> dict[str, dict[str, Any]]` (filename → {field: value} for fields listed in that file's `manual_tags`); `manual_tags.restore(sources_dir: Path, snap: dict) -> list[str]` (rewrites only files whose listed fields changed; returns their names); `manual_tags.is_manual(data: dict, field: str) -> bool`. The YAML key `manual_tags` (list of field names) is written by Task 8.

- [ ] **Step 1: Write the failing tests**

`pythonscripts/tests/test_manual_tags.py`:

```python
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import manual_tags
import tagdataset


def _write(path, data):
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _read(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_snapshot_captures_only_listed_fields(tmp_path):
    _write(tmp_path / "a.yml", {
        "title": "A",
        "description_tags": ["Agriculture"],
        "anova_tags": {"good-for-one-way-anova": True},
        "manual_tags": ["description_tags"],
    })
    _write(tmp_path / "b.yml", {"title": "B", "description_tags": ["Transportation"]})
    snap = manual_tags.snapshot(tmp_path)
    assert snap == {"a.yml": {"description_tags": ["Agriculture"]}}


def test_restore_reverts_pipeline_overwrites(tmp_path):
    path = tmp_path / "a.yml"
    _write(path, {"title": "A", "description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    snap = manual_tags.snapshot(tmp_path)
    _write(path, {"title": "A", "description_tags": ["Transportation"], "manual_tags": ["description_tags"]})

    changed = manual_tags.restore(tmp_path, snap)

    assert changed == ["a.yml"]
    assert _read(path)["description_tags"] == ["Agriculture"]


def test_restore_leaves_untouched_files_alone(tmp_path):
    path = tmp_path / "a.yml"
    _write(path, {"description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    before = path.read_bytes()
    assert manual_tags.restore(tmp_path, manual_tags.snapshot(tmp_path)) == []
    assert path.read_bytes() == before


def test_is_manual():
    assert manual_tags.is_manual({"manual_tags": ["description_tags"]}, "description_tags")
    assert not manual_tags.is_manual({}, "description_tags")
    assert not manual_tags.is_manual({"manual_tags": "description_tags"}, "description_tags")


def test_tagdataset_skips_manual_description_tags(tmp_path, monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Transportation": ["bus"]})
    path = tmp_path / "a.yml"
    _write(path, {"title": "bus routes", "description_tags": ["Agriculture"], "manual_tags": ["description_tags"]})
    tagdataset.process_file(path=path, dry_run=False)
    assert _read(path)["description_tags"] == ["Agriculture"]


def test_tagdataset_still_tags_when_not_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(tagdataset, "TAG_RULES", {"Transportation": ["bus"]})
    path = tmp_path / "a.yml"
    _write(path, {"title": "bus routes", "description_tags": ["Agriculture"]})
    tagdataset.process_file(path=path, dry_run=False)
    assert _read(path)["description_tags"] == ["Transportation"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest pythonscripts/tests/test_manual_tags.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'manual_tags'`

- [ ] **Step 3: Implement `manual_tags.py`**

```python
"""Preserve tag fields that an editor set by hand.

A source YAML lists hand-set fields under `manual_tags`, e.g.
`manual_tags: [description_tags, anova_tags]`. runall.py snapshots those
fields before the analysis scripts run and restores them after each one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _listed(data: dict) -> list[str]:
    value = data.get("manual_tags")
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def is_manual(data: dict, field: str) -> bool:
    return field in _listed(data)


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def snapshot(sources_dir: Path) -> dict[str, dict[str, Any]]:
    snap: dict[str, dict[str, Any]] = {}
    for path in sorted(sources_dir.glob("*.yml")):
        data = _load(path)
        fields = {f: data[f] for f in _listed(data) if f in data}
        if fields:
            snap[path.name] = fields
    return snap


def restore(sources_dir: Path, snap: dict[str, dict[str, Any]]) -> list[str]:
    changed: list[str] = []
    for name, fields in snap.items():
        path = sources_dir / name
        if not path.exists():
            continue
        data = _load(path)
        if all(data.get(k) == v for k, v in fields.items()):
            continue
        data.update(fields)
        path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=False), encoding="utf-8")
        changed.append(name)
    return changed
```

- [ ] **Step 4: Make `tagdataset.process_file` respect `manual_tags`**

In `pythonscripts/tagdataset.py`, add `import manual_tags` next to the other imports, and in `process_file` insert right after the `isinstance(data, dict)` check:

```python
    if manual_tags.is_manual(data, "description_tags"):
        print("  - Skipped (description_tags set by hand; see manual_tags)")
        return
```

- [ ] **Step 5: Wire snapshot/restore into `runall.py`**

```python
import manual_tags

SOURCES_DIR = SCRIPT_DIR.parent / "data" / "sources"
EXCLUDED_FILES = {"variableextraction.py", "runall.py", "community_identifiers.py", "detect_five_ws.py",
                   "test_tagdataset.py", "tag_vocabulary.py", "manual_tags.py"}
```

and replace the loop in `main()`:

```python
    preserved = manual_tags.snapshot(SOURCES_DIR)

    for script_path in scripts:
        print(f"\n=== Running {script_path.name} on {target} ===")
        result = subprocess.run(
            [sys.executable, str(script_path), target],
            cwd=SCRIPT_DIR,
        )
        restored = manual_tags.restore(SOURCES_DIR, preserved)
        if restored:
            print(f"  (restored hand-set tags in {', '.join(restored)})")
        if result.returncode != 0:
            raise SystemExit(result.returncode)
```

Restoring after every script (not only at the end) matters because `import_sources_to_sqlite.py` and `export_catalog.py` run mid-sequence and must see the hand-set values.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest pythonscripts -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add pythonscripts/manual_tags.py pythonscripts/tests/test_manual_tags.py pythonscripts/tagdataset.py pythonscripts/runall.py
git commit -m "Preserve hand-set tags listed in manual_tags across pipeline runs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

Part B continues with Task 3. Task 11 (the backend build-hook workflow) comes last, after the web build change it triggers.

---

## Part B — web repo

All files in this part are relative to `web/`. Function modules are ESM (`.mjs`) using Netlify Functions v2 (`export default async (request, context) => Response`). The existing `submission-notify.js` stays as is.

### Task 3: Test runner, config, tokens, allowlist, HTTP helpers

**Files:**
- Modify: `package.json` (scripts, dependency)
- Modify: `.github/workflows/ci.yml` (test step)
- Create: `netlify/functions/_lib/config.mjs`, `tokens.mjs`, `allowlist.mjs`, `http.mjs`
- Test: `netlify/functions/_lib/tokens.test.mjs`, `allowlist.test.mjs`, `config.test.mjs`

**Interfaces:**
- Produces:
  - `loadConfig(env = process.env) -> { authSecret, editorEmails, siteOrigin, resendApiKey, mailFrom, githubToken, githubRepo, previewPath }` — throws `Error` if `AUTH_SECRET`, `EDITOR_EMAILS`, or `SITE_ORIGIN` is missing or `AUTH_SECRET.length < 32`. `siteOrigin` has no trailing slash; `previewPath` is `'/web/data-preview'`.
  - `signToken(payload: object, secret: string) -> string`
  - `verifyToken(token: string|null, secret: string, { purpose: string, now?: number }) -> { payload } | { error: 'invalid' | 'expired' }`
  - `newNonce() -> string`
  - `normalizeEmail(value: unknown) -> string`, `isAllowed(email: string, rawList: string) -> boolean`, `isValidEmail(email: string) -> boolean`
  - `class HttpError extends Error { status: number; field?: string }` with `new HttpError(status, message, field?)`
  - `json(status: number, body: object, headers?: object) -> Response`, `toResponse(err: unknown) -> Response`

- [ ] **Step 1: Add the test script, dependency, and CI step**

```bash
npm install @netlify/blobs
```

In `package.json` `scripts`, add:

```json
"test:functions": "node --test \"netlify/**/*.test.mjs\" \"src/lib/editor/*.test.mjs\""
```

In `.github/workflows/ci.yml`, after "Install dependencies":

```yaml
      - name: Function tests
        run: npm run test:functions
```

- [ ] **Step 2: Write the failing tests**

`netlify/functions/_lib/tokens.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { signToken, verifyToken, newNonce } from './tokens.mjs';

const SECRET = 'x'.repeat(32);
const NOW = 1_800_000_000_000;

test('round-trips a valid token', () => {
  const token = signToken({ email: 'a@b.org', exp: NOW + 1000, purpose: 'link' }, SECRET);
  assert.deepEqual(verifyToken(token, SECRET, { purpose: 'link', now: NOW }).payload.email, 'a@b.org');
});

test('rejects expired tokens as expired', () => {
  const token = signToken({ email: 'a@b.org', exp: NOW, purpose: 'link' }, SECRET);
  assert.deepEqual(verifyToken(token, SECRET, { purpose: 'link', now: NOW }), { error: 'expired' });
});

test('rejects wrong purpose, tampering, wrong secret, and garbage as invalid', () => {
  const token = signToken({ email: 'a@b.org', exp: NOW + 1000, purpose: 'link' }, SECRET);
  const [body, sig] = token.split('.');
  const forged = Buffer.from(JSON.stringify({ email: 'evil@x.org', exp: NOW + 1000, purpose: 'link' })).toString('base64url');
  for (const [t, secret, purpose] of [
    [token, SECRET, 'session'],
    [`${forged}.${sig}`, SECRET, 'link'],
    [token, 'y'.repeat(32), 'link'],
    [`${body}.${sig}.extra`, SECRET, 'link'],
    ['not-a-token', SECRET, 'link'],
    [null, SECRET, 'link'],
  ]) {
    assert.deepEqual(verifyToken(t, secret, { purpose, now: NOW }), { error: 'invalid' });
  }
});

test('nonces are random and URL-safe', () => {
  const a = newNonce();
  assert.match(a, /^[A-Za-z0-9_-]{22}$/);
  assert.notEqual(a, newNonce());
});
```

`netlify/functions/_lib/allowlist.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { normalizeEmail, isAllowed, isValidEmail } from './allowlist.mjs';

test('normalizes case and whitespace on both sides', () => {
  const list = ' Alice@Example.ORG , bob@cdl.org ,,';
  assert.equal(isAllowed('  alice@example.org ', list), true);
  assert.equal(isAllowed('ALICE@EXAMPLE.ORG', list), true);
  assert.equal(isAllowed('bob@cdl.org', list), true);
});

test('rejects unknown, empty, and non-string emails', () => {
  assert.equal(isAllowed('carol@cdl.org', 'alice@example.org'), false);
  assert.equal(isAllowed('', 'alice@example.org,'), false);
  assert.equal(isAllowed(undefined, 'alice@example.org'), false);
  assert.equal(normalizeEmail(42), '');
});

test('validates email shape and length', () => {
  assert.equal(isValidEmail('a@b.org'), true);
  assert.equal(isValidEmail('no-at-sign'), false);
  assert.equal(isValidEmail('a b@c.org'), false);
  assert.equal(isValidEmail(`${'a'.repeat(250)}@b.org`), false);
});
```

`netlify/functions/_lib/config.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { loadConfig } from './config.mjs';

const BASE = { AUTH_SECRET: 's'.repeat(32), EDITOR_EMAILS: 'a@b.org', SITE_ORIGIN: 'https://cdl.example/' };

test('loads config with defaults', () => {
  const c = loadConfig(BASE);
  assert.equal(c.siteOrigin, 'https://cdl.example');
  assert.equal(c.githubRepo, 'community-data-libraries/backend');
  assert.equal(c.previewPath, '/web/data-preview');
});

test('requires core variables and a long secret', () => {
  assert.throws(() => loadConfig({ ...BASE, SITE_ORIGIN: '' }), /SITE_ORIGIN/);
  assert.throws(() => loadConfig({ ...BASE, AUTH_SECRET: 'short' }), /at least 32/);
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../tokens.mjs'` (and the others).

- [ ] **Step 4: Implement the modules**

`netlify/functions/_lib/config.mjs`:

```js
export function loadConfig(env = process.env) {
  for (const name of ['AUTH_SECRET', 'EDITOR_EMAILS', 'SITE_ORIGIN']) {
    if (!env[name]) throw new Error(`Missing environment variable ${name}`);
  }
  if (env.AUTH_SECRET.length < 32) throw new Error('AUTH_SECRET must be at least 32 characters');
  return {
    authSecret: env.AUTH_SECRET,
    editorEmails: env.EDITOR_EMAILS,
    siteOrigin: env.SITE_ORIGIN.replace(/\/+$/, ''),
    resendApiKey: env.RESEND_API_KEY,
    mailFrom: env.MAIL_FROM,
    githubToken: env.GITHUB_TOKEN,
    githubRepo: env.GITHUB_REPO || 'community-data-libraries/backend',
    previewPath: '/web/data-preview',
  };
}
```

`netlify/functions/_lib/tokens.mjs`:

```js
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

const encode = (value) => Buffer.from(value).toString('base64url');
const hmac = (body, secret) => createHmac('sha256', secret).update(body).digest();

export function signToken(payload, secret) {
  const body = encode(JSON.stringify(payload));
  return `${body}.${encode(hmac(body, secret))}`;
}

export function verifyToken(token, secret, { purpose, now = Date.now() }) {
  if (typeof token !== 'string') return { error: 'invalid' };
  const parts = token.split('.');
  if (parts.length !== 2) return { error: 'invalid' };
  const [body, sig] = parts;
  const expected = hmac(body, secret);
  const given = Buffer.from(sig, 'base64url');
  if (given.length !== expected.length || !timingSafeEqual(given, expected)) return { error: 'invalid' };
  let payload;
  try {
    payload = JSON.parse(Buffer.from(body, 'base64url').toString('utf8'));
  } catch {
    return { error: 'invalid' };
  }
  if (!payload || payload.purpose !== purpose || typeof payload.email !== 'string') return { error: 'invalid' };
  if (typeof payload.exp !== 'number' || payload.exp <= now) return { error: 'expired' };
  return { payload };
}

export const newNonce = () => randomBytes(16).toString('base64url');
```

`netlify/functions/_lib/allowlist.mjs`:

```js
export const normalizeEmail = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

export function isAllowed(email, rawList = '') {
  const target = normalizeEmail(email);
  if (!target) return false;
  return rawList.split(',').map(normalizeEmail).includes(target);
}

export const isValidEmail = (email) => email.length <= 254 && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
```

`netlify/functions/_lib/http.mjs`:

```js
export class HttpError extends Error {
  constructor(status, message, field) {
    super(message);
    this.status = status;
    this.field = field;
  }
}

export const json = (status, body, headers = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store', ...headers },
  });

export function toResponse(err) {
  if (err instanceof HttpError) {
    return json(err.status, { error: err.message, ...(err.field ? { field: err.field } : {}) });
  }
  console.error(err);
  return json(500, { error: 'Internal error' });
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS (all tests in the three files).

- [ ] **Step 6: Commit**

```bash
git add package.json package-lock.json .github/workflows/ci.yml netlify/functions/_lib
git commit -m "Add auth primitives: config, signed tokens, allowlist, HTTP helpers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Session cookie and `requireEditor` guard

**Files:**
- Create: `netlify/functions/_lib/session.mjs`
- Test: `netlify/functions/_lib/session.test.mjs`

**Interfaces:**
- Consumes: `signToken`, `verifyToken` (Task 3); `isAllowed` (Task 3); `HttpError` (Task 3); config object from `loadConfig`.
- Produces: `SESSION_COOKIE = 'cdl_session'`; `sessionCookie(email, secret, now?) -> string` (a `Set-Cookie` value); `clearSessionCookie() -> string`; `readCookie(request, name) -> string | null`; `requireEditor(request, config, now?) -> string` (email) — throws `HttpError` 401 (`'Not signed in'`), 403 (`'Access removed'`), or 403 (`'Bad origin'`); `checkOrigin(request, config)` throws 403 `'Bad origin'`.

- [ ] **Step 1: Write the failing tests**

`netlify/functions/_lib/session.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sessionCookie, clearSessionCookie, readCookie, requireEditor, SESSION_COOKIE } from './session.mjs';
import { signToken } from './tokens.mjs';

const config = { authSecret: 's'.repeat(32), editorEmails: 'alice@example.org', siteOrigin: 'https://cdl.example' };
const NOW = 1_800_000_000_000;

const cookieValue = (setCookie) => setCookie.split(';')[0];
const req = (method, { cookie, origin } = {}) =>
  new Request('https://cdl.example/.netlify/functions/x', {
    method,
    headers: { ...(cookie ? { cookie } : {}), ...(origin ? { origin } : {}) },
  });

test('session cookie has the required attributes', () => {
  const c = sessionCookie('alice@example.org', config.authSecret, NOW);
  assert.match(c, /^cdl_session=[^;]+; HttpOnly; Secure; SameSite=Lax; Path=\/; Max-Age=604800$/);
  assert.match(clearSessionCookie(), /^cdl_session=; .*Max-Age=0$/);
});

test('readCookie finds the cookie among others', () => {
  const r = req('GET', { cookie: `a=1; ${SESSION_COOKIE}=abc.def; b=2` });
  assert.equal(readCookie(r, SESSION_COOKIE), 'abc.def');
  assert.equal(readCookie(req('GET'), SESSION_COOKIE), null);
});

test('requireEditor accepts a valid session', () => {
  const cookie = cookieValue(sessionCookie('alice@example.org', config.authSecret, NOW));
  assert.equal(requireEditor(req('GET', { cookie }), config, NOW), 'alice@example.org');
  assert.equal(requireEditor(req('POST', { cookie, origin: 'https://cdl.example' }), config, NOW), 'alice@example.org');
});

test('requireEditor rejects missing, expired, and link-purpose tokens with 401', () => {
  const expired = cookieValue(sessionCookie('alice@example.org', config.authSecret, NOW - 8 * 864e5));
  const linkToken = signToken({ email: 'alice@example.org', exp: NOW + 1000, purpose: 'link' }, config.authSecret);
  for (const cookie of [undefined, expired, `${SESSION_COOKIE}=${linkToken}`]) {
    assert.throws(() => requireEditor(req('GET', { cookie }), config, NOW), (e) => e.status === 401);
  }
});

test('requireEditor rejects revoked emails with 403', () => {
  const cookie = cookieValue(sessionCookie('alice@example.org', config.authSecret, NOW));
  const revoked = { ...config, editorEmails: 'bob@example.org' };
  assert.throws(() => requireEditor(req('GET', { cookie }), revoked, NOW), (e) => e.status === 403 && /removed/.test(e.message));
});

test('requireEditor rejects POSTs from another origin with 403', () => {
  const cookie = cookieValue(sessionCookie('alice@example.org', config.authSecret, NOW));
  for (const origin of [undefined, 'https://evil.example']) {
    assert.throws(() => requireEditor(req('POST', { cookie, origin }), config, NOW), (e) => e.status === 403 && /origin/i.test(e.message));
  }
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../session.mjs'`.

- [ ] **Step 3: Implement**

`netlify/functions/_lib/session.mjs`:

```js
import { signToken, verifyToken } from './tokens.mjs';
import { isAllowed } from './allowlist.mjs';
import { HttpError } from './http.mjs';

export const SESSION_COOKIE = 'cdl_session';
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;
const ATTRS = 'HttpOnly; Secure; SameSite=Lax; Path=/';

export function sessionCookie(email, secret, now = Date.now()) {
  const token = signToken({ email, exp: now + SESSION_TTL_SECONDS * 1000, purpose: 'session' }, secret);
  return `${SESSION_COOKIE}=${token}; ${ATTRS}; Max-Age=${SESSION_TTL_SECONDS}`;
}

export const clearSessionCookie = () => `${SESSION_COOKIE}=; ${ATTRS}; Max-Age=0`;

export function readCookie(request, name) {
  for (const part of (request.headers.get('cookie') ?? '').split(';')) {
    const index = part.indexOf('=');
    if (index !== -1 && part.slice(0, index).trim() === name) return part.slice(index + 1).trim();
  }
  return null;
}

export function checkOrigin(request, config) {
  if (request.headers.get('origin') !== config.siteOrigin) throw new HttpError(403, 'Bad origin');
}

export function requireEditor(request, config, now = Date.now()) {
  const result = verifyToken(readCookie(request, SESSION_COOKIE), config.authSecret, { purpose: 'session', now });
  if (!result.payload) throw new HttpError(401, 'Not signed in');
  if (!isAllowed(result.payload.email, config.editorEmails)) throw new HttpError(403, 'Access removed');
  if (request.method !== 'GET' && request.method !== 'HEAD') checkOrigin(request, config);
  return result.payload.email;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add netlify/functions/_lib/session.mjs netlify/functions/_lib/session.test.mjs
git commit -m "Add session cookie and requireEditor guard

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Minimal-diff YAML editing

**Files:**
- Create: `netlify/functions/_lib/yamlEdit.mjs`
- Test: `netlify/functions/_lib/yamlEdit.test.mjs`
- Create: `netlify/functions/_lib/fixtures/sample-source.yml` (copy of `backend/data/sources/census-state-pop-change-2020-2025.yml` — it contains `notes`-style long strings, floats like `-181533.0`, and block lists)

**Interfaces:**
- Produces: `readYaml(text: string) -> object` (plain JS data); `replaceTopLevelKeys(text: string, updates: Record<string, unknown>) -> string` — replaces the source of each existing top-level key in `updates`, appends missing keys at the end, leaves all other bytes unchanged. Throws `Error('YAML root must be a mapping')` otherwise.

- [ ] **Step 1: Create the fixture**

```bash
mkdir -p netlify/functions/_lib/fixtures
cp ../backend/data/sources/census-state-pop-change-2020-2025.yml netlify/functions/_lib/fixtures/sample-source.yml
```

- [ ] **Step 2: Write the failing tests**

`netlify/functions/_lib/yamlEdit.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readYaml, replaceTopLevelKeys } from './yamlEdit.mjs';

const SRC = readFileSync(new URL('./fixtures/sample-source.yml', import.meta.url), 'utf8');

function changedLines(a, b) {
  const x = a.split('\n');
  const y = b.split('\n');
  const out = [];
  for (let i = 0; i < Math.max(x.length, y.length); i++) if (x[i] !== y[i]) out.push(i);
  return out;
}

test('no updates leaves the file byte-identical', () => {
  assert.equal(replaceTopLevelKeys(SRC, {}), SRC);
});

test('replacing title changes exactly one line', () => {
  const out = replaceTopLevelKeys(SRC, { title: 'New Title' });
  assert.equal(readYaml(out).title, 'New Title');
  assert.equal(changedLines(SRC, out).length, 1);
});

test('untouched values keep their exact source text (floats, long strings)', () => {
  const out = replaceTopLevelKeys(SRC, { title: 'New Title' });
  assert.ok(SRC.includes('min: -181533.0'));
  assert.ok(out.includes('min: -181533.0'));
  const before = readYaml(SRC);
  const after = readYaml(out);
  for (const key of Object.keys(before)) if (key !== 'title') assert.deepEqual(after[key], before[key], key);
});

test('lists are written in PyYAML style (no extra indent)', () => {
  const out = replaceTopLevelKeys(SRC, { description_tags: ['Agriculture', 'Population Data'] });
  assert.match(out, /\ndescription_tags:\n- Agriculture\n- Population Data\n/);
});

test('missing keys are appended at the end', () => {
  const out = replaceTopLevelKeys(SRC, { manual_tags: ['description_tags'] });
  assert.ok(out.startsWith(SRC.endsWith('\n') ? SRC : `${SRC}\n`));
  assert.ok(out.endsWith('manual_tags:\n- description_tags\n'));
});

test('YAML 1.1 lookalikes are quoted so PyYAML keeps them as strings', () => {
  for (const value of ['yes', 'No', 'on', 'off', '2025', 'null', '~', '0777', '1.0']) {
    const out = replaceTopLevelKeys('title: x\n', { title: value });
    assert.match(out, /^title: (".*"|'.*')\n$/, `${value} should be quoted, got ${out}`);
    assert.equal(readYaml(out).title, value);
  }
});

test('non-mapping roots are rejected', () => {
  assert.throws(() => replaceTopLevelKeys('- a\n- b\n', { title: 'x' }), /mapping/);
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../yamlEdit.mjs'`.

- [ ] **Step 4: Implement**

`netlify/functions/_lib/yamlEdit.mjs`:

```js
import { isMap, parseDocument, stringify } from 'yaml';

// Match PyYAML's output (block lists flush with their key) and quote YAML 1.1
// lookalikes such as "yes" or "2025" so the Python pipeline reads strings back.
const BLOCK_OPTIONS = { indentSeq: false, lineWidth: 80, minContentWidth: 0, version: '1.1' };

export const readYaml = (text) => parseDocument(text, { version: '1.1' }).toJS();

/**
 * Replace the source text of each top-level key in `updates`, append keys
 * that are not present, and leave every other byte untouched. Re-serializing
 * the whole document would re-wrap long strings and turn `-181533.0` into an int.
 */
export function replaceTopLevelKeys(text, updates) {
  const doc = parseDocument(text, { version: '1.1' });
  if (!isMap(doc.contents)) throw new Error('YAML root must be a mapping');
  const pairs = doc.contents.items;
  const edits = [];
  let appended = '';

  for (const [key, value] of Object.entries(updates)) {
    const block = stringify({ [key]: value }, BLOCK_OPTIONS);
    const index = pairs.findIndex((pair) => pair.key?.value === key);
    if (index === -1) {
      appended += block;
      continue;
    }
    const start = pairs[index].key.range[0];
    const end = index + 1 < pairs.length ? pairs[index + 1].key.range[0] : text.length;
    edits.push([start, end, block]);
  }

  let out = text;
  for (const [start, end, block] of edits.sort((a, b) => b[0] - a[0])) {
    out = out.slice(0, start) + block + out.slice(end);
  }
  if (appended) out = (out === '' || out.endsWith('\n') ? out : `${out}\n`) + appended;
  return out;
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 6: Cross-check with PyYAML**

```bash
node -e "import('./netlify/functions/_lib/yamlEdit.mjs').then(m=>process.stdout.write(m.replaceTopLevelKeys('title: x\nwhen: \"2025\"\n',{title:'yes',when:'on'})))" > /tmp/cdl-yaml-check.yml
python3 -c "import yaml; d=yaml.safe_load(open('/tmp/cdl-yaml-check.yml')); assert d=={'title':'yes','when':'on'}, d; print('PyYAML OK')"
```

Expected: `PyYAML OK`. (Use any Python with PyYAML, e.g. `../backend/.venv/bin/python`.)

- [ ] **Step 7: Commit**

```bash
git add netlify/functions/_lib/yamlEdit.mjs netlify/functions/_lib/yamlEdit.test.mjs netlify/functions/_lib/fixtures
git commit -m "Add minimal-diff YAML block replacement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Editable fields — validation, current values, and updates

**Files:**
- Create: `netlify/functions/_lib/editableFields.mjs`
- Test: `netlify/functions/_lib/editableFields.test.mjs`

**Interfaces:**
- Consumes: `HttpError` (Task 3).
- Produces:
  - `TEXT_FIELDS: string[]` — `['title','notes','who','how','where','why','when']`.
  - `pickEditable(data: object, vocabulary) -> object` — current values shaped for the form: text fields (`null` → `''`), `sensitive`/`requires_account` booleans (default `false`), `student_suitability` (default `'unreviewed'`), `provider: {name, agency, url}` (`''` defaults), `download: {url}`, `description_tags: string[]`, and one object per vocabulary statistical group with every vocabulary key (`false` default).
  - `validateChanges(changes: unknown, vocabulary) -> object` — normalized changes; throws `HttpError(400, message, field)`.
  - `toTopLevelUpdates(current: object, normalized: object, vocabulary) -> object` — top-level YAML updates with no-ops removed and `manual_tags` added when tag fields change.
  - `vocabulary` shape everywhere: `{ description_tags: string[], statistical_tags: Record<string, string[]> }`.

- [ ] **Step 1: Write the failing tests**

`netlify/functions/_lib/editableFields.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pickEditable, validateChanges, toTopLevelUpdates } from './editableFields.mjs';

const vocab = {
  description_tags: ['Agriculture', 'Population Data', 'Transportation'],
  statistical_tags: { anova_tags: ['good-for-one-way-anova', 'good-for-two-way-anova'] },
};

const current = {
  id: 'ds',
  title: 'Old',
  notes: null,
  provider: { name: 'USDA', agency: 'ERS', url: 'https://usda.gov' },
  download: { url: 'https://usda.gov/a.csv', format: 'csv' },
  description_tags: ['Agriculture'],
  student_suitability: 'unreviewed',
};

const fieldOf = (fn) => {
  try { fn(); } catch (e) { return [e.status, e.field]; }
  return 'no error';
};

test('pickEditable fills defaults, including tag groups missing from the file', () => {
  const picked = pickEditable(current, vocab);
  assert.equal(picked.notes, '');
  assert.equal(picked.sensitive, false);
  assert.deepEqual(picked.download, { url: 'https://usda.gov/a.csv' });
  assert.deepEqual(picked.anova_tags, { 'good-for-one-way-anova': false, 'good-for-two-way-anova': false });
});

test('validates and trims text fields', () => {
  assert.deepEqual(validateChanges({ title: '  New  ', notes: '' }, vocab), { title: 'New', notes: null });
  assert.deepEqual(fieldOf(() => validateChanges({ title: '   ' }, vocab)), [400, 'title']);
  assert.deepEqual(fieldOf(() => validateChanges({ who: 'x'.repeat(501) }, vocab)), [400, 'who']);
  assert.deepEqual(fieldOf(() => validateChanges({ title: 5 }, vocab)), [400, 'title']);
});

test('rejects unknown and read-only fields', () => {
  for (const field of ['id', 'version', 'variable_names', 'manual_tags', 'surprise']) {
    assert.deepEqual(fieldOf(() => validateChanges({ [field]: 'x' }, vocab)), [400, field]);
  }
  assert.deepEqual(fieldOf(() => validateChanges({}, vocab)), [400, undefined]);
  assert.deepEqual(fieldOf(() => validateChanges(['title'], vocab)), [400, undefined]);
});

test('validates URLs, booleans, and suitability', () => {
  assert.deepEqual(fieldOf(() => validateChanges({ download: { url: 'http://x.org' } }, vocab)), [400, 'download.url']);
  assert.deepEqual(fieldOf(() => validateChanges({ download: { url: 'https://x.org', format: 'zip' } }, vocab)), [400, 'download.format']);
  assert.deepEqual(fieldOf(() => validateChanges({ provider: { url: 'javascript:alert(1)' } }, vocab)), [400, 'provider.url']);
  assert.deepEqual(validateChanges({ provider: { url: '' } }, vocab), { provider: { url: null } });
  assert.deepEqual(fieldOf(() => validateChanges({ sensitive: 'true' }, vocab)), [400, 'sensitive']);
  assert.deepEqual(fieldOf(() => validateChanges({ student_suitability: 'maybe' }, vocab)), [400, 'student_suitability']);
});

test('validates tags against the vocabulary', () => {
  assert.deepEqual(validateChanges({ description_tags: ['Transportation', 'Agriculture'] }, vocab), {
    description_tags: ['Agriculture', 'Transportation'],
  });
  assert.deepEqual(fieldOf(() => validateChanges({ description_tags: ['Made Up'] }, vocab)), [400, 'description_tags']);
  assert.deepEqual(fieldOf(() => validateChanges({ description_tags: ['Agriculture', 'Agriculture'] }, vocab)), [400, 'description_tags']);
  assert.deepEqual(fieldOf(() => validateChanges({ anova_tags: { 'good-for-one-way-anova': true } }, vocab)), [400, 'anova_tags']);
  assert.deepEqual(fieldOf(() => validateChanges({ anova_tags: { 'good-for-one-way-anova': 'yes', 'good-for-two-way-anova': false } }, vocab)), [400, 'anova_tags']);
});

test('toTopLevelUpdates merges nested objects and drops no-ops', () => {
  const normalized = validateChanges({ title: 'Old', provider: { agency: 'NASS' }, download: { url: 'https://usda.gov/b.csv' } }, vocab);
  assert.deepEqual(toTopLevelUpdates(current, normalized, vocab), {
    provider: { name: 'USDA', agency: 'NASS', url: 'https://usda.gov' },
    download: { url: 'https://usda.gov/b.csv', format: 'csv' },
  });
});

test('tag changes add the field to manual_tags; unchanged tags do not', () => {
  const group = { 'good-for-one-way-anova': true, 'good-for-two-way-anova': false };
  const updates = toTopLevelUpdates(current, validateChanges({ anova_tags: group, description_tags: ['Agriculture'] }, vocab), vocab);
  assert.deepEqual(updates, { anova_tags: group, manual_tags: ['anova_tags'] });
});

test('group key order does not count as a change', () => {
  const withGroup = { ...current, anova_tags: { 'good-for-two-way-anova': false, 'good-for-one-way-anova': true } };
  const normalized = validateChanges({ anova_tags: { 'good-for-one-way-anova': true, 'good-for-two-way-anova': false } }, vocab);
  assert.deepEqual(toTopLevelUpdates(withGroup, normalized, vocab), {});
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../editableFields.mjs'`.

- [ ] **Step 3: Implement**

`netlify/functions/_lib/editableFields.mjs`:

```js
import { HttpError } from './http.mjs';

export const TEXT_FIELDS = ['title', 'notes', 'who', 'how', 'where', 'why', 'when'];
const SUITABILITY = ['suitable', 'not_suitable', 'unreviewed'];

const fail = (field, message) => {
  throw new HttpError(400, `${field} ${message}`, field);
};
const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);

function text(value, field, { max, required = false }) {
  if (!required && (value === null || value === '')) return null;
  if (typeof value !== 'string') fail(field, 'must be text');
  const trimmed = value.trim();
  if (trimmed === '') return required ? fail(field, 'is required') : null;
  if (trimmed.length > max) fail(field, `must be at most ${max} characters`);
  return trimmed;
}

function httpsUrl(value, field, { required }) {
  const v = text(value, field, { max: 2000, required });
  if (v === null) return null;
  let url;
  try {
    url = new URL(v);
  } catch {
    fail(field, 'must be a valid URL');
  }
  if (url.protocol !== 'https:') fail(field, 'must start with https://');
  return v;
}

const bool = (value, field) => (typeof value === 'boolean' ? value : fail(field, 'must be true or false'));

function onlyKeys(value, field, allowed) {
  if (!isPlainObject(value)) fail(field, 'must be an object');
  for (const key of Object.keys(value)) if (!allowed.includes(key)) fail(`${field}.${key}`, 'is not editable');
}

const SIMPLE = {
  title: (v, f) => text(v, f, { max: 200, required: true }),
  notes: (v, f) => text(v, f, { max: 5000 }),
  who: (v, f) => text(v, f, { max: 500 }),
  how: (v, f) => text(v, f, { max: 500 }),
  where: (v, f) => text(v, f, { max: 500 }),
  why: (v, f) => text(v, f, { max: 500 }),
  when: (v, f) => text(v, f, { max: 500 }),
  sensitive: bool,
  requires_account: bool,
  student_suitability: (v, f) => (SUITABILITY.includes(v) ? v : fail(f, `must be one of ${SUITABILITY.join(', ')}`)),
};

function validateProvider(value) {
  onlyKeys(value, 'provider', ['name', 'agency', 'url']);
  const out = {};
  if ('name' in value) out.name = text(value.name, 'provider.name', { max: 200 });
  if ('agency' in value) out.agency = text(value.agency, 'provider.agency', { max: 200 });
  if ('url' in value) out.url = httpsUrl(value.url, 'provider.url', { required: false });
  return out;
}

function validateTopicTags(value, allowed) {
  const field = 'description_tags';
  if (!Array.isArray(value)) fail(field, 'must be a list');
  for (const tag of value) if (!allowed.includes(tag)) fail(field, `has unknown tag ${JSON.stringify(tag)}`);
  if (new Set(value).size !== value.length) fail(field, 'has duplicate tags');
  return [...value].sort();
}

function validateGroup(value, keys, field) {
  if (!isPlainObject(value)) fail(field, 'must be an object');
  const given = Object.keys(value);
  if (given.length !== keys.length || !keys.every((k) => k in value)) fail(field, 'must include exactly the vocabulary keys');
  const out = {};
  for (const key of keys) {
    if (typeof value[key] !== 'boolean') fail(field, `${key} must be true or false`);
    out[key] = value[key];
  }
  return out;
}

export function validateChanges(changes, vocabulary) {
  if (!isPlainObject(changes)) throw new HttpError(400, 'changes must be an object');
  const out = {};
  for (const [field, value] of Object.entries(changes)) {
    if (field in SIMPLE) out[field] = SIMPLE[field](value, field);
    else if (field === 'provider') out.provider = validateProvider(value);
    else if (field === 'download') {
      onlyKeys(value, 'download', ['url']);
      out.download = { url: httpsUrl(value.url, 'download.url', { required: true }) };
    } else if (field === 'description_tags') out.description_tags = validateTopicTags(value, vocabulary.description_tags);
    else if (field in vocabulary.statistical_tags) out[field] = validateGroup(value, vocabulary.statistical_tags[field], field);
    else fail(field, 'is not editable');
  }
  if (Object.keys(out).length === 0) throw new HttpError(400, 'No changes submitted');
  return out;
}

export function pickEditable(data, vocabulary) {
  const picked = {};
  for (const field of TEXT_FIELDS) picked[field] = typeof data[field] === 'string' ? data[field] : data[field] == null ? '' : String(data[field]);
  picked.sensitive = data.sensitive === true;
  picked.requires_account = data.requires_account === true;
  picked.student_suitability = SUITABILITY.includes(data.student_suitability) ? data.student_suitability : 'unreviewed';
  const provider = isPlainObject(data.provider) ? data.provider : {};
  picked.provider = { name: provider.name ?? '', agency: provider.agency ?? '', url: provider.url ?? '' };
  picked.download = { url: isPlainObject(data.download) ? data.download.url ?? '' : '' };
  picked.description_tags = Array.isArray(data.description_tags) ? data.description_tags : [];
  for (const [group, keys] of Object.entries(vocabulary.statistical_tags)) {
    const existing = isPlainObject(data[group]) ? data[group] : {};
    picked[group] = Object.fromEntries(keys.map((k) => [k, existing[k] === true]));
  }
  return picked;
}

// Key-order-insensitive comparison, so reordered tag groups are not a change.
const canonical = (v) =>
  Array.isArray(v)
    ? `[${v.map(canonical).join(',')}]`
    : isPlainObject(v)
      ? `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`
      : JSON.stringify(v ?? null);

export function toTopLevelUpdates(current, normalized, vocabulary) {
  const updates = {};
  for (const [field, value] of Object.entries(normalized)) {
    const merged =
      field === 'provider' || field === 'download'
        ? { ...(isPlainObject(current[field]) ? current[field] : {}), ...value }
        : value;
    if (canonical(merged) !== canonical(current[field])) updates[field] = merged;
  }
  const tagFields = Object.keys(updates).filter((f) => f === 'description_tags' || f in vocabulary.statistical_tags);
  if (tagFields.length) {
    const existing = Array.isArray(current.manual_tags) ? current.manual_tags : [];
    const merged = [...new Set([...existing, ...tagFields])].sort();
    if (canonical(merged) !== canonical(existing)) updates.manual_tags = merged;
  }
  return updates;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add netlify/functions/_lib/editableFields.mjs netlify/functions/_lib/editableFields.test.mjs
git commit -m "Add editable-field validation and update computation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Auth functions (request link, verify, me, logout)

**Files:**
- Create: `netlify/functions/_lib/authStore.mjs`, `netlify/functions/_lib/mailer.mjs`
- Create: `netlify/functions/auth-request-link.mjs`, `auth-verify.mjs`, `auth-me.mjs`, `auth-logout.mjs`
- Create: `netlify/tests/memoryStore.mjs` (in-memory Blobs store for tests)
- Test: `netlify/functions/_lib/authStore.test.mjs`, `netlify/tests/auth.test.mjs`

**Interfaces:**
- Consumes: Tasks 3–4 (`loadConfig`, `signToken`, `verifyToken`, `newNonce`, `normalizeEmail`, `isAllowed`, `isValidEmail`, `json`, `toResponse`, `sessionCookie`, `clearSessionCookie`, `requireEditor`, `checkOrigin`).
- Produces:
  - `consumeNonce(store, nonce: string, exp: number) -> Promise<boolean>`; `hitRateLimit(store, key: string, { limit, windowMs }, now?) -> Promise<boolean>` (true = over limit); `getAuthStore() -> Promise<store>`. `store` is any object with `get(key, { type: 'json' })` and `setJSON(key, value)` (the `@netlify/blobs` store API).
  - `sendMagicLink({ apiKey, from, to, link, fetchImpl? }) -> Promise<void>`.
  - Each function file exports `createHandler(deps)` for tests and a default Netlify handler. `deps`: `{ config, store?, sendMail?, now? }` where `now: () => number`.

- [ ] **Step 1: Write the failing tests**

`netlify/tests/memoryStore.mjs`:

```js
// Minimal stand-in for a @netlify/blobs store: get(key, { type: 'json' }) and setJSON(key, value).
export function memoryStore() {
  const data = new Map();
  return {
    data,
    async get(key) { return data.has(key) ? structuredClone(data.get(key)) : null; },
    async setJSON(key, value) { data.set(key, structuredClone(value)); },
  };
}
```

`netlify/functions/_lib/authStore.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { consumeNonce, hitRateLimit } from './authStore.mjs';
import { memoryStore } from '../../tests/memoryStore.mjs';

test('a nonce can be consumed once', async () => {
  const store = memoryStore();
  assert.equal(await consumeNonce(store, 'n1', 123), true);
  assert.equal(await consumeNonce(store, 'n1', 123), false);
  assert.equal(await consumeNonce(store, 'n2', 123), true);
});

test('rate limit trips after the limit and resets after the window', async () => {
  const store = memoryStore();
  const opts = { limit: 3, windowMs: 1000 };
  const results = [];
  for (let i = 0; i < 4; i++) results.push(await hitRateLimit(store, 'k', opts, 10_000));
  assert.deepEqual(results, [false, false, false, true]);
  assert.equal(await hitRateLimit(store, 'k', opts, 11_001), false);
});
```

`netlify/tests/auth.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memoryStore } from './memoryStore.mjs';
import { signToken } from '../functions/_lib/tokens.mjs';
import { sessionCookie } from '../functions/_lib/session.mjs';
import { createHandler as requestLink } from '../functions/auth-request-link.mjs';
import { createHandler as verify } from '../functions/auth-verify.mjs';
import { createHandler as me } from '../functions/auth-me.mjs';
import { createHandler as logout } from '../functions/auth-logout.mjs';

const ORIGIN = 'https://cdl.example';
const config = { authSecret: 's'.repeat(32), editorEmails: 'alice@example.org', siteOrigin: ORIGIN, previewPath: '/web/data-preview' };
const NOW = 1_800_000_000_000;
const now = () => NOW;
const URL_BASE = `${ORIGIN}/.netlify/functions`;

const post = (name, body, headers = {}) =>
  new Request(`${URL_BASE}/${name}`, { method: 'POST', headers: { origin: ORIGIN, 'content-type': 'application/json', ...headers }, body: JSON.stringify(body) });

function setup() {
  const sent = [];
  const store = memoryStore();
  const deps = { config, store, now, sendMail: async (m) => sent.push(m) };
  return { sent, store, deps };
}

const linkToken = (email = 'alice@example.org', exp = NOW + 60_000, nonce = 'nonce-1') =>
  signToken({ email, exp, nonce, purpose: 'link' }, config.authSecret);

test('request-link: identical responses for allowed, unknown, and rate-limited emails', async () => {
  const { sent, deps } = setup();
  const handler = requestLink(deps);
  const bodies = [];
  for (const email of ['  ALICE@example.org ', 'mallory@example.org', 'alice@example.org', 'alice@example.org', 'alice@example.org']) {
    const res = await handler(post('auth-request-link', { email }), { ip: '1.2.3.4' });
    assert.equal(res.status, 200);
    bodies.push(await res.text());
  }
  assert.equal(new Set(bodies).size, 1);
  assert.equal(sent.length, 3); // 3 allowed requests within the per-email limit; the 4th alice request is limited
  assert.equal(sent[0].to, 'alice@example.org');
  assert.match(sent[0].link, /^https:\/\/cdl\.example\/\.netlify\/functions\/auth-verify\?token=/);
});

test('request-link: rejects bad origin, bad JSON, and malformed emails', async () => {
  const handler = requestLink(setup().deps);
  assert.equal((await handler(post('auth-request-link', { email: 'a@b.org' }, { origin: 'https://evil.example' }), {})).status, 403);
  assert.equal((await handler(new Request(`${URL_BASE}/auth-request-link`, { method: 'POST', headers: { origin: ORIGIN }, body: '{' }), {})).status, 400);
  assert.equal((await handler(post('auth-request-link', { email: 'nope' }), {})).status, 400);
  assert.equal((await handler(new Request(`${URL_BASE}/auth-request-link`), {})).status, 405);
});

test('request-link: email failure still returns the same 200', async () => {
  const { deps } = setup();
  const handler = requestLink({ ...deps, sendMail: async () => { throw new Error('resend down'); } });
  assert.equal((await handler(post('auth-request-link', { email: 'alice@example.org' }), {})).status, 200);
});

test('verify GET shows a confirm page and does NOT consume the link (email scanners)', async () => {
  const { store, deps } = setup();
  const handler = verify(deps);
  const token = linkToken();
  for (let i = 0; i < 3; i++) {
    const res = await handler(new Request(`${URL_BASE}/auth-verify?token=${encodeURIComponent(token)}`), {});
    assert.equal(res.status, 200);
    assert.match(res.headers.get('content-type'), /text\/html/);
    const html = await res.text();
    assert.match(html, /<form method="post"/);
    assert.ok(html.includes(token));
  }
  assert.equal(store.data.size, 0);
});

test('verify POST signs in once, then reports the link as used', async () => {
  const handler = verify(setup().deps);
  const form = (token) => new Request(`${URL_BASE}/auth-verify`, { method: 'POST', body: new URLSearchParams({ token }) });
  const token = linkToken();
  const first = await handler(form(token), {});
  assert.equal(first.status, 303);
  assert.equal(first.headers.get('location'), `${ORIGIN}/web/data-preview?auth=ok`);
  assert.match(first.headers.get('set-cookie'), /^cdl_session=/);
  const second = await handler(form(token), {});
  assert.equal(second.headers.get('location'), `${ORIGIN}/web/data-preview?auth=expired`);
  assert.equal(second.headers.get('set-cookie'), null);
});

test('verify redirects expired, invalid, and revoked links without a cookie', async () => {
  const handler = verify(setup().deps);
  const form = (token) => new Request(`${URL_BASE}/auth-verify`, { method: 'POST', body: new URLSearchParams({ token }) });
  const cases = [
    [linkToken('alice@example.org', NOW - 1, 'n-exp'), 'expired'],
    ['garbage', 'invalid'],
    [linkToken('bob@example.org', NOW + 60_000, 'n-bob'), 'invalid'],
  ];
  for (const [token, reason] of cases) {
    const res = await handler(form(token), {});
    assert.equal(res.headers.get('location'), `${ORIGIN}/web/data-preview?auth=${reason}`);
    assert.equal(res.headers.get('set-cookie'), null);
  }
  const get = await handler(new Request(`${URL_BASE}/auth-verify?token=garbage`), {});
  assert.equal(get.headers.get('location'), `${ORIGIN}/web/data-preview?auth=invalid`);
});

test('me returns the email for a session and 401 without one', async () => {
  const handler = me({ config, now });
  const cookie = sessionCookie('alice@example.org', config.authSecret, NOW).split(';')[0];
  const ok = await handler(new Request(`${URL_BASE}/auth-me`, { headers: { cookie } }), {});
  assert.deepEqual(await ok.json(), { email: 'alice@example.org' });
  assert.equal((await handler(new Request(`${URL_BASE}/auth-me`), {})).status, 401);
});

test('logout clears the cookie and requires same origin', async () => {
  const handler = logout({ config });
  const res = await handler(new Request(`${URL_BASE}/auth-logout`, { method: 'POST', headers: { origin: ORIGIN } }), {});
  assert.equal(res.status, 200);
  assert.match(res.headers.get('set-cookie'), /Max-Age=0/);
  assert.equal((await handler(new Request(`${URL_BASE}/auth-logout`, { method: 'POST', headers: { origin: 'https://evil.example' } }), {})).status, 403);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — missing modules.

- [ ] **Step 3: Implement store and mailer**

`netlify/functions/_lib/authStore.mjs`:

```js
export async function getAuthStore() {
  const { getStore } = await import('@netlify/blobs');
  return getStore({ name: 'cdl-auth', consistency: 'strong' });
}

export async function consumeNonce(store, nonce, exp) {
  const key = `nonce/${nonce}`;
  if (await store.get(key, { type: 'json' })) return false;
  await store.setJSON(key, { exp });
  return true;
}

export async function hitRateLimit(store, key, { limit, windowMs }, now = Date.now()) {
  const storeKey = `rate/${key}`;
  const current = await store.get(storeKey, { type: 'json' });
  const entry = current && now - current.start < windowMs ? current : { start: now, count: 0 };
  entry.count += 1;
  await store.setJSON(storeKey, entry);
  return entry.count > limit;
}
```

`netlify/functions/_lib/mailer.mjs`:

```js
export async function sendMagicLink({ apiKey, from, to, link, fetchImpl = fetch }) {
  const res = await fetchImpl('https://api.resend.com/emails', {
    method: 'POST',
    headers: { authorization: `Bearer ${apiKey}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      from,
      to: [to],
      subject: 'Your Community Data Libraries editor sign-in link',
      text: `Use this link to sign in and edit dataset metadata:\n\n${link}\n\nIt expires in 15 minutes and works once. If you did not request it, you can ignore this email.`,
    }),
  });
  if (!res.ok) throw new Error(`Resend responded with HTTP ${res.status}`);
}
```

- [ ] **Step 4: Implement the four functions**

`netlify/functions/auth-request-link.mjs`:

```js
import { loadConfig } from './_lib/config.mjs';
import { json } from './_lib/http.mjs';
import { isAllowed, isValidEmail, normalizeEmail } from './_lib/allowlist.mjs';
import { newNonce, signToken } from './_lib/tokens.mjs';
import { getAuthStore, hitRateLimit } from './_lib/authStore.mjs';
import { sendMagicLink } from './_lib/mailer.mjs';

const MINUTE = 60_000;
const LINK_TTL_MS = 15 * MINUTE;
const RESPONSE = { message: 'If that email is approved, a sign-in link is on its way.' };

export function createHandler({ config, store, sendMail, now = Date.now }) {
  return async (request, context) => {
    if (request.method !== 'POST') return json(405, { error: 'Method not allowed' });
    if (request.headers.get('origin') !== config.siteOrigin) return json(403, { error: 'Bad origin' });
    let body;
    try {
      body = await request.json();
    } catch {
      return json(400, { error: 'Invalid JSON' });
    }
    const email = normalizeEmail(body?.email);
    if (!isValidEmail(email)) return json(400, { error: 'Enter a valid email address', field: 'email' });

    const ipLimited = await hitRateLimit(store, `ip/${context?.ip ?? 'unknown'}`, { limit: 20, windowMs: 60 * MINUTE }, now());
    const emailLimited = await hitRateLimit(store, `email/${email}`, { limit: 3, windowMs: 15 * MINUTE }, now());
    if (ipLimited || emailLimited) {
      console.warn('auth-request-link: rate limited');
      return json(200, RESPONSE);
    }
    if (!isAllowed(email, config.editorEmails)) return json(200, RESPONSE);

    const token = signToken({ email, exp: now() + LINK_TTL_MS, nonce: newNonce(), purpose: 'link' }, config.authSecret);
    const link = `${config.siteOrigin}/.netlify/functions/auth-verify?token=${encodeURIComponent(token)}`;
    try {
      await sendMail({ to: email, link });
    } catch (err) {
      console.error('auth-request-link: email failed:', err.message);
    }
    return json(200, RESPONSE);
  };
}

export default async (request, context) => {
  const config = loadConfig();
  const sendMail = ({ to, link }) => sendMagicLink({ apiKey: config.resendApiKey, from: config.mailFrom, to, link });
  return createHandler({ config, store: await getAuthStore(), sendMail })(request, context);
};
```

`netlify/functions/auth-verify.mjs`:

```js
import { loadConfig } from './_lib/config.mjs';
import { isAllowed } from './_lib/allowlist.mjs';
import { verifyToken } from './_lib/tokens.mjs';
import { sessionCookie } from './_lib/session.mjs';
import { consumeNonce, getAuthStore } from './_lib/authStore.mjs';

const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

// GET never consumes the link: email security scanners pre-fetch links.
// Only the POST from this page's button signs the person in.
const confirmPage = (token) => `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in</title>
<style>body{font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0}button{font-size:1rem;padding:.75rem 1.5rem;cursor:pointer}</style>
</head><body><form method="post" action="/.netlify/functions/auth-verify">
<input type="hidden" name="token" value="${escapeHtml(token)}">
<p>Sign in to edit Community Data Libraries metadata.</p>
<button type="submit">Sign in</button>
</form></body></html>`;

export function createHandler({ config, store, now = Date.now }) {
  const redirect = (reason, cookie) =>
    new Response(null, {
      status: 303,
      headers: { location: `${config.siteOrigin}${config.previewPath}?auth=${reason}`, ...(cookie ? { 'set-cookie': cookie } : {}) },
    });

  return async (request) => {
    if (request.method === 'GET') {
      const token = new URL(request.url).searchParams.get('token');
      const result = verifyToken(token, config.authSecret, { purpose: 'link', now: now() });
      if (!result.payload) return redirect(result.error);
      return new Response(confirmPage(token), {
        status: 200,
        headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', 'referrer-policy': 'no-referrer' },
      });
    }
    if (request.method !== 'POST') return new Response('Method not allowed', { status: 405 });

    const token = (await request.formData().catch(() => null))?.get('token');
    const result = verifyToken(typeof token === 'string' ? token : null, config.authSecret, { purpose: 'link', now: now() });
    if (!result.payload) return redirect(result.error);
    const { email, nonce, exp } = result.payload;
    if (typeof nonce !== 'string' || !(await consumeNonce(store, nonce, exp))) return redirect('expired');
    if (!isAllowed(email, config.editorEmails)) return redirect('invalid');
    console.log(`auth-verify: signed in ${email}`);
    return redirect('ok', sessionCookie(email, config.authSecret, now()));
  };
}

export default async (request, context) =>
  createHandler({ config: loadConfig(), store: await getAuthStore() })(request, context);
```

Note: the revoked-email case consumes the nonce before the allowlist check, so the test's `n-bob` link is burned; that is intended.

`netlify/functions/auth-me.mjs`:

```js
import { loadConfig } from './_lib/config.mjs';
import { json, toResponse } from './_lib/http.mjs';
import { requireEditor } from './_lib/session.mjs';

export function createHandler({ config, now = Date.now }) {
  return async (request) => {
    try {
      return json(200, { email: requireEditor(request, config, now()) });
    } catch (err) {
      return toResponse(err);
    }
  };
}

export default async (request, context) => createHandler({ config: loadConfig() })(request, context);
```

`netlify/functions/auth-logout.mjs`:

```js
import { loadConfig } from './_lib/config.mjs';
import { json, toResponse } from './_lib/http.mjs';
import { checkOrigin, clearSessionCookie } from './_lib/session.mjs';

export function createHandler({ config }) {
  return async (request) => {
    try {
      if (request.method !== 'POST') return json(405, { error: 'Method not allowed' });
      checkOrigin(request, config);
      return json(200, { ok: true }, { 'set-cookie': clearSessionCookie() });
    } catch (err) {
      return toResponse(err);
    }
  };
}

export default async (request, context) => createHandler({ config: loadConfig() })(request, context);
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add netlify/functions/_lib/authStore.mjs netlify/functions/_lib/authStore.test.mjs netlify/functions/_lib/mailer.mjs netlify/functions/auth-*.mjs netlify/tests/auth.test.mjs netlify/tests/memoryStore.mjs
git commit -m "Add magic-link auth functions with scanner-safe confirm page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `metadata-edit` function (read and open PR)

**Files:**
- Create: `netlify/functions/_lib/github.mjs`
- Create: `netlify/functions/metadata-edit.mjs`
- Test: `netlify/tests/metadata-edit.test.mjs`

**Interfaces:**
- Consumes: `loadConfig`, `json`, `toResponse`, `HttpError` (Task 3); `requireEditor` (Task 4); `readYaml`, `replaceTopLevelKeys` (Task 5); `pickEditable`, `validateChanges`, `toTopLevelUpdates` (Task 6); vocabulary file layout (Task 1).
- Produces:
  - `createGitHub({ token, repo, fetchImpl? })` → `{ getHeadSha(branch='main') -> Promise<string>, getFile(path, ref) -> Promise<{text, sha} | null>, createBranch(name, sha), deleteBranch(name), putFile(path, { text, sha, branch, message }), openPullRequest({ title, body, head, base='main' }) -> Promise<{ number, html_url }>, listOpenPullRequests(prefix) -> Promise<Array<{ number, url, title }>> }`.
  - HTTP API: `GET /.netlify/functions/metadata-edit?id=<id>` → `200 { id, fields, manualTags, vocabulary, openPullRequests }`; `POST {id, changes}` → `201 { prUrl, prNumber }`. Errors: 400/401/403/404/502 as `{ error, field? }`.

- [ ] **Step 1: Write the failing tests**

`netlify/tests/metadata-edit.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHandler } from '../functions/metadata-edit.mjs';
import { sessionCookie } from '../functions/_lib/session.mjs';
import { readYaml } from '../functions/_lib/yamlEdit.mjs';

const ORIGIN = 'https://cdl.example';
const config = { authSecret: 's'.repeat(32), editorEmails: 'alice@example.org', siteOrigin: ORIGIN };
const NOW = Date.UTC(2026, 9, 14, 15, 30, 0);
const COOKIE = sessionCookie('alice@example.org', config.authSecret, NOW).split(';')[0];
const VOCAB = `description_tags:
- Agriculture
- Population Data
statistical_tags:
  anova_tags:
  - good-for-one-way-anova
  - good-for-two-way-anova
`;
const DATASET = `id: usda-milk
title: Milk Production
notes: null
provider:
  name: USDA
  url: https://usda.gov
description_tags:
- Agriculture
when: '2025'
`;

function fakeGitHub(files = { 'data/sources/usda-milk.yml': DATASET, 'data/tag_vocabulary.yml': VOCAB }, { failPut = false } = {}) {
  const calls = [];
  return {
    calls,
    files,
    async getHeadSha() { calls.push(['getHeadSha']); return 'head123'; },
    async getFile(path, ref) { calls.push(['getFile', path, ref]); return path in files ? { text: files[path], sha: `sha-${path}` } : null; },
    async createBranch(name, sha) { calls.push(['createBranch', name, sha]); },
    async deleteBranch(name) { calls.push(['deleteBranch', name]); },
    async putFile(path, opts) { calls.push(['putFile', path, opts]); if (failPut) throw new Error('409'); },
    async openPullRequest(opts) { calls.push(['openPullRequest', opts]); return { number: 7, html_url: 'https://github.com/x/pull/7' }; },
    async listOpenPullRequests(prefix) { calls.push(['list', prefix]); return [{ number: 3, url: 'u', title: 't' }]; },
  };
}

const get = (id, cookie = COOKIE) => new Request(`${ORIGIN}/.netlify/functions/metadata-edit?id=${id}`, { headers: cookie ? { cookie } : {} });
const post = (body, { cookie = COOKIE, origin = ORIGIN } = {}) =>
  new Request(`${ORIGIN}/.netlify/functions/metadata-edit`, {
    method: 'POST',
    headers: { cookie, origin, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });

test('GET returns editable fields, vocabulary, manual tags, and open PRs', async () => {
  const github = fakeGitHub();
  const res = await createHandler({ config, github, now: () => NOW })(get('usda-milk'));
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.fields.title, 'Milk Production');
  assert.deepEqual(body.fields.anova_tags, { 'good-for-one-way-anova': false, 'good-for-two-way-anova': false });
  assert.deepEqual(body.vocabulary.description_tags, ['Agriculture', 'Population Data']);
  assert.deepEqual(body.manualTags, []);
  assert.deepEqual(body.openPullRequests, [{ number: 3, url: 'u', title: 't' }]);
  assert.deepEqual(github.calls.find((c) => c[0] === 'list'), ['list', 'metadata/usda-milk-']);
});

test('requires a session and a safe id', async () => {
  const handler = createHandler({ config, github: fakeGitHub(), now: () => NOW });
  assert.equal((await handler(get('usda-milk', null))).status, 401);
  for (const id of ['..%2Fsecrets', 'Bad_ID', '']) assert.equal((await handler(get(id))).status, 400, id);
  assert.equal((await handler(get('missing'))).status, 404);
});

test('POST opens a PR containing only the changed lines', async () => {
  const github = fakeGitHub();
  const res = await createHandler({ config, github, now: () => NOW })(
    post({ id: 'usda-milk', changes: { title: 'yes', anova_tags: { 'good-for-one-way-anova': true, 'good-for-two-way-anova': false } } }),
  );
  assert.equal(res.status, 201);
  assert.deepEqual(await res.json(), { prUrl: 'https://github.com/x/pull/7', prNumber: 7 });

  const branch = 'metadata/usda-milk-20261014T153000Z';
  assert.deepEqual(github.calls.find((c) => c[0] === 'createBranch'), ['createBranch', branch, 'head123']);
  const [, path, put] = github.calls.find((c) => c[0] === 'putFile');
  assert.equal(path, 'data/sources/usda-milk.yml');
  assert.equal(put.branch, branch);
  assert.equal(put.sha, 'sha-data/sources/usda-milk.yml');
  assert.match(put.message, /^Update metadata for usda-milk\n\nCo-Authored-By: alice@example.org <alice@example.org>$/);
  const written = readYaml(put.text);
  assert.equal(written.title, 'yes');
  assert.deepEqual(written.manual_tags, ['anova_tags']);
  assert.equal(written.when, '2025');
  assert.ok(put.text.startsWith('id: usda-milk\ntitle: "yes"\nnotes: null\n'));

  const [, pr] = github.calls.find((c) => c[0] === 'openPullRequest');
  assert.equal(pr.title, 'Update metadata: Milk Production (by alice@example.org)');
  assert.equal(pr.head, branch);
  assert.match(pr.body, /\| `title` \| Milk Production \| yes \|/);
});

test('POST applies only submitted fields onto the latest main content', async () => {
  // Simulates main having changed after the form loaded: notes is new on main.
  const github = fakeGitHub({ 'data/sources/usda-milk.yml': DATASET.replace('notes: null', 'notes: Newer note'), 'data/tag_vocabulary.yml': VOCAB });
  await createHandler({ config, github, now: () => NOW })(post({ id: 'usda-milk', changes: { title: 'New' } }));
  const [, , put] = github.calls.find((c) => c[0] === 'putFile');
  assert.equal(readYaml(put.text).notes, 'Newer note');
  assert.deepEqual(github.calls.filter((c) => c[0] === 'getFile').map((c) => c[2]), ['head123', 'head123']);
});

test('POST rejects no-op edits, invalid fields, bad origin', async () => {
  const handler = createHandler({ config, github: fakeGitHub(), now: () => NOW });
  assert.equal((await handler(post({ id: 'usda-milk', changes: { title: 'Milk Production' } }))).status, 400);
  const bad = await handler(post({ id: 'usda-milk', changes: { description_tags: ['Nope'] } }));
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).field, 'description_tags');
  assert.equal((await handler(post({ id: 'usda-milk', changes: { title: 'x' } }, { origin: 'https://evil.example' }))).status, 403);
});

test('POST cleans up the branch when GitHub fails', async () => {
  const github = fakeGitHub(undefined, { failPut: true });
  const res = await createHandler({ config, github, now: () => NOW })(post({ id: 'usda-milk', changes: { title: 'New' } }));
  assert.equal(res.status, 502);
  assert.match((await res.json()).error, /nothing was saved/);
  assert.deepEqual(github.calls.at(-1), ['deleteBranch', 'metadata/usda-milk-20261014T153000Z']);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../metadata-edit.mjs'`.

- [ ] **Step 3: Implement the GitHub wrapper**

`netlify/functions/_lib/github.mjs`:

```js
export function createGitHub({ token, repo, fetchImpl = fetch }) {
  async function api(method, path, body) {
    const res = await fetchImpl(`https://api.github.com/repos/${repo}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${token}`,
        accept: 'application/vnd.github+json',
        'x-github-api-version': '2022-11-28',
        ...(body ? { 'content-type': 'application/json' } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 404 && method === 'GET') return null;
    if (!res.ok) throw new Error(`GitHub ${method} ${path} failed: HTTP ${res.status}`);
    return res.status === 204 ? {} : res.json();
  }

  return {
    async getHeadSha(branch = 'main') {
      const ref = await api('GET', `/git/ref/heads/${branch}`);
      if (!ref) throw new Error(`Branch ${branch} not found`);
      return ref.object.sha;
    },
    async getFile(path, ref) {
      const file = await api('GET', `/contents/${path}?ref=${ref}`);
      return file && { text: Buffer.from(file.content, 'base64').toString('utf8'), sha: file.sha };
    },
    createBranch: (name, sha) => api('POST', '/git/refs', { ref: `refs/heads/${name}`, sha }),
    deleteBranch: (name) => api('DELETE', `/git/refs/heads/${name}`),
    putFile: (path, { text, sha, branch, message }) =>
      api('PUT', `/contents/${path}`, { message, content: Buffer.from(text, 'utf8').toString('base64'), sha, branch }),
    openPullRequest: ({ title, body, head, base = 'main' }) => api('POST', '/pulls', { title, body, head, base }),
    async listOpenPullRequests(prefix) {
      const pulls = (await api('GET', '/pulls?state=open&per_page=100')) ?? [];
      return pulls.filter((p) => p.head.ref.startsWith(prefix)).map((p) => ({ number: p.number, url: p.html_url, title: p.title }));
    },
  };
}
```

- [ ] **Step 4: Implement the function**

`netlify/functions/metadata-edit.mjs`:

```js
import { loadConfig } from './_lib/config.mjs';
import { HttpError, json, toResponse } from './_lib/http.mjs';
import { requireEditor } from './_lib/session.mjs';
import { readYaml, replaceTopLevelKeys } from './_lib/yamlEdit.mjs';
import { pickEditable, toTopLevelUpdates, validateChanges } from './_lib/editableFields.mjs';
import { createGitHub } from './_lib/github.mjs';

const VOCABULARY_PATH = 'data/tag_vocabulary.yml';
const sourcePath = (id) => `data/sources/${id}.yml`;
const branchPrefix = (id) => `metadata/${id}-`;

function checkId(id) {
  if (typeof id !== 'string' || !/^[a-z0-9-]+$/.test(id)) throw new HttpError(400, 'Invalid dataset id', 'id');
  return id;
}

async function loadAt(github, ref, id) {
  const [file, vocabFile] = await Promise.all([github.getFile(sourcePath(id), ref), github.getFile(VOCABULARY_PATH, ref)]);
  if (!file) throw new HttpError(404, `Dataset ${id} not found`);
  if (!vocabFile) throw new Error(`${VOCABULARY_PATH} missing on ${ref}`);
  const vocab = readYaml(vocabFile.text);
  const vocabulary = { description_tags: vocab.description_tags ?? [], statistical_tags: vocab.statistical_tags ?? {} };
  return { file, current: readYaml(file.text) ?? {}, vocabulary };
}

const cell = (v) => {
  const s = v == null ? '' : typeof v === 'string' ? v : JSON.stringify(v);
  const short = s.length > 300 ? `${s.slice(0, 297)}...` : s;
  return short.replace(/\|/g, '\\|').replace(/\n/g, ' ');
};

function prBody(id, email, current, updates) {
  const rows = Object.keys(updates).map((f) => `| \`${f}\` | ${cell(current[f])} | ${cell(updates[f])} |`);
  return [
    `Metadata edit for \`${id}\` submitted by ${email} via the Data Preview editor.`,
    '',
    '| Field | Before | After |',
    '|---|---|---|',
    ...rows,
  ].join('\n');
}

const timestamp = (ms) => new Date(ms).toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');

export function createHandler({ config, github, now = Date.now }) {
  return async (request) => {
    try {
      const email = requireEditor(request, config, now());

      if (request.method === 'GET') {
        const id = checkId(new URL(request.url).searchParams.get('id'));
        const head = await github.getHeadSha();
        const { current, vocabulary } = await loadAt(github, head, id);
        return json(200, {
          id,
          fields: pickEditable(current, vocabulary),
          manualTags: Array.isArray(current.manual_tags) ? current.manual_tags : [],
          vocabulary,
          openPullRequests: await github.listOpenPullRequests(branchPrefix(id)),
        });
      }

      if (request.method !== 'POST') throw new HttpError(405, 'Method not allowed');
      const body = await request.json().catch(() => {
        throw new HttpError(400, 'Invalid JSON');
      });
      const id = checkId(body?.id);
      const head = await github.getHeadSha();
      const { file, current, vocabulary } = await loadAt(github, head, id);
      const updates = toTopLevelUpdates(current, validateChanges(body.changes, vocabulary), vocabulary);
      if (Object.keys(updates).length === 0) throw new HttpError(400, 'Nothing changed');

      const branch = `${branchPrefix(id)}${timestamp(now())}`;
      await github.createBranch(branch, head);
      try {
        await github.putFile(sourcePath(id), {
          text: replaceTopLevelKeys(file.text, updates),
          sha: file.sha,
          branch,
          message: `Update metadata for ${id}\n\nCo-Authored-By: ${email} <${email}>`,
        });
        const pr = await github.openPullRequest({
          title: `Update metadata: ${current.title ?? id} (by ${email})`,
          body: prBody(id, email, current, updates),
          head: branch,
        });
        console.log(`metadata-edit: ${email} edited ${id} [${Object.keys(updates).join(', ')}] -> PR #${pr.number}`);
        return json(201, { prUrl: pr.html_url, prNumber: pr.number });
      } catch (err) {
        console.error(`metadata-edit: ${email} edit of ${id} failed:`, err.message);
        await github.deleteBranch(branch).catch(() => {});
        throw new HttpError(502, 'Could not open a pull request, so nothing was saved. Please try again.');
      }
    } catch (err) {
      return toResponse(err);
    }
  };
}

export default async (request, context) => {
  const config = loadConfig();
  const github = createGitHub({ token: config.githubToken, repo: config.githubRepo });
  return createHandler({ config, github })(request, context);
};
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add netlify/functions/_lib/github.mjs netlify/functions/metadata-edit.mjs netlify/tests/metadata-edit.test.mjs
git commit -m "Add metadata-edit function that opens a PR per edit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Editor UI on the Data Preview page

**Files:**
- Create: `src/lib/editor/changes.mjs`
- Test: `src/lib/editor/changes.test.mjs`
- Create: `src/components/MetadataEditor.astro`
- Modify: `src/pages/data-preview.astro:1-3` (import) and `:37` (mount inside `#preview-content`)

**Interfaces:**
- Consumes: HTTP API from Tasks 7–8 (`auth-me`, `auth-request-link`, `auth-logout`, `metadata-edit`).
- Produces: `diffFields(original: object, edited: object) -> object` — top-level fields whose canonical value changed; for `provider`, only changed sub-keys; for `download`, `{ url }` when changed.

- [ ] **Step 1: Write the failing test**

`src/lib/editor/changes.test.mjs`:

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { diffFields } from './changes.mjs';

const original = {
  title: 'Milk',
  notes: '',
  sensitive: false,
  provider: { name: 'USDA', agency: '', url: 'https://usda.gov' },
  download: { url: 'https://usda.gov/a.csv' },
  description_tags: ['Agriculture'],
  anova_tags: { a: false, b: true },
};

test('returns only changed fields', () => {
  const edited = structuredClone(original);
  edited.title = 'Milk 2';
  edited.provider.agency = 'ERS';
  edited.anova_tags.a = true;
  assert.deepEqual(diffFields(original, edited), {
    title: 'Milk 2',
    provider: { agency: 'ERS' },
    anova_tags: { a: true, b: true },
  });
});

test('reordered tag lists are not a change; no edits is empty', () => {
  const edited = structuredClone(original);
  edited.description_tags = ['Agriculture'];
  assert.deepEqual(diffFields(original, edited), {});
});

test('download changes send only the url', () => {
  const edited = structuredClone(original);
  edited.download.url = 'https://usda.gov/b.csv';
  assert.deepEqual(diffFields(original, edited), { download: { url: 'https://usda.gov/b.csv' } });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:functions`
Expected: FAIL — `Cannot find module '.../changes.mjs'`.

- [ ] **Step 3: Implement `changes.mjs`**

```js
const same = (a, b) =>
  JSON.stringify(Array.isArray(a) ? [...a].sort() : a) === JSON.stringify(Array.isArray(b) ? [...b].sort() : b);

/** Fields the editor changed, in the shape metadata-edit expects. */
export function diffFields(original, edited) {
  const out = {};
  for (const [field, value] of Object.entries(edited)) {
    if (field === 'provider') {
      const changed = Object.fromEntries(Object.entries(value).filter(([k, v]) => v !== original.provider?.[k]));
      if (Object.keys(changed).length) out.provider = changed;
    } else if (field === 'download') {
      if (value.url !== original.download?.url) out.download = { url: value.url };
    } else if (!same(value, original[field])) {
      out[field] = value;
    }
  }
  return out;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test:functions`
Expected: PASS.

- [ ] **Step 5: Create the component**

`src/components/MetadataEditor.astro`:

```astro
---
// Editor sign-in and metadata form. Server side lives in netlify/functions/;
// on hosts without Netlify Functions (GitHub Pages) this renders nothing visible.
---
<section id="metadata-editor" class="metadata-editor" hidden>
  <p id="editor-message" class="editor-message" role="status" hidden></p>

  <div id="editor-signed-out" hidden>
    <button type="button" id="editor-signin-toggle" class="editor-link">Editor sign in</button>
    <form id="editor-signin-form" hidden>
      <label>Email <input type="email" name="email" required autocomplete="email" /></label>
      <button type="submit">Send sign-in link</button>
    </form>
  </div>

  <div id="editor-signed-in" hidden>
    <p>Signed in as <strong id="editor-email"></strong> · <button type="button" id="editor-signout" class="editor-link">Sign out</button></p>
    <button type="button" id="editor-open">Edit metadata</button>
    <div id="editor-panel" hidden>
      <ul id="editor-open-prs" class="editor-prs"></ul>
      <form id="editor-form"></form>
    </div>
  </div>
</section>

<script>
  import { diffFields } from '@lib/editor/changes.mjs';

  const FN = '/.netlify/functions';
  const TEXT = [['title', 'Title'], ['notes', 'Notes'], ['who', 'Who'], ['how', 'How'], ['where', 'Where'], ['why', 'Why'], ['when', 'When']];
  const AUTH_MESSAGES: Record<string, string> = {
    ok: 'You are signed in.',
    expired: 'That sign-in link has expired or was already used. Request a new one.',
    invalid: 'That sign-in link is not valid. Request a new one.',
  };

  const $ = (id: string) => document.getElementById(id) as HTMLElement;
  const root = $('metadata-editor');
  const message = $('editor-message');
  const sourceId = new URLSearchParams(location.search).get('source')?.trim() ?? '';
  let original: any = null;
  let vocabulary: any = null;

  function say(text: string, isError = false) {
    message.textContent = text;
    message.classList.toggle('is-error', isError);
    message.hidden = !text;
  }

  async function api(path: string, init: RequestInit = {}) {
    const res = await fetch(`${FN}/${path}`, { credentials: 'same-origin', ...init, headers: { 'content-type': 'application/json', ...(init.headers ?? {}) } });
    const type = res.headers.get('content-type') ?? '';
    return { res, body: type.includes('application/json') ? await res.json() : null };
  }

  function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, any> = {}, children: (Node | string)[] = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'checked' || k === 'value') (node as any)[k] = v;
      else node.setAttribute(k, String(v));
    }
    node.append(...children);
    return node;
  }

  function renderForm(fields: any, manualTags: string[]) {
    const form = $('editor-form') as HTMLFormElement;
    form.replaceChildren();
    for (const [name, label] of TEXT) {
      const input = name === 'notes' ? el('textarea', { name, rows: 4 }) : el('input', { name, type: 'text' });
      (input as HTMLInputElement).value = fields[name];
      form.append(el('label', {}, [label, input]));
    }
    for (const [name, label] of [['provider.name', 'Provider name'], ['provider.agency', 'Provider agency'], ['provider.url', 'Provider URL'], ['download.url', 'Download URL']]) {
      const [group, key] = name.split('.');
      form.append(el('label', {}, [label, el('input', { name, type: 'url', value: fields[group][key] })]));
    }
    for (const [name, label] of [['sensitive', 'Sensitive'], ['requires_account', 'Requires account']]) {
      form.append(el('label', {}, [el('input', { name, type: 'checkbox', checked: fields[name] }), label]));
    }
    const suitability = el('select', { name: 'student_suitability' }, ['suitable', 'not_suitable', 'unreviewed'].map((v) => el('option', { value: v }, [v.replace('_', ' ')])));
    (suitability as HTMLSelectElement).value = fields.student_suitability;
    form.append(el('label', {}, ['Student suitability', suitability]));

    const groupBox = (field: string, title: string, options: string[], isChecked: (k: string) => boolean) =>
      el('fieldset', { 'data-tag-field': field }, [
        el('legend', {}, [title, manualTags.includes(field) ? el('span', { class: 'editor-badge' }, ['set by hand']) : '']),
        ...options.map((k) => el('label', {}, [el('input', { type: 'checkbox', value: k, checked: isChecked(k) }), k])),
      ]);
    form.append(groupBox('description_tags', 'Topic tags', vocabulary.description_tags, (k) => fields.description_tags.includes(k)));
    const stats = el('details', {}, [el('summary', {}, ['Statistical tags'])]);
    for (const [group, keys] of Object.entries<string[]>(vocabulary.statistical_tags)) {
      stats.append(groupBox(group, group.replace(/_tags$/, '').replace(/_/g, ' '), keys, (k) => fields[group][k]));
    }
    form.append(stats, el('button', { type: 'submit' }, ['Submit for review']));
  }

  function readForm() {
    const form = $('editor-form') as HTMLFormElement;
    const value = (name: string) => (form.elements.namedItem(name) as HTMLInputElement).value;
    const edited: any = {};
    for (const [name] of TEXT) edited[name] = value(name);
    edited.provider = { name: value('provider.name'), agency: value('provider.agency'), url: value('provider.url') };
    edited.download = { url: value('download.url') };
    edited.sensitive = (form.elements.namedItem('sensitive') as HTMLInputElement).checked;
    edited.requires_account = (form.elements.namedItem('requires_account') as HTMLInputElement).checked;
    edited.student_suitability = value('student_suitability');
    for (const fieldset of form.querySelectorAll<HTMLFieldSetElement>('fieldset[data-tag-field]')) {
      const field = fieldset.dataset.tagField!;
      const boxes = [...fieldset.querySelectorAll<HTMLInputElement>('input[type=checkbox]')];
      edited[field] = field === 'description_tags'
        ? boxes.filter((b) => b.checked).map((b) => b.value)
        : Object.fromEntries(boxes.map((b) => [b.value, b.checked]));
    }
    return edited;
  }

  async function openEditor() {
    say('Loading current metadata…');
    const { res, body } = await api(`metadata-edit?id=${encodeURIComponent(sourceId)}`);
    if (!res.ok) return say(body?.error ?? 'Could not load metadata.', true);
    original = body.fields;
    vocabulary = body.vocabulary;
    const prs = $('editor-open-prs');
    prs.replaceChildren(...body.openPullRequests.map((p: any) => el('li', {}, ['Open edit awaiting review: ', el('a', { href: p.url, target: '_blank', rel: 'noopener' }, [`PR #${p.number}`])])));
    renderForm(original, body.manualTags);
    $('editor-panel').hidden = false;
    say('');
  }

  async function init() {
    const authResult = new URLSearchParams(location.search).get('auth');
    const { res, body } = await api('auth-me').catch(() => ({ res: null as Response | null, body: null }));
    if (!res || ![200, 401, 403].includes(res.status)) return; // no Netlify Functions here
    root.hidden = false;
    if (authResult && AUTH_MESSAGES[authResult]) say(AUTH_MESSAGES[authResult], authResult !== 'ok');
    if (res.status === 403) say('Your editor access has been removed.', true);

    if (res.status === 200) {
      $('editor-signed-in').hidden = false;
      $('editor-email').textContent = body.email;
      $('editor-open').hidden = !sourceId;
    } else {
      $('editor-signed-out').hidden = false;
    }
  }

  $('editor-signin-toggle').addEventListener('click', () => {
    $('editor-signin-form').hidden = false;
  });

  $('editor-signin-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const email = new FormData(event.target as HTMLFormElement).get('email');
    const { res, body } = await api('auth-request-link', { method: 'POST', body: JSON.stringify({ email }) });
    say(body?.message ?? body?.error ?? 'Something went wrong.', !res.ok);
  });

  $('editor-signout').addEventListener('click', async () => {
    await api('auth-logout', { method: 'POST' });
    location.reload();
  });

  $('editor-open').addEventListener('click', openEditor);

  $('editor-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const changes = diffFields(original, readForm());
    if (Object.keys(changes).length === 0) return say('No changes to submit.');
    const { res, body } = await api('metadata-edit', { method: 'POST', body: JSON.stringify({ id: sourceId, changes }) });
    if (res.status === 401) return say('Your session expired. Sign in again.', true);
    if (!res.ok) return say(body?.field ? `${body.field}: ${body.error}` : body?.error ?? 'Could not submit.', true);
    say(`Submitted for review: PR #${body.prNumber}.`);
    const link = el('a', { href: body.prUrl, target: '_blank', rel: 'noopener' }, [' View pull request']);
    message.append(link);
    $('editor-panel').hidden = true;
  });

  init();
</script>

<style>
  .metadata-editor { margin: 1rem 0; padding: 1rem; border: 1px solid var(--color-border, #d0d7de); border-radius: 8px; }
  .metadata-editor label { display: flex; flex-direction: column; gap: 0.25rem; margin: 0.5rem 0; }
  .metadata-editor fieldset label { flex-direction: row; align-items: center; display: inline-flex; margin-right: 1rem; }
  .metadata-editor input[type='text'], .metadata-editor input[type='url'], .metadata-editor input[type='email'], .metadata-editor textarea, .metadata-editor select { width: 100%; max-width: 40rem; }
  .editor-link { background: none; border: 0; padding: 0; color: inherit; text-decoration: underline; cursor: pointer; }
  .editor-message.is-error { color: #b42318; }
  .editor-badge { margin-left: 0.5rem; font-size: 0.75rem; padding: 0.1rem 0.4rem; border-radius: 999px; background: #fff4d6; }
</style>
```

- [ ] **Step 6: Mount it on the page**

In `src/pages/data-preview.astro` frontmatter (after line 3):

```astro
import MetadataEditor from '@components/MetadataEditor.astro';
```

Directly after `<div id="preview-content" hidden>` (line 37):

```astro
      <MetadataEditor />
```

- [ ] **Step 7: Build and lint**

Run: `npm run build && npx eslint src/components/MetadataEditor.astro src/lib/editor`
Expected: build succeeds; no new lint errors.

- [ ] **Step 8: Commit**

```bash
git add src/lib/editor src/components/MetadataEditor.astro src/pages/data-preview.astro
git commit -m "Add metadata editor UI to the Data Preview page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Netlify build uses canonical backend data; setup docs; end-to-end check

**Files:**
- Modify: `netlify.toml` (`[build] command`)
- Modify: `.gitignore` (add `.backend-canonical/`)
- Create: `docs/technical/metadata-editor.md`
- Modify: `docs/technical/architecture.md` (one line under the Decap CMS paragraph linking the new doc)

**Interfaces:**
- Consumes: everything above.
- Produces: Netlify builds that sync `data/sources` from `community-data-libraries/backend@main`.

- [ ] **Step 1: Point the Netlify build at the canonical backend**

In `netlify.toml`:

```toml
[build]
  command = "rm -rf .backend-canonical && git clone --depth 1 https://github.com/community-data-libraries/backend.git .backend-canonical && BACKEND_ROOT=.backend-canonical npm run build"
  publish = "dist"
  functions = "netlify/functions"
```

The clone lives inside the build directory (writable on Netlify) and is git-ignored. Append to `.gitignore`:

```
.backend-canonical/
```

- [ ] **Step 2: Verify the same command locally**

Run (from `web/`): `rm -rf .backend-canonical && git clone --depth 1 https://github.com/community-data-libraries/backend.git .backend-canonical && BACKEND_ROOT=.backend-canonical npm run build; rm -rf .backend-canonical`
Expected: `prebuild` prints `Mirrored N source file(s) from …/.backend-canonical/data/sources` and `Removed stale mirror: …` for datasets upstream deleted; build succeeds.

The sync rewrites `backend/data/sources/` and `src/content/master-library/datasets/` in the working tree. Do **not** commit those in this task (refreshing the committed mirror is a separate decision). Run `git status --short`, then `git checkout -- backend/data/sources src/content/master-library/datasets` to restore modified/deleted files, and delete any new untracked files `git status` lists under those two directories.

- [ ] **Step 3: Write the setup doc**

`docs/technical/metadata-editor.md`:

````markdown
# Metadata editor (magic-link sign-in)

Approved editors can sign in on `/web/data-preview` and submit dataset-metadata edits.
Each edit opens a pull request on `community-data-libraries/backend`; merging it rebuilds the
Netlify site. Works on the Netlify deployment only.

Design: `backend/docs/superpowers/specs/2026-10-14-metadata-editor-auth-design.md`.

## Netlify environment variables

| Variable | Value |
|---|---|
| `EDITOR_EMAILS` | Comma-separated approved emails. Removing one revokes access immediately. |
| `AUTH_SECRET` | `openssl rand -base64 48`. Rotating it signs everyone out. |
| `RESEND_API_KEY` | From resend.com (domain verified for `MAIL_FROM`). |
| `MAIL_FROM` | e.g. `CDL Editors <editors@your-domain>` |
| `GITHUB_TOKEN` | Fine-grained token: repo `community-data-libraries/backend`; Contents read/write, Pull requests read/write. |
| `GITHUB_REPO` | Optional; defaults to `community-data-libraries/backend`. Point at a fork to test. |
| `SITE_ORIGIN` | Exact site origin, e.g. `https://cdl.netlify.app` (no trailing slash). Deploy previews have a different origin, so editing is disabled there. |

## Build hook

Create a build hook in Netlify (Site configuration → Build & deploy → Build hooks) and save its
URL as the `NETLIFY_BUILD_HOOK` secret in the backend repo. The backend's
`netlify-rebuild.yml` workflow calls it when `data/**` changes on `main`.

## Tags

Allowed tags come from `backend/data/tag_vocabulary.yml`. Tag groups an editor changes are listed
in the dataset's `manual_tags` and are not overwritten by the pipeline. To hand a group back to the
pipeline, remove it from `manual_tags` in a pull request.

## Local testing

`npm run test:functions` runs the unit tests. For an end-to-end check use `netlify dev` with the
variables above in `.env`, `SITE_ORIGIN=http://localhost:8888`, and `GITHUB_REPO` set to a fork.
````

In `docs/technical/architecture.md`, after the Decap CMS paragraph (line 94), add:

```markdown
Dataset metadata (backend YAML) is edited separately through the magic-link metadata editor on `/data-preview`; see [metadata-editor.md](metadata-editor.md).
```

- [ ] **Step 4: Manual end-to-end check (needs a Resend key and a fork)**

1. `npx netlify dev` with `.env` containing all variables, `SITE_ORIGIN=http://localhost:8888`, `GITHUB_REPO=<your-user>/backend`. Note: the cookie is `Secure`; browsers accept it on `localhost`.
2. Open `http://localhost:8888/web/data-preview?source=usda-milk-production`; click "Editor sign in", submit your email.
3. Open the emailed link: confirm page appears; click "Sign in"; you land back signed in.
4. Click "Edit metadata", change the title and tick one `anova` tag, submit.
5. On the fork: the PR diff touches only `title`, `anova_tags`, and adds `manual_tags`.
6. Open the same email link again → "expired or was already used".

Expected: all six behave as described.

- [ ] **Step 5: Commit**

```bash
git add netlify.toml .gitignore docs/technical/metadata-editor.md docs/technical/architecture.md
git commit -m "Build Netlify from canonical backend data; document metadata editor setup

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Part C — backend repo (after Part B)

### Task 11: Rebuild Netlify when backend data changes

**Repo:** `backend/`

**Files:**
- Create: `.github/workflows/netlify-rebuild.yml`

**Interfaces:**
- Consumes: Netlify build hook URL in repo secret `NETLIFY_BUILD_HOOK` (Task 10 doc).

- [ ] **Step 1: Write the workflow**

```yaml
name: Rebuild website

on:
  push:
    branches: ["main"]
    paths: ["data/**"]
  workflow_dispatch:

jobs:
  trigger-netlify:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Netlify build hook
        env:
          HOOK: ${{ secrets.NETLIFY_BUILD_HOOK }}
        run: |
          if [ -z "$HOOK" ]; then
            echo "NETLIFY_BUILD_HOOK secret is not set; skipping." >&2
            exit 0
          fi
          curl --fail --silent --show-error -X POST -d '{}' "$HOOK"
```

- [ ] **Step 2: Validate syntax**

Run: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/netlify-rebuild.yml')); assert d[True]['push']['paths']==['data/**']; print('ok')"`
Expected: `ok` (PyYAML parses `on` as `True`).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/netlify-rebuild.yml
git commit -m "Trigger Netlify rebuild when backend data changes on main

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: After merge — verify once**

Run the workflow manually from the Actions tab (`workflow_dispatch`) after the secret is set; confirm a new deploy starts in Netlify.
