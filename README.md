# mwongj Plugin Forks

A Dispatcharr plugin distribution repository for mwongj's forks. Plugin source stays in its own repository; this repository builds pinned commits, hosts ZIP releases, and publishes the catalogue metadata.

Add this **manifest URL** under Dispatcharr's plugin repositories:

```text
https://raw.githubusercontent.com/mwongj/Dispatcharr-Plugins/releases/manifest.json
```

The GitHub repository page is not the JSON feed. Keep the official catalogue enabled for other plugins and select this repository when installing a fork.

## Plugins

| Plugin | Source | Current release |
| --- | --- | --- |
| VOD to Media Library | [mwongj/VOD2MLIB](https://github.com/mwongj/VOD2MLIB) | `1.20.2-rc.3` — test release for filter/Emby NFO-only cleanup (stable `1.20.1` retained) |

The current RC package is pinned to tested [PR #5](https://github.com/mwongj/VOD2MLIB/pull/5) source for filter/Emby ownership NFO-only archival, with Save-driven scheduling and the stable fork identity. Stable `1.20.1` remains available in version history. Its source SHA is recorded in the definition, feed, and release notes. The release version is written into the packaged manifest and Python class without changing the source fork's release metadata. Original copyright and license information are retained; package metadata identifies mwongj as this fork's maintainer.

VOD2MLIB keeps the identifier **`vod2mlib`** and displays **VOD to Media Library (mwongj fork)**. Install this repository's version over the existing plugin to switch its managed source while keeping the existing settings. It is one installation, so upstream and this fork cannot run as separate plugins. Scheduled tasks keep the existing identities.

## Publish or add a fork

1. Add or edit `plugins/<slug>.json`. Pin `source_commit` to a full 40-character SHA and list only the files needed by the plugin. Use a new version for changed package contents.
2. Commit the definition to `main`. The **Publish plugin forks** workflow tests the publisher, builds the ZIP, uploads a GitHub release, verifies its SHA256, then updates the `releases` branch.
3. Refresh the repository in Dispatcharr to see the published version. To republish after a failed run, use **Actions → Publish plugin forks → Run workflow**. An identical existing release is reused; changed bytes at the same version are rejected.

Future forks need their own definition and source repository; they do not need another distribution repository. Published versions are retained in each plugin's detail manifest. Definitions select the current advertised version explicitly; there is no automatic tracking of upstream branches.

Local build without publication:

```sh
python -m unittest discover -s tests -v
python scripts/publish.py --plugin vod2mlib --dry-run
```

ZIPs are written to `dist/`. Publishing locally uses `GH_TOKEN` / `GITHUB_TOKEN`, or an existing Git credential-manager login. GitHub Actions uses its repository token with `contents: write`; no cross-repository secret is needed for public source forks.

## Repository layout

- `main`: plugin definitions, registry configuration, publisher, workflow, and tests.
- `releases`: generated `manifest.json`, per-plugin `metadata/<slug>/manifest.json`, READMEs, and logos.
- GitHub releases: versioned plugin ZIPs with recorded SHA256 checksums.

This is an independent feed named **mwongj Plugin Forks**. Its manifests are unsigned; Dispatcharr reports them as unverified. SHA256 checks detect mismatched downloads but are not signatures.

Earlier VOD2MLIB releases remain available in version history. Refresh **mwongj Plugin Forks** and explicitly select `1.20.2-rc.3` to test PR #5. Dispatcharr may suppress ordinary update notifications for installations marked as prereleases; select a version explicitly when needed. The RC preserves settings and keeps the `vod2mlib` plugin identity. Generation applies current filters before batching, removes verified rejected STRMs, and archives confirmed rejected NFO-only folders regardless of NFO generation or ownership/source deletion scope. Enabled Emby ownership cleanup also archives passing-filter NFO-only duplicates, including series with episode NFOs but no tvshow.nfo; whole-show mode is required for series, and existing cleanup timing is respected. Preview selective cleanup shows proposals first; scan Emby after generation to clear cached empty items.
