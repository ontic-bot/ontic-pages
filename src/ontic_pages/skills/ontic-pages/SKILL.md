---
name: ontic-pages
description: Make, publish and share an ontic page (a hosted HTML page for the team). Use whenever the user asks for an ontic page, a page to publish or share with the team, or a report, gallery, dashboard or viewer to host. Pages are published with `ontic pages publish`, never to claude.ai. Also for the team's research milestones: listing, adding, closing them, and linking reports to them.
---

# Ontic pages

An ontic page is plain HTML served at `https://pages.onticlabs.io/<name>/` under a slim bar
(title menu with versions and info, comments, Share), behind the team Google sign-in unless it
is public.
The tool is `ontic pages` (onticlabs/ontic-pages, installed with the ontic CLI; `ontic-pages` is
the same command). It is independent of the ontic CLI's store: no provenance integration, only metadata you pass with `--meta`.
Every page shows up in the research overview at `https://pages.onticlabs.io/`, under its pillar
and, when it has one, its milestone.
When the user asks for a page, report, gallery or dashboard, make an ontic page, not a claude.ai
artifact. Never publish to claude.ai unless the user asks for that explicitly.

## Setup

Do not install or reinstall the tool yourself. If `ontic pages` is not found, ask the user to
install it:

```bash
uv tool install --force git+https://github.com/onticlabs/cli
```

If a command says "not signed in, or the sign-in expired", ask the user to run
`ontic pages login` (it opens the browser and needs their click); do not look for keys or tokens
elsewhere. `ontic pages whoami` shows who is signed in.

This skill is installed by the ontic CLI and refreshed on every `ontic` run;
`ontic pages skill` shows where it is installed and whether it is current.

## Write the page like an artifact

If the `artifact-design` skill is available (Claude Code), load it before writing (and
`artifact-diagramming` when the page needs diagrams) and follow it as you would for an artifact.
Codex has neither; then follow these plain rules:

- Light and dark: colors as CSS variables on `:root`, redefined under
  `@media (prefers-color-scheme: dark)`; give `body` an explicit background.
- Works at phone width: a `<meta name="viewport">` tag, 16px side gutters, no horizontal page
  scroll.
- A short `<title>` that names the page; put the explanation in `--description`.

Either way, these differ from a claude.ai artifact:

- Write a complete HTML document (`<!doctype html>`, `<head>`, `<body>`); nothing wraps it.
- Relative supporting files next to `index.html` (CSS, scripts, JSON, images, video) are fine,
  because the whole folder is served from the page's own host.
- Links and sources must be relative (`assets/a.png`), never root-absolute (`/assets/a.png`).
- Any CDN works. Pages are static: no server, no database. Data goes in the page or in
  relative JSON files.
- Do not put a `page.json`, `_info` or top-level `_v` folder in the page folder; they are reserved.

## Publish

1. Write the page into its own folder: `index.html` plus any assets (or one `.html` file, which
   becomes `index.html`).
2. Pick a name: lowercase letters, digits, `.` and `-`, starting with a letter or digit, short
   (it is the URL and a host name, so no `_`). Publishing again under the same name adds a
   version and makes it current; only the page's owner (its publisher) may do that.
3. File it. Every page goes into the team's research overview (a table of all reports by
   pillar, milestone and tag), so every publish of a new page carries all of these; do not
   skip them:

   - **Milestone.** Milestones are what the team works toward: a capability to demonstrate or
     a question to resolve. Read the open ones with `ontic pages milestones --json`. If the
     report is evidence for one, pass `--milestone <id>` (its pillar comes along). If none
     clearly fits, leave it out: the report is exploratory, which is fine. Never create a
     milestone just to file a report.
   - **Pillar.** Without a milestone, always pass `--pillar`: `data`, `scene-reconstruction`,
     `world-models`, `robot-learning`, or `other` for anything else. In an experiments repo
     folder `projects/<pillar>/...` it is that pillar.
   - **Tags.** Always 1 to 3 `--tag`: what the report is about (the method, dataset, robot or
     metric), lowercase with `-` (letters, digits, `.`, `_`, `-`). Reuse the team's tags first:
     `ontic pages list` shows them; write `eval`, not a new `evaluation`.
   - **Title.** Give the HTML a meaningful `<title>` (in Markdown, the first `# heading`): it is
     the report's name in the overview.
4. Publish, from the repo the page came from (git remote, branch and commit are recorded):

   ```bash
   ontic pages publish ./report --name depth-eval --milestone predict-particle-dynamics --tag depth --tag ablation --description "Depth ablation, same budget"
   ontic pages publish ./report --name splat-scale --pillar scene-reconstruction --tag splats --tag probe
   ontic pages publish notes.md --name week-41-dynamics --milestone predict-particle-dynamics --tag rollout   # Markdown (math as $x^2$), rendered, with the images and videos it links
   ontic pages publish ./report --name depth-eval --pillar data --tag eval --meta model=sha256:9f1c... --meta dataset=point-clouds-arctic
   ```

   Publishing again without them keeps the page's pillar, tags and milestone; `--tag` replaces
   the tags, `--no-tags` clears them, `--no-milestone` makes it exploratory again. When a new
   version changes what the report is about, update them too.

   `--meta key=value` (repeatable) records what the page was made from. It is metadata only.
   If the results come from an experiment tracked by the ontic CLI, always record its job id
   (`--meta job=<job-id>`, as `ontic launch` or `ontic status` print it), plus the job ids or
   registry names of the checkpoints and datasets it shows, so others can trace the page back.
