# Metadata Editor with Magic-Link Auth — Design

Date: 2026-10-14
Scope: `web/netlify/functions/` (new auth + edit functions), `web/src/pages/data-preview.astro`
(editor UI), `web` Netlify build config, and in `backend/`: a tag vocabulary file, manual-tag
preservation in the pipeline, and a build-hook workflow.
Out of scope: hosting the backend preview API in production (see "Known gaps"), and the
redesign of the tagging system itself (owned by the team; this design only makes tags
editable against whatever vocabulary that redesign produces).

## Goal

Let a small, approved list of people sign in by magic link on the Data Preview page and
revise a dataset's metadata — including its tags, chosen from a fixed vocabulary. Every
edit becomes a pull request against `community-data-libraries/backend`; merging it
publishes the change to the site.

Success criteria:

- Only emails on the allowlist can obtain a session; removing an email revokes access on
  the next request.
- An edit produces a PR whose diff touches only the changed lines of one
  `data/sources/<id>.yml` file.
- Tags can only be set to values in the vocabulary file.
- Tags set by an editor survive a full pipeline re-run.
- Merging a metadata PR rebuilds the Netlify site with the new data, with no manual steps.

## Current state (relevant facts)

- `web` is a static Astro site (`output: 'static'`) deployed to Netlify and GitHub Pages.
  Netlify Functions already exist (`netlify/functions/submission-notify.js`).
- The backend YAML in git is the source of truth for metadata. The web build syncs it via
  `scripts/sync-backend-yaml.mjs`, which reads `../backend` (or `BACKEND_ROOT`). On Netlify
  and CI that directory does not exist, so the sync is skipped and the build uses stale
  copies committed in `web/backend/data/sources/` (which still contain datasets upstream
  has deleted).
- Topic tags (`description_tags`, a list) are written by `tagdataset.py` from the keys of
  `TAG_RULES` (19 categories). Statistical tags are 16 `*_tags` groups, each a
  `dict[str, bool]` with a fixed key set, written by 16 separate scripts orchestrated by
  `runall.py`.
- Netlify Identity is loaded by Decap CMS, but is invite+password (no magic link) and is
  not used for this feature.

## Architecture

All new server code runs as Netlify Functions in `web/netlify/functions/`. The backend
Node server stays read-only and is not involved. Editing works on the Netlify deployment
only; on GitHub Pages the editor entry point is hidden (detected by `auth-me` being
unreachable).

| Function | Method | Responsibility |
|---|---|---|
| `auth-request-link` | POST `{email}` | Allowlist check, rate limit, send magic link via Resend. Always responds 200 with the same body. |
| `auth-verify` | GET `?token=` / POST | GET: check token, render a confirm page (does not consume). POST: verify, burn nonce, set session cookie, redirect to `/web/data-preview`. |
| `auth-me` | GET | `{email}` if signed in, else 401. |
| `auth-logout` | POST | Clear the session cookie. |
| `metadata-edit` | GET `?id=` | Current editable fields, tag vocabulary, and open metadata PRs for the dataset. |
| `metadata-edit` | POST `{id, changes}` | Validate, open a PR, return its URL. |

Shared code lives in `web/netlify/functions/_lib/` as small modules with single
responsibilities, each unit-testable without network:

- `tokens.mjs` — sign/verify HMAC-SHA256 tokens (`base64url(payload).base64url(sig)`),
  constant-time comparison, expiry check.
- `allowlist.mjs` — parse `EDITOR_EMAILS` (comma-separated), normalize (trim, lowercase),
  membership test.
- `session.mjs` — read/write the session cookie; `requireEditor(request)` performs the
  full guard (see below) and returns the email or throws a 401/403.
- `editableFields.mjs` — field whitelist, per-field validators, and applying changes to a
  YAML document.
- `github.mjs` — thin wrapper over the GitHub REST API (get file + sha, create branch,
  commit file, open PR, list open PRs by branch prefix).
- `mailer.mjs` — thin wrapper over the Resend API.
- `rateLimit.mjs` and nonce storage — Netlify Blobs.

### Configuration (Netlify environment variables)

| Variable | Purpose |
|---|---|
| `EDITOR_EMAILS` | Comma-separated allowlist. |
| `AUTH_SECRET` | ≥32 random bytes; HMAC key for link tokens and session cookies. |
| `RESEND_API_KEY`, `MAIL_FROM` | Sending magic-link email. |
| `GITHUB_TOKEN` | Fine-grained PAT (or GitHub App token) scoped to `community-data-libraries/backend` with Contents: read/write and Pull requests: read/write only. |
| `GITHUB_REPO` | `community-data-libraries/backend` (overridable for testing against a fork). |
| `SITE_ORIGIN` | Expected `Origin` for state-changing requests. |

