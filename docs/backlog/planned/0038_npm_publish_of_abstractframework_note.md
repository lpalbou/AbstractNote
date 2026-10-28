# npm publish of `@abstractframework/note` (0.2.0 rejected E404; 0.1.1 never published)

## Summary
The v0.2.0 release (run 40628871300, 2026-10-28) published `abstractnote` 0.2.0 to PyPI but the job
"Publish @abstractframework/note to npm" failed at `npm publish --access public --provenance` with
`E404 Not Found - PUT https://registry.npmjs.org/@abstractframework%2fnote ... could not be found or you do
not have permission`, so the "Create GitHub Release" job was skipped. npm holds only `0.0.0-stage` and `0.1.0`;
0.1.1 (CHANGELOG 2026-10-19) was never tagged or published.

## Why
Root release wave 1055 lists it as open ("`@abstractframework/note` 0.2.0 npm publish failed"); the gateway's
app catalog and `npx @abstractframework/note` serve 0.1.0 until it is fixed.

## Current understanding
- An E404 on PUT with OIDC is npm's answer when the package has no trusted publisher matching the workflow
  (the token exchange yields no publish right). The workflow is `.github/workflows/release.yml`, job environment
  `npm`, `id-token: write`, npm >= 11.5.1 checked, provenance on. 0.1.0 (run 39197496857) was published by
  the `lpalbou` npm user with no provenance attestation, so the trusted-publisher link was never exercised.
- Same failure class and fix as root 0952 (monitor-memory npm trusted publisher, done 2026-09-28).

## Chosen route (operator, on npmjs.com; then one rerun)
1. npmjs.com -> `@abstractframework/note` -> Settings -> Trusted Publisher -> GitHub Actions: owner `lpalbou`,
   repository `AbstractNote`, workflow filename `release.yml`, environment `npm`. Save.
2. Optional rehearsal: `gh workflow run release.yml -R lpalbou/AbstractNote -f dry_run=true` (exercises the
   token exchange with `npm publish --dry-run`).
3. `gh run rerun 40628871300 --failed -R lpalbou/AbstractNote` (reruns the npm job and the skipped GitHub
   Release job; PyPI 0.2.0 is already up and is not rerun).
4. Verify: `npm view @abstractframework/note@0.2.0 dist.attestations` shows a provenance attestation; the
   GitHub release v0.2.0 exists.

## Open decision
- 0.1.1: skip it on npm (recommended: 0.2.0 supersedes it; publishing an older version afterwards would only
  add noise) or publish it from its commit for completeness.

## Validation
`npm view @abstractframework/note version` = 0.2.0 with provenance; release run green.

## Metadata
- Created: 2026-10-28
- Status: planned
- Area: release
- Root link: AbstractFramework 1055 (release wave 2026-10-27)