5. Give the user the printed link, and say where you filed it (milestone or exploratory, pillar,
   tags), so a wrong guess is quick to fix.

To change a published page's pillar, tags, milestone or title without publishing again (no new
version): `ontic pages label <name> --pillar data --tag eval --milestone <id>`. The team edits
the same labels in the overview's table. Delete a page only when the user asks:
`ontic pages delete <name>` hides it everywhere (files kept), `ontic pages restore <name>` brings
it back.

## Milestones

When the user asks to add, update or close a milestone (or a result clearly resolves one and
the user agrees), use the command line; anyone on the team may, the gateway records who did:

```bash
ontic pages milestone add "Predict 1 s of particle dynamics from RGB" --pillar world-models --criteria "rollouts under 5 mm for 1 s on held-out scenes" --next "train on merged data"
ontic pages milestone add "Does depth input help dynamics?" --kind question --pillar world-models --criteria "a controlled run with and without depth"
ontic pages milestone update <id> --next "the new next step"
ontic pages milestone update <id> --status achieved --result "what was shown" --evidence <page>
ontic pages milestone update <id> --status answered --negative --result "no gain: 0.2 mm, within noise" --evidence <page>
```

The milestones are the GitHub milestones of `onticlabs/experiments`, kept in step both ways:
what you add or change here shows on GitHub within a minute, and the other way round. They
are deleted on GitHub only, never here.

A question is resolved by a clear answer, and a useful negative finding counts (`--negative`).
Closing always needs `--result`: one or two sentences of what was learned, with numbers from
the actual runs. Never close a milestone the user has not asked to close.

## Visibility

A new page is `ontic`: anyone signed in with an `@onticlabs.io` Google account can open it.
Change it only when the user asks:

```bash
ontic pages publish ./report --name depth-eval --visibility private   # only the publisher
ontic pages share depth-eval public      # anyone with the link, no sign-in
ontic pages share depth-eval ontic       # back to the team
```

Visibility belongs to the page, not to a version. The link is the same at every level.
The owner can also change it from the Share button in the bar.

## Versions

```bash
ontic pages list                        # every page you can open: visibility, pillar, date, tags
ontic pages list --pillar world-models --tag eval   # only those pages
ontic pages list --name depth-eval      # its versions, newest first, * marks the current one
ontic pages info depth-eval             # page.json plus the versions (also Details in the bar)
ontic pages set-current depth-eval 20261007T153000Z   # serve an older version again
```

Nothing is ever deleted. To change a page published earlier, fix the files and publish again
under the same name; open viewers see the new version without reloading. In a new session, find
the name with `ontic pages list`; never guess it from a file name.

The owner can also fix text directly on the page in the browser (there is no Edit button: the
plain texts are editable for them, and Save in the bar writes a new version), so your last
published files may not be the newest. Only text written in the HTML file itself can be saved
that way, not text a script puts on the page. Before publishing a page again, run
`ontic pages info <name>`: if the current version's meta has `edited_from`, download it with
`ontic pages pull <name> /tmp/<name>-current`, compare its HTML with your source
(`diff -r`), carry those text changes into the source (or the code that generates it), then
publish.

## Comments

Signed-in viewers attach comments to elements of a page (a paragraph, a heading, a figure),
reply and resolve them in the bar. They are feedback for you. Before publishing a new version of
an existing page:

1. Read the open threads: `ontic pages comments <name>` (`--all` adds resolved ones, `--json`
   gives everything as JSON). Each thread shows a short id, where on the page it points (path,
   the element's tag and its quoted text) and every comment with its author.
2. Address each open comment in the page.
3. Publish the new version.
4. Reply to every thread you handled, saying what you changed (or why you did not), and resolve
   the ones you fixed:

   ```bash
   ontic pages comment depth-eval 3f9a1c "Fixed: the table now uses meters"
   ontic pages resolve depth-eval 3f9a1c
   ontic pages resolve depth-eval 3f9a1c --reopen   # if it was resolved by mistake
   ```

   A thread id may be shortened to its first characters while it stays unique. A long reply can
   come from stdin: `ontic pages comment <name> <id> - < reply.txt`. Leave threads you did not
   address open, and tell the user about them.

`--direct` (or `ONTIC_PAGES_DIRECT=1`) talks to the bucket with a key instead of the gateway;
only for admins who hold one. Do not use it unless the user asks.
