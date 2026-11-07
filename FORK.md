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

The official HyperFrames desktop app isn't in this repository, so this is not that app. It has no Framey chat or Pro
features, and Studio's "Install the desktop app" button leads to the official one.

## Shorts Generator

`tools/shorts-generator` cuts long videos into vertical clips. See its `README.md`.
