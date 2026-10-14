# Deploying the gateway: `https://pages.onticlabs.io/<name>/`

The gateway runs on the team box (`ontic-vps`, 159.195.206.154) behind the same
Caddy and the same kind of Google sign-in that Olympus and Atlas use.

    pages.onticlabs.io/oauth2/*  browser -> caddy :443 -> pages-oauth2-proxy 127.0.0.1:4190  (Google sign-in)
    pages.onticlabs.io/*         browser -> caddy :443 -> pages-gateway 127.0.0.1:8790       (listing, bar, API)
    <name>.pages.onticlabs.io/*  browser -> caddy :443 -> pages-gateway 127.0.0.1:8790       (the page itself)
    gateway -> pages-oauth2-proxy (http://oauth2-proxy:4180/oauth2/auth, pages network): who is this cookie?
    gateway -> bucket ontic-pages (its own private B2 bucket), read and write key, no delete
    laptop  -> bucket: file bytes, PUT to presigned URLs the gateway signs (never through Caddy)

| File | What it is |
|---|---|
| `pages.caddy` | the Caddy site, installed as `/etc/caddy/conf.d/pages.caddy` |
| `pages-gateway.container` | the gateway as a Podman Quadlet unit (`pages-gateway.service`) |
| `pages-oauth2-proxy.container` | Google sign-in in front of it (`pages-oauth2-proxy.service`) |
| `pages.network` | the bridge the two containers share |
| `Dockerfile`, `Dockerfile.dockerignore` | the image: this repo in a venv, uid 10003 |
| `oauth2-proxy.cfg.example` | the oauth2-proxy config; `pages.sh` fills in the secrets |
| `secrets.env.example` | the one file you fill in (copy to `secrets.env`, never commit it) |
| `pages.sh` | on the box, as root: `check`, `install`, `status` |
| `deploy.sh` | on the laptop: the whole deploy in one command |

## Deploy

```sh
cp deploy/secrets.env.example deploy/secrets.env && chmod 600 deploy/secrets.env
$EDITOR deploy/secrets.env
deploy/deploy.sh ontic-vps check     # prepares checkout/secrets; validates without restarting
deploy/deploy.sh ontic-vps           # check, then install
deploy/deploy.sh ontic-vps status    # or: logs
```

`deploy.sh` refuses an unpushed branch, clones or fast-forwards
`/opt/ontic-pages` on the box, copies `secrets.env` over ssh (mode 600, never
printed), runs `pages.sh check` and `pages.sh install`, then checks
`http://127.0.0.1:8790/_health` on the box. `install` writes
`/etc/ontic-pages/gateway.env` and `/etc/ontic-pages/oauth2-proxy.cfg`, builds
`localhost/ontic-pages:latest`, installs the three units, starts them, installs
the Caddy site, validates and reloads Caddy. It never edits
`/etc/caddy/Caddyfile` (`check` refuses until it has the global `on_demand_tls`
option, see One-time setup) and never restarts Caddy. The box takes a password, so
set `ATLAS_SSH_ASKPASS` to the askpass program for the run, as for the old
deploy.

## One-time setup

1. **Deploy key.** The box needs read access to `onticlabs/ontic-pages`, behind the ssh alias
   `github-ontic-pages` (the box has one deploy key per repository):

   ```sh
   vps 'ssh-keygen -t ed25519 -N "" -C ontic-vps-pages-deploy -f /root/.ssh/ontic_pages_deploy && printf "\nHost github-ontic-pages\n  HostName github.com\n  User git\n  IdentityFile /root/.ssh/ontic_pages_deploy\n  IdentitiesOnly yes\n" >> /root/.ssh/config && cat /root/.ssh/ontic_pages_deploy.pub'
   gh repo deploy-key add <(echo '<the ssh-ed25519 line>') --repo onticlabs/ontic-pages --title "ontic-vps pages"
   ```

2. **Bucket key: read and write, no delete.** The gateway is the only holder of a key: it
   writes each version's `page.json`, `<name>/current` and `<name>/visibility`, signs the
   presigned PUT URLs the command line uploads files with (and the GET URLs of `pull`), and
   for a text edit saved in the bar copies the version's files inside the bucket and writes
   the edited HTML file. The `ontic-pages-publish` key has exactly the right capabilities
   (`listBuckets,listFiles,readFiles,writeFiles`, scoped to the bucket); reuse it, or
   make one for the gateway alone:

   ```sh
   b2 key create --bucket ontic-pages ontic-pages-gateway-rw listBuckets,listFiles,readFiles,writeFiles
   ```

   Its two values go into `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` of `secrets.env`.
   The read-only `ontic-pages-gateway` key is no longer enough.

3. **Token secret.** `ONTIC_PAGES_TOKEN_SECRET` signs the command line's 30-day tokens
   (`ontic-pages login`). Generate it once with `openssl rand -base64 32` and put it in
   `secrets.env`; `pages.sh check` refuses an empty or short one and `install` writes it into
   `gateway.env`. Changing it signs everyone out.

4. **Google OAuth.** The redirect URI `https://pages.onticlabs.io/oauth2/callback`
   is already on the client the old deploy used; reuse its id, secret and cookie secret.

5. **DNS.** Two A records to 159.195.206.154: `pages` (exists) and the wildcard
   `*.pages` for the page hosts. Check that the wildcard still exists (it was slated
   for deletion after the path-based deploy) and add it back at Namecheap if not:
   `dig +short anything.pages.onticlabs.io` must print 159.195.206.154.

6. **Caddy global option.** Page hosts get their certificates on demand, and Caddy
   must ask the gateway first. That is a global option in `/etc/caddy/Caddyfile`
   (a site file cannot set it); `pages.sh check` refuses to go on without it. As root
   on the box (`vps`), this one line adds it to the global options block (or creates
   that block), validates, and puts the old file back if validation fails:

   ```sh
   f=/etc/caddy/Caddyfile; cp -a "$f" "$f.bak-pages" && awk 'BEGIN{a="\ton_demand_tls {\n\t\task http://127.0.0.1:8790/_tls-ask\n\t}"} !d && /^[ \t]*(#|$)/ {print; next} !d {d=1; if ($0 ~ /^[ \t]*\{[ \t]*$/) {print; print a; next} print "{\n" a "\n}\n"} {print}' "$f.bak-pages" > "$f" && caddy validate --config "$f" --adapter caddyfile || cp -a "$f.bak-pages" "$f"
   ```

   Run it only if `grep -n _tls-ask /etc/caddy/Caddyfile` finds nothing (a second
   `on_demand_tls` block would fail validation and be rolled back). Caddy picks it up
   with the `systemctl reload caddy` that `pages.sh install` does.

## Who can open what

Each page has a visibility in `<name>/visibility` in the bucket: `private`, `ontic`
(the default when the file is absent) or `public`. The gateway decides every request
itself, the shell and the page's files alike, and before every answer (304s too):

- **Who.** The gateway asks oauth2-proxy: `GET /oauth2/auth` with only the request's
  `Cookie` header. 202 with `X-Auth-Request-Email` is signed in; anything else is
  signed out; no answer is a 503 for anything that is not public. The answer is kept
  for 5 seconds per cookie. Identity headers in a request are never read (and Caddy
  strips them). `set_xauthrequest = true` in `oauth2-proxy.cfg.example` must stay.
- **The cookie reaches every page host** because oauth2-proxy sets it for
  `.pages.onticlabs.io` (`cookie_domains`), and `whitelist_domains` lets sign-in return
  to a page host.
- **Rules.** `public`: anyone, signed in or not. `ontic`: an `@onticlabs.io` email.
  `private`: the `published_by` of the current version. Signed out, a page load
  redirects to `/oauth2/start?rd=<the page>`; signed in but not allowed is 403. The
  listing at `/` and `/<name>/_info` always need sign-in.
- **Writes** (`POST /_api/...`: visibility, current version, publishing, comments) are taken on the
  apex only, with a JSON body, 20 per minute per person, and either from the apex's own pages
  (`Sec-Fetch-Site: same-origin`, `Origin: https://pages.onticlabs.io`, cookie) or with a
  command line token.
- **Tokens** (`Authorization: Bearer op1...`) come from `ontic-pages login`: the login page at
  `/_cli/login` (signed in, `@onticlabs.io` only) approves a code the terminal made, and the
  terminal's next poll gets the token once. Codes live 5 minutes in memory, single use, at
  most 100 at a time, and are hidden in the log. A request with a token and any `Origin` or
  `Sec-Fetch-Site` header is refused: browsers never hold tokens. Without
  `ONTIC_PAGES_TOKEN_SECRET` these routes answer 503.
- **Publishing.** The command line sends the file list (Caddy's 64 KB body cap stays: big
  lists go in several requests), gets one presigned PUT URL per file (valid 1 hour), uploads
  straight to B2, then asks the gateway to commit; the gateway checks every file's size in the
  bucket, then writes `page.json` with the token's email as `published_by`, the visibility and
  `current`. Anyone at the domain may create a page; only the owner publishes new versions. A page's own
  scripts run on `<name>.pages.onticlabs.io`, which is same-site, so the browser would
  send the viewer's cookie with their requests; these checks are what stop a page from
  acting as the person looking at it.
- **Only Caddy reaches the gateway**, on the box's loopback (`127.0.0.1:8790`). Caddy
  answers `/_tls-ask` with 404 on both public sites; the gateway answers it only for
  a Host that is neither the apex nor a page host.

## Direct delivery (default for new pages)

After this release, first publication of a new page name records `delivery: "direct"` in its
`labels.json`. Existing pages without that field remain server-delivered, and republishing,
editing, switching versions, deletion/restoration preserve the page's setting. No migration or
publisher action is required. Use the updated gateway for normal publishing; legacy clients
publishing with their own bucket key (`--direct`) must be upgraded to persist this setting.

The gateway permits direct delivery by default (`ONTIC_PAGES_DIRECT_FILES=1`). Before deploying,
configure bucket CORS. Eligible CORS-mode GETs for data/media are redirected after authorization;
HTML, CSS, scripts/workers, playlists, SVG/XML, glTF JSON, HEAD and browser no-cors/same-origin requests stay
on the gateway. The bucket remains private. New pages should use `crossorigin="anonymous"` for
images/video; `fetch()` already defaults to CORS. See the compatibility details in README.md.

### Admin rollout, in this order

1. **Save and merge the live CORS rules.** Use a B2 administrative application key with
   `writeBuckets` and bucket-inspection rights. The account master key is not required. Do not
   add these privileges to the gateway key. Authenticate the B2 CLI in the admin environment.
   `b2 bucket update --cors-rules` replaces the whole rule list, so save the actual current
   configuration and preserve every unrelated rule, including browser uploads:

   ```sh
   b2 bucket get ontic-pages > ontic-pages-bucket-before.json
   jq '.corsRules' ontic-pages-bucket-before.json > ontic-pages-cors-before.json
   jq '[{"corsRuleName": "page-files",
         "allowedOrigins": ["https://*.pages.onticlabs.io"],
         "allowedOperations": ["s3_get", "s3_head"],
         "allowedHeaders": ["range"],
         "exposeHeaders": ["content-length", "content-range", "etag"],
         "maxAgeSeconds": 3600}]
       + [.[] | select(.corsRuleName != "page-files")]' \
       ontic-pages-cors-before.json > ontic-pages-cors-proposed.json
   diff -u ontic-pages-cors-before.json ontic-pages-cors-proposed.json
   # Review the diff, then apply it:
   b2 bucket update --cors-rules "$(cat ontic-pages-cors-proposed.json)" ontic-pages
   b2 bucket get ontic-pages > ontic-pages-bucket-after.json
   jq '{bucketType, corsRules}' ontic-pages-bucket-after.json
   ```

   Confirm `bucketType` is still `allPrivate`, all unrelated rules survived, and the
   `overview-uploads` PUT rule is present. If it is absent, add that rule from the overview
   section below to the saved list before applying. B2 explicitly supports the subdomain
   wildcard above: [CORS rule syntax](https://www.backblaze.com/docs/cloud-storage-cross-origin-resource-sharing-rules).
   Adapt origins if serving a different content domain. CORS does not grant access to unsigned
   private-bucket URLs.

2. **Deploy the merged revision using root-capable SSH to `ontic-vps`.** The deployment script
   deploys the local branch, so explicitly select and update `main` after this PR is merged:

   ```sh
   git switch main
   git pull --ff-only origin main
   git rev-parse HEAD                    # record the intended revision
   # Retain the complete existing deploy/secrets.env, mode 600.
   # Set ONTIC_PAGES_DIRECT_FILES=1, or omit it for the default.
   deploy/deploy.sh ontic-vps
   deploy/deploy.sh ontic-vps status
   ```

   `deploy.sh` copies the complete secrets file, builds the image and restarts the services.
   The gateway's existing `readFiles` key signs downloads; no additional runtime privilege,
   DNS record or OAuth change is required. The existing root deployment handles the environment
   file, container rebuild and Caddy validation/reload. Verify the running image revision
   (inspect the container image ID, then its `org.opencontainers.image.revision` label) and inspect
   only `ONTIC_PAGES_DIRECT_FILES` in `/etc/ontic-pages/gateway.env`, not other secret values.
   An absent value means enabled. The `check` subcommand also updates the remote checkout and
   copies secrets, although it does not install/restart services.

3. **Verify actual B2/browser behavior before declaring the rollout complete.** Use a new test
   page with JSON, CSS dependencies, modules, a worker, canvas images, a GLB and a seekable video:

   - A pre-existing page remains server-delivered, including after a new version is published.
   - The new page's eligible files show `302` at the gateway followed by `200`/`206` at B2;
     HTML/CSS/scripts, HEAD and non-CORS images remain on the gateway. The redirect has
     `Cache-Control: private, no-store`. Confirm bucket Content-Type and CORS headers.
   - Video seeks return the expected `206` and `Content-Range`; test a Range preflight too.
     Inspect `Origin` and `Access-Control-Allow-Origin` for the actual page subdomain.
   - Nested modules, CSS images/fonts and workers load; CORS-enabled images work in canvas/WebGL.
     Publish another version with changed JSON/media and verify a populated browser cache sees it.
   - Test public, team and private access in separate sessions. A non-owner cannot open a private
     page; a signed-out request cannot obtain fresh links to protected files.
   - After a link is over one hour old, it fails for a new bucket request. Reloading the page
     while authorized obtains fresh links. A tab holding an expired URL may need a reload before
     a later seek/download. Test seeking after that reload.
   - Confirm existing overview composer uploads still succeed. Check service health and gateway
     logs; the automated deployment health check alone does not exercise direct delivery.

### Access lifetime and rollback

The accepted link lifetime is one hour, with reuse for 50 minutes. An issued URL is a bearer
credential for that one file: it remains valid after logout, visibility changes, deletion, a
per-page mode change, or disabling direct delivery. Browser-cached/downloaded bytes cannot be
revoked. New requests to original gateway URLs still check authorization and page state.

For a global rollback, set **`ONTIC_PAGES_DIRECT_FILES=0`** in the complete existing secrets file
and redeploy. Clearing or omitting the setting enables the default and is NOT rollback. Stored
page settings remain intact, so a later re-enable restores direct delivery only for direct pages.
For an individual incompatible page, its owner can use `ontic-pages delivery <name> server`;
this is a recovery control, not a required publishing step.

With this revision's uncacheable redirects, subsequent gateway requests use the server after
rollback. If the original PR #15 was previously deployed, browsers may retain its cached
redirects for up to 59 minutes; validate with a fresh browser context as well. Already-issued
bucket URLs work until expiry in either case. The CORS rule can remain. If restoring CORS is
necessary, first check for intervening admin changes and merge them rather than overwriting them
with a stale backup. Do not make the bucket public or rotate the runtime key for routine rollback.

## What changed with the research overview

- **One-time: a CORS rule on the bucket**, so the overview's composer can PUT images and videos
  straight to B2 with the presigned URLs (bytes still never pass the gateway or Caddy). Run it
  once with an administrative B2 key with `writeBuckets`, not the gateway's:

  Preserve all current rules using the read/merge/review procedure in "Direct delivery" above.
  The upload rule to include in that list is:

  ```json
  {"corsRuleName": "overview-uploads",
   "allowedOrigins": ["https://pages.onticlabs.io"], "allowedOperations": ["s3_put"],
   "allowedHeaders": ["content-type"], "exposeHeaders": ["etag"], "maxAgeSeconds": 3600}
  ```

  Without it everything works but uploads from the browser: the composer says the bucket
  refused the browser (CORS). Publishing from the command line never needs it.
- **Three more files**: `<name>/labels.json` per page (labels changed in the table, and the
  deleted flag; a publish writes it too), `_milestones.json` and `_views.json` at the bucket root,
  written with the same key (nothing deleted; B2 keeps the overwritten copies). Page names
  cannot start with `_`, so they never collide with a page.
- **`/` is the overview** (its own script, `static/overview.js`, under a policy that allows
  `connect-src` to the bucket's upload host and images from the page hosts). Without script it
  falls back to the plain list. Milestones and views are for `@onticlabs.io` accounts only.
- **No new secret, no Caddy change.** Markdown travels in one request (at most 48 KB of text,
  under the 64 KB cap); the gateway renders it and writes `index.html` and `update.md` itself.

## What changed with comments (phase 3)

- **The gateway writes one more file per page**, `<name>/comments.json`, with the same key (no
  new capability, nothing deleted; B2 keeps the overwritten copies). No new secret, no Caddy
  change: comment bodies are small JSON under the 64 KB cap.
- **Who.** Reading and writing comments needs sign-in and access to the page; signed-out
  viewers of a public page get none. The same write checks and the same 20 writes a minute
  apply. Reads from a page's own scripts (`Sec-Fetch-Site: same-site`) are refused.
- **Old comments** from the SQLite database of the old deploy are not migrated.

## What changed with publishing through the gateway (phase 2)

- **New secret** `ONTIC_PAGES_TOKEN_SECRET` in `secrets.env` (see One-time setup, step 3).
- **People need no key.** `ontic-pages login` once, then the usual commands. Admins keep
  `--direct` with their own key.
- **No Caddy change.** The body cap stays 64 KB; uploads go to B2 directly. B2 takes presigned
  PUTs (SigV4, the configured region), checked end to end before the first deploy.

## What changed with the bar (phase 1)

- **A page host again.** Content is served from `<name>.pages.onticlabs.io`, its own
  browser origin, so a page's scripts cannot read the bar or another page. The apex
  shows the bar around a frame of it. Opening a page host directly redirects to the
  bar; `?raw=1` opens it without the bar.
- **On-demand certificates and `/_tls-ask` are back** (Caddy global option, wildcard
  DNS record, see One-time setup).
- **oauth2-proxy no longer proxies.** It serves `/oauth2/*` only (`upstreams =
  static://404`, `set_xauthrequest = true`), sets its cookie for `.pages.onticlabs.io`
  and has the network alias `oauth2-proxy`. The gateway is started with
  `--auth-url http://oauth2-proxy:4180/oauth2/auth`.
- **The gateway key can write** (no delete), for the Share panel.
- **Memory cache.** Up to 256 MB of version files (each up to 4 MB) in memory, with
  ETags; versions are immutable, so a version's own address is cached for a year.
- **Old links.** `/public/<name>/...` redirects to `/<name>/...`, which now works
  without sign-in for public pages.

## What changed from the old `ontic pages` deploy

- **Same names.** Units, image name, ports (8790, 4190), network
  (`ontic-pages`, 172.31.241.0/24), uid 10003 and `/etc/ontic-pages` are the
  same, so `install` replaces the old deploy in place. Checkout moves from
  `/opt/ontic-cli` to `/opt/ontic-pages`.
- **Pages by name**, `https://pages.onticlabs.io/<name>/` and
  `https://<name>.pages.onticlabs.io/`, not by job id. Old `<job-id>` links stop working.
- **No disk cache, no comments database, no broker identity, no announce index.**
  Visibility is per page, and private means "only the publisher", with no share lists.
- **Merged scripts.** `install-secrets.sh` is folded into `pages.sh`.
  `secrets.env` has fewer keys: no B2_READ_*, no mirror, no broker token; the
  bucket key is in `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`.

Cleanup after the first deploy, by hand, when you are happy with it:

- Back up the old comments: `sqlite3 /var/cache/ontic-pages/comments.sqlite3 ".backup /root/pages-comments-$(date +%F).sqlite3"`,
  then remove `/var/cache/ontic-pages` (the new gateway does not mount it).
- Delete the old `jobs/` read key. Keep the `*.pages` DNS record.
- `/opt/ontic-cli` is no longer used by pages.

## Logs

`journalctl -u pages-gateway -f` (one line per request: time, email, request,
status) and `journalctl -u pages-oauth2-proxy -f`.