## Login flow

1. The Data Preview page shows "Editor sign in" (Netlify only). It opens a form that posts
   the email to `auth-request-link`.
2. `auth-request-link` normalizes the email. Rate limits: 3 requests per email per 15 min,
   20 per IP per hour (Netlify Blobs counters). If allowlisted and under the limit, it
   signs a link token `{email, exp: now+15min, nonce: 16 random bytes, purpose: "link"}`
   and emails `https://<site>/.netlify/functions/auth-verify?token=…` via Resend. The
   response is identical in every case so the allowlist cannot be probed.
3. `auth-verify` GET only checks the token and renders a minimal confirm page with a
   "Sign in" button that POSTs the token back. It never consumes the token, because email
   security scanners (e.g. Outlook Safe Links) pre-fetch links and would otherwise burn
   single-use links before the person clicks. On POST, `auth-verify` verifies signature,
   `purpose`, and expiry; rejects if the nonce is already
   recorded in Blobs, otherwise records it (with TTL past expiry); re-checks the allowlist;
   sets `cdl_session` = signed `{email, exp: now+7d, purpose: "session"}` with
   `HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800`; redirects to the preview page.
   Failures redirect to the preview page with `?auth=expired` (or `invalid`), which shows a
   "request a new link" message.
4. The page calls `auth-me` on load and shows editor controls only when signed in.

### Guard on every editor request (`requireEditor`)

1. Session cookie present, signature valid, `purpose === "session"`, not expired.
2. Email still in `EDITOR_EMAILS` (revocation is immediate).
3. For POSTs: `Origin` header equals `SITE_ORIGIN` (CSRF defense alongside `SameSite=Lax`).

Signing out clears the cookie; there is no server-side session store, so a stolen cookie
stays valid until expiry or until the email is removed or `AUTH_SECRET` is rotated (which
signs everyone out). This is acceptable for a small trusted editor group.

## Editable fields

| Field | Editor control | Validation |
|---|---|---|
| `title` | text | required, 1–200 chars |
| `notes` | textarea | ≤ 5,000 chars |
| `who`, `how`, `where`, `why`, `when` | text | string ≤ 500 chars, or empty → `null` |
| `sensitive`, `requires_account` | checkbox | boolean |
| `provider` (`name`, `agency`, `url`) | text ×3 | strings ≤ 200; `url` must be `https://` |
| `download.url` | text | `https://` URL |
| `student_suitability` | select | one of `suitable`, `not_suitable`, `unreviewed` (matches the SQLite `CHECK` constraint) |
| `description_tags` | checkbox list from vocabulary | every value in `vocabulary.description_tags`; no duplicates |
| each `*_tags` group | checkbox per key from vocabulary | object whose keys are exactly the vocabulary keys for that group; values boolean |

Read-only (pipeline-computed, not tags): `id`, `version`, `variable_names`,
`variable_report`, `geographic_coverage`, `coverage`, `geographic_granularity`, `location`.

Any key in `changes` not in the table is rejected with 400 naming the field.

## Tag vocabulary

New file: `backend/data/tag_vocabulary.yml` — the single source of truth for allowed tags,
owned by the team doing the tagging redesign.

```yaml
description_tags:
  - Agriculture
  - Business & Economy
  # … (initially the 19 TAG_RULES keys)
statistical_tags:
  anova_tags:
    - good-for-one-way-anova
    # … (initially the key sets currently found in data/sources/*.yml)
  # … one entry per *_tags group
```

- `metadata-edit` GET reads it from GitHub (`main`) and returns it to the form, so a
  vocabulary change takes effect for editors as soon as it is merged — no web redeploy.
- A backend test asserts every `data/sources/*.yml` uses only vocabulary values, and that
  `TAG_RULES` keys equal `vocabulary.description_tags`. During the redesign the team
  updates the vocabulary and rules together; the test keeps them in lockstep.

### Preserving manual tags across pipeline runs

When an edit changes any tag field, `metadata-edit` adds that field name to a list in the
dataset YAML:

```yaml
manual_tags:
  - description_tags
  - anova_tags
```

`runall.py` snapshots the listed fields from every source file before running the scripts
and restores them afterwards (a shared helper, `pythonscripts/manual_tags.py`), so no
individual analysis script needs changing. `tagdataset.py`, which can also be run on its
own, skips `description_tags` when it is listed. Clearing manual control is done by
removing the field from `manual_tags` in a normal PR. `manual_tags` is read-only in the
editor UI but shown ("set by hand") next to the affected groups.

## Edit → pull request

`metadata-edit` POST:

1. `requireEditor`; validate `id` against `^[a-z0-9-]+$` and that the file exists.
2. Validate `changes` (table above); reject empty or no-op changes.
3. Fetch `data/sources/<id>.yml` and its blob `sha` from `main`.
4. Parse with the `yaml` package to read current values, then replace only the source text
   of each changed top-level key (located via the parsed node ranges), adding tag fields to
   `manual_tags` if needed; new keys are appended at the end. Every untouched byte is
   preserved. (Re-serializing the whole document is not acceptable: it re-wraps long
   strings and turns floats like `-181533.0` into ints.) Replacement blocks are emitted with
   `indentSeq: false` (PyYAML list style) and `version: '1.1'` so strings such as `yes`,
   `on`, or `2025` are quoted and read back by PyYAML as strings.
5. Create branch `metadata/<id>-<UTC timestamp>` from `main`'s head, commit the file
   (message `Update metadata for <id>`, trailer `Co-Authored-By: <email>`), and open a PR
   titled `Update metadata: <title> (by <email>)`. The body lists each changed field with
   before/after values.
6. Return `{prUrl, prNumber}`; the page shows "Submitted for review: PR #N".

The branch is created only immediately before the commit; if the commit or PR call fails,
the function deletes the branch, so no partial state is left. Concurrent edits to the same
dataset produce separate PRs; the second to merge resolves an ordinary git conflict on
GitHub.

Reviewers merge on GitHub. Existing backend CI (tests and the catalog `--check`) runs on
the PR.

## Editor UI (data-preview.astro)

- Signed-in header: "Signed in as <email> · Sign out".
- "Edit metadata" opens a panel with a form for the editable fields, pre-filled from
  `metadata-edit` GET (the YAML on `main`, not the possibly stale preview data).
- Tags: `description_tags` as a checkbox list; each statistical group as a collapsible
  checkbox set. Groups listed in `manual_tags` show a "set by hand" badge.
- Open metadata PRs for this dataset are listed above the form with links, to discourage
  duplicate edits.
- The editor UI is a separate component/script from the existing 1,360-line page, which
  only mounts it, to keep the page from growing further.

## Publishing merged edits

- Netlify build: before `npm run build`, clone `community-data-libraries/backend` (shallow,
  `main`) into the build workspace and set `BACKEND_ROOT` to it, so the existing `prebuild`
  sync uses current canonical data. This also fixes the stale mirror on Netlify.
- New workflow `backend/.github/workflows/netlify-rebuild.yml`: on push to `main` touching
  `data/**`, POST to a Netlify build hook (URL stored as a repo secret `NETLIFY_BUILD_HOOK`).
- GitHub Pages builds are unchanged (they continue to use the committed mirror). This is
  noted, not fixed, since editing is Netlify-only.

## Error handling

| Situation | Behavior |
|---|---|
| Link expired / reused / bad signature | Redirect with `?auth=expired`/`invalid`; page offers a new link. |
| Rate limited | Same 200 response as success (no signal); logged. |
| Session missing/expired | 401; page drops to signed-out state. |
| Email removed from allowlist | 403; page shows "access removed". |
| Validation failure | 400 `{field, message}`; form highlights the field. |
| Resend failure | 200 to the client (no allowlist leak); error logged. |
| GitHub failure | 502 `{message}`; branch cleaned up; form keeps the user's input. |
| Dataset file missing on `main` | 404. |

Function logs record each edit attempt: email, dataset id, changed field names, outcome,
PR number. Tokens and cookies are never logged.

## Testing

- Unit (`node:test`, no network): token sign/verify incl. tampering, expiry, wrong
  purpose; allowlist normalization; `requireEditor` cases (no cookie, expired, revoked,
  bad origin); every field validator; tag validation against a fixture vocabulary;
  YAML round-trip keeps untouched bytes identical and only changes the targeted lines;
  `manual_tags` bookkeeping.
- Function tests with `github.mjs`, `mailer.mjs`, and Blobs mocked: request-link responses
  are indistinguishable for allowed vs. unknown emails; nonce reuse rejected; edit flow
  calls GitHub in order and cleans up on failure.
- Backend (pytest): vocabulary consistency test; `manual_tags` snapshot/restore helper;
  `tagdataset.py` skips manual `description_tags`.
- Manual end-to-end with `netlify dev` and `GITHUB_REPO` pointed at a fork: sign in,
  edit text and tags, confirm PR diff is minimal, merge, confirm the build hook fires.

## Known gaps (not addressed here)

- The Data Preview page's tables and charts call the backend API, which has no production
  host; `PUBLIC_API_BASE_URL` is unset, so in production they fall back to
  `localhost:4323`. The editor does not depend on that API, but the preview data will not
  load on Netlify until the backend is hosted. Separate project.
- `web/backend/` mirror and its `pythonscripts/tagdataset.py` starter copy diverge from the
  canonical backend; unchanged by this design.
