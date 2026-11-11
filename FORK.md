# This fork

Personal fork of [heygen-com/hyperframes](https://github.com/heygen-com/hyperframes), Apache-2.0.

- `custom` is the default branch and holds all changes made in this fork.
- `main` stays an unmodified copy of upstream. Don't commit to it.

## The update check

`.github/workflows/fork-upstream-check.yml` runs daily at 06:17 UTC. If upstream has new commits, it opens or
updates one issue labelled `upstream-update`, with the number of commits, the latest release, the newest commit
subjects and whether a merge would conflict. When the fork catches up, it closes that issue. It never merges or
changes code.

Run it now: `gh workflow run fork-upstream-check.yml -R Ali-Alphaa/hyperframes`

GitHub pauses scheduled workflows after 60 days with no repository activity. If the issue stops updating, re-enable
the workflow on the Actions tab.

## Updating the fork

```bash
git switch custom
git fetch upstream
git merge upstream/main
git push origin custom
```

Then go to the Actions tab and disable any new workflows the merge brought in. This fork only needs its own check.

## Using the fork

Working on it needs [Bun](https://bun.sh): `bun install`, `bun run build`, `bun run dev`. Existing video projects
keep using the official `hyperframes` package from npm until they are pointed at this build.

## Desktop app

`tools/desktop/HyperFramesFork.ps1` opens this fork's Studio in its own window, and stops the Studio server when the
window closes. `tools/desktop/install-shortcut.ps1` puts a "HyperFrames Fork" shortcut on the Windows desktop. It needs
Chrome or Edge, and it runs the built files, so run `bun run build` after any code change or merge.

The window opens in your normal browser profile. A separate, fresh profile played the preview without sound on this PC.
With `-Pick` it shows the standard Windows Explorer Open window instead of the home screen (below): go into the
project folder and open its `index.html`. `install-shortcut.ps1 -Project <folder> -Name <name>` makes a shortcut that opens one project directly.

When its window closes, the launcher stops only the server it started. (`preview --stop` stops every Studio for the
project, so it used to kill the server behind another open window.) Only one Studio runs per project: opening the
project again replaces the earlier server. Shortcuts start the launcher through `conhost --headless`, because Windows
Terminal shows a window for it that kills the launcher when closed.

With no project, the launcher opens a HyperFrames home screen in the Studio window (`project-home.mjs` and
`project-home.html`): recent and found projects (from the `videos` folders), a search box and "Other folder…" for the
Explorer window. Picking one starts its Studio and the window moves on to it; Alt+Left comes back to the list, and
opening another project stops the previous server. `HyperFramesFork.ps1 -Pick` keeps the old Explorer-first start.
`install-shortcut.ps1 -Name HyperFrames` makes the shortcut.

`install-shortcut.ps1 -Features` adds a "HyperFrames Features" shortcut: a window with a dropdown of every feature
(HyperFrames, Studio Pack, add-ons, research), what each does and what to ask Claude, with a button that copies the
request. The list is `tools/desktop/features.json`; add a row there when a feature is added.

## Catalog thumbnails

HeyGen hosts preview images for only some catalog items, and Studio shows a plain category tile for the rest. This fork
renders the missing ones on your computer: run `node scripts/fork-catalog-thumbnails.mjs` after `bun run build`. It writes
PNGs to `docs/images/catalog/` (not committed), and Studio serves them through `/api/registry/local-poster/...` for
items with no hosted preview. Run it again after merging upstream, because new catalog items arrive without images.

## Shorts Generator

`tools/shorts-generator` cuts long videos into vertical clips. See its `README.md`.
