# ontic-pages: agent notes

Simple HTML hosting for the team: `ontic-pages login|publish|list|info|pull|share|delivery|set-current|url|comments|comment|resolve|gateway`.
The commands talk to the gateway with a token from `login`; `--direct` uses a bucket key instead.
See README.md for what it does and deploy/README.md for how it is hosted.

## Layout

- `src/ontic_pages/config.py`: config file and environment variables.
- `src/ontic_pages/store.py`: bucket layout, the S3 calls, content types, name rules.
- `src/ontic_pages/publish.py`: collecting files, the file path rules, git metadata, writing a
  version (page.json, visibility, current); shared by `--direct` and the gateway's commit.
- `src/ontic_pages/uploads.py`: publishing through the gateway, its side: checks the file list,
  picks the version, presigned PUT URLs, the size check and the commit.
- `src/ontic_pages/edits.py`: text edited in the bar, the gateway's side: finds each changed text
  in the HTML source (exactly once, else nothing is saved), and writes a new version (server-side
  copies of the other files, the patched file, page.json with `edited_from`), reading only what
  the gateway's caches do not hold, and logs one timing line per save (`Phases`).
- `src/ontic_pages/pull.py`: `ontic-pages pull`: the gateway's presigned GET URLs for a version's
  files, and downloading them into a folder.
- `src/ontic_pages/tokens.py`: the command line's tokens (HMAC signed, 30 days) and the login
  codes of the browser hand-off. In memory only.
- `src/ontic_pages/remote.py`: the command line's side of the gateway: the token file, JSON
  calls, `login`, and uploading files to the presigned URLs.
- `src/ontic_pages/info.py`: the facts behind `ontic-pages info`, the gateway's `/<name>/_info` page,
  the short-name helper, the pillar labels and `stamp` (every shown time is DD.MM.YYYY hh:mm, local).
- `src/ontic_pages/gateway.py`: the HTTP server (stdlib `http.server`): routing on the apex and
  on page hosts (`<name>.<suffix>`), the API, file answers with ETags. Sign-in stays in
  oauth2-proxy.
- `src/ontic_pages/auth.py`: who is asking (the oauth2-proxy `/oauth2/auth` subrequest, or
  `--local-as`), the access rule, the write rate limit. Never read identity request headers.
  A Bearer token (tokens.py) is the other identity; it is refused with any Origin or
  Sec-Fetch-Site (browsers never hold tokens).
- `src/ontic_pages/cache.py`: the in-memory caches (version files and file lists, page state);
  a saved edit seeds them with what it wrote. `DirectLinks`: the presigned GET URLs handed out
  with direct files on (the same URL for a file for 50 minutes).
- `src/ontic_pages/comments.py`: comments, one `<name>/comments.json` per page written only by the
  gateway (read-modify-write under a lock per page, cached a few seconds); cleaning of comment
  text and anchors; deleted comments stay as tombstones.
- `src/ontic_pages/milestones.py`: the research overview's shared state, `_milestones.json` and
  `_views.json` at the bucket root (gateway-only writers like comments): checks of milestone
  fields and of a saved view's config. `milestones_cli.py`: `milestones` and `milestone add|update`.
- `src/ontic_pages/github.py`: the milestones kept in step with the GitHub milestones of
  `onticlabs/experiments`, both ways, by a thread in the gateway (polls every 30 s, and soon
  after a change here): a field-by-field merge against the last synced fields, the extra fields
  as bullet lines in the GitHub description, deleted on GitHub only (to the bin here, restorable).
  Stdlib urllib; off without `ONTIC_PAGES_GITHUB_TOKEN`. Tests use a fake GitHub, never the API.
- `src/ontic_pages/markdown.py`: Markdown updates on markdown-it-py (no raw HTML, safe URL
  schemes, image links to videos become players), math as MathML (latex2mathml, the output
  checked to MathML elements only), and the standalone page around it.
- `src/ontic_pages/comments_cli.py`: the `comments`, `comment` and `resolve` commands (always
  through the gateway; short thread ids; waits when rate limited).
