# Releasing AbstractNote

One version number covers both packages: `abstractnote` on PyPI and the web app
`@abstractframework/note` on npm. Both are published by
[`.github/workflows/release.yml`](../.github/workflows/release.yml) through trusted publishing
(no tokens): PyPI from the `pypi` environment, npm from the `npm` environment. Every release needs
the owner's explicit go.

## What the workflow checks

1. **validate**: the version (the tag, or the `version` input) equals `pyproject.toml` and
   `web/package.json`, and `CHANGELOG.md` has a non-empty `## [<version>] - <date>` entry. Then
   `pip install -e ".[dev,gateway]"` and `pytest -q tests` (the gateway tests included, nothing
   skipped), and the web app's `npm ci`, `npm test`, `npm run build`, `npm pack`.
2. **build-python**: wheel and sdist, `twine check`, and the built wheel installed in a clean
   environment (`abstractnote --help`, the version).
3. **ensure-tag**: a dispatch creates the tag `v<version>` on the dispatched commit (or reuses it
   when it already points there); a tag push uses its own tag.
4. **publish-npm** and **publish-pypi**: npm skips a version that is already published; PyPI skips
   files that already exist (`skip-existing`), so a re-run after a partial failure finishes the job.
5. **github-release**: only when both publish jobs succeeded.

## Before a release

Bump the version in `pyproject.toml`, `src/abstractnote/__init__.py`, `web/package.json` (and
`web/package-lock.json`), and `MARKETING_VERSION` / `CURRENT_PROJECT_VERSION` in
`watch/apple/project.yml`; add the `CHANGELOG.md` entry; regenerate `llms-full.txt` with
`python scripts/build_llms_full.py`; run `pytest -q tests` and, in `web/`, `npm test` and
`npm run build`. Push `main`.

## Rehearse (dry run)

A dispatch with `dry_run` runs every check and build, installs the built wheel, and runs
`npm publish --dry-run --provenance` inside the `npm` environment. npm performs the
trusted-publisher token exchange even on a dry run; the job fails unless the exchange succeeded,
so a passing rehearsal proves that npm accepts this repository, workflow and environment. Nothing
is tagged, uploaded or released.

```sh
gh workflow run release.yml --repo lpalbou/AbstractNote --ref main -f version=0.1.1 -f dry_run=true
gh run watch --repo lpalbou/AbstractNote "$(gh run list --repo lpalbou/AbstractNote --workflow release.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

If the npm job fails with "The npm trusted-publisher token exchange did not succeed", open the
package settings on npmjs.com (`@abstractframework/note`, Trusted publishing) and check that the
publisher names the GitHub repository `lpalbou/AbstractNote`, the workflow `release.yml` and the
environment `npm`.

## Release

Tag the commit on `main` and push the tag:

```sh
git tag -a v0.1.1 -m "AbstractNote v0.1.1"
git push origin v0.1.1
```

or dispatch without `dry_run` (`gh workflow run release.yml --ref main -f version=0.1.1`), which
creates the tag itself. Afterwards check
[pypi.org/project/abstractnote](https://pypi.org/project/abstractnote/),
[npmjs.com/package/@abstractframework/note](https://www.npmjs.com/package/@abstractframework/note)
and the GitHub release, then install from a clean environment:
`pip install "abstractnote[gateway]==0.1.1"`.

The Apple Watch app is not published by this workflow.
