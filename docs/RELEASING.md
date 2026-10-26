# Releasing

The public source is `filip-pilar/x-bookmarks`. Users install versioned built ZIPs; they do not build Swift or npm dependencies themselves.

## Prepare a version

1. Make and review the change. Update `docs/KNOWN_ISSUES.md` when an issue has actually been resolved and verified.
2. Run `python3 scripts/bump-version.py 0.5.2` with the intended new version. It keeps both plugin manifests, npm metadata and the MCP/UI version strings aligned.
3. Run the build/check commands in the root README. No import, OAuth or model inference is part of these checks. Review the exact diff and commit it with a Conventional Commit message.
4. Push the commit, then create and push its version tag:

```sh
git push origin main
git tag v0.5.2
git push origin v0.5.2
```

The release workflow checks that the tag matches the plugin version, builds the native arm64 helpers on macOS with Swift 6.2+, packages the plugin and checks the archive. It creates a **draft prerelease** containing:

- `bookmark-library-release.zip`
- `SHA256SUMS`
- `SETUP_PROMPT.md`
- `UPDATE_PROMPT.md`

The initial public baseline is v0.5.1. Its release notes must clearly identify the unresolved import/media/helper-trust issues. There are no Apple Developer signing credentials configured in this workflow; GitHub hosting does not supply notarization.

## Publish

Check the workflow result, generated release notes and assets in GitHub Releases. Keep Preview/prerelease status until the known runtime issues and recipient installation have been verified. Publish the draft when it is ready for users; use a normal release only after that validation. Do not overwrite an already published version or reuse its tag. Corrections get a new version.

GitHub sends release notifications according to each user's Watch settings. Users ask Codex/ChatGPT to follow `docs/UPDATE_PROMPT.md` to apply the update. Do not distribute personal Client IDs, config files, models or bookmark data with public releases.

## Verification boundaries

CI proves source/build/package consistency. It does not prove that Gatekeeper accepts the executable, that a fresh Mac can download models, that OAuth succeeds, or that X exposes a full historical inventory. Verify those separately before claiming a supported release.