- `src/ontic_pages/shell.py` and `src/ontic_pages/static/`: the bar (shell HTML, bar.js,
  bar.css), the bridge script added to framed HTML, and the command line's login page
  (login.js). Static files are package data, served at content-hashed URLs. User text goes into
  the bar with textContent, never innerHTML. A feature in files of its own is served as part of
  these or next to them (`shell.BUNDLES`):
  - Details (the info button's panel, also opened from the title menu, with one version's
    page.json, asked from
    `/_api/pages/<name>/versions/<version>` when opened): `details.js` (after bar.js, hooked in
    through `window.onticBar`) and `details.css` (after bar.css).
  - comments: `comments.js` and `comments.css` in the bar, `pins.js` after bridge.js (the pins,
    the hover outline in comment mode, the outline of the open or hovered thread's element). The
    page in the frame never gets comment text or emails, only pins and `highlight {id}`.
  - edit text in place, always on for the owner (no Edit button): `edit.js` (after bar.js,
    hooked in through `window.onticBar`), `edit.css` (after bar.css) and `edit-bridge.js`
    (after bridge.js; it also hears `comment-mode` and pauses editing then). Its bridge
    messages: `edit-mode` (on/off, `discard`, `save`, `saved`: the text on screen is the saved
    original now, no reload), `edit-state` (the count, `save` for Cmd/Ctrl+S), `edits`.
- `src/ontic_pages/skills.py` and `src/ontic_pages/skills/ontic-pages/SKILL.md`: the agent skill
  (package data), synced into Claude Code and Codex on every command but `gateway` and `skill`.
  Edit the skill text there, never in an installed copy.
- The overview at `/` (`shell.overview_html`, `static/overview.js` and `overview.css`): a
  Notion-like database with two tables (reports, milestones), saved views per table, and the composer (Markdown with dropped media,
  uploaded by the browser to presigned URLs; the gateway renders at commit, uploads.py). Text goes
  in with textContent; the preview is the rendered page in a frame without scripts.
- `src/ontic_pages/cli.py`: argparse commands.
- `scripts/migrate_old_pages.py`: one-off copy of the old `ontic pages` jobs into this layout.
- `tests/`: pytest, against `tests/fake_s3.py` (an in-memory fake of the few boto3 calls used;
  `helpers.upload_server` takes its presigned PUTs and answers its GETs). `test_js.py` runs
  bridge.js and bar.js in node, `test_comments_js.py` pins.js and comments.js, and
  `test_edit_js.py` the served edit mode, `test_details_js.py` the Details panel, each with a small fake DOM (all skipped without node).
- `tests/test_direct_files_browser.py`: optional Chromium/Playwright regression suite, run with
  `uv run --with playwright --with nodejs-wheel pytest -q`; CHROMIUM_PATH selects the browser.
  `direct_bucket.py` models private grants, expiry, Range and CORS without production credentials.
- `deploy/`: container image, Quadlet units, Caddy and oauth2-proxy config, deploy scripts.

## Rules

- Use uv for everything: `uv run pytest -q`, `uv run ruff check`, `uv add <pkg>`. Never bare python or pip.
- Never import the `ontic` package or depend on ontic-cli. This tool stands alone. The one
  exception is `scripts/migrate_old_pages.py`, a one-off bridge that reads the old system; run it
  with `uv run --with /path/to/cli scripts/migrate_old_pages.py`. Never import ontic in `src/`.
- Standard library first. Runtime dependencies: boto3, and for Markdown updates markdown-it-py
  (with linkify), mdit-py-plugins and latex2mathml; ask before adding another.
- Keep it small. Provenance is metadata in `page.json`, never checked.
- The tool never deletes anything from the bucket. Deleting a page sets `deleted` in its
  `labels.json`: hidden everywhere, 404 at its address, restorable.
- Uploaded file bytes go to presigned URLs (Caddy caps request bodies at 64 KB). Download
  delivery is per page in labels.json: missing means server; only new names default to direct.
  Preserve it across publishes, edits and version switches. Direct mode redirects eligible
  CORS GETs for data/media; dependencies, HEAD and no-cors/same-origin browser requests remain
  on the gateway. Redirects are private, no-store. ONTIC_PAGES_DIRECT_FILES=0 is global rollback;
  omitted/empty defaults to enabled. Only the gateway holds a bucket key in normal use.
- Add or update a test for every behavior change; when you use a new boto3 call, add it to the fake.
- Never commit `deploy/secrets.env` or any credential.
- Never use em dashes in any text.
