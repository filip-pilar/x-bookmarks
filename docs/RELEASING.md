# Releasing

Users install a versioned ZIP with prebuilt JavaScript and **native source only**. Their setup builds the helpers and downloads Gemma/Parakeet locally. Do not distribute maintainer-built native executables or model weights.

1. Review changes and keep `docs/KNOWN_ISSUES.md` current. Run `python3 scripts/bump-version.py MAJOR.MINOR.PATCH` with the next version.
2. Run the README's TypeScript/build, Python syntax and archive checks. Native build validation requires Apple silicon and Swift 6.2+. Local runtime verification must use an isolated library and generated media; do not run imports as a release check.
3. Commit and push the reviewed change. Tag the exact commit as `vMAJOR.MINOR.PATCH`, then push the new tag. Never move published tags or overwrite published assets.

The macOS release workflow checks tag/version consistency, compiles native source for validation, builds JavaScript, and packages only the native source. Archive validation rejects `bin/`, models, private configuration, caches and library files. It creates a draft prerelease with `bookmark-library-release.zip`, `SHA256SUMS`, `SETUP_PROMPT.md` and `UPDATE_PROMPT.md`.

Inspect workflow results, assets and release notes before publishing the draft. Keep preview status until fresh-recipient installation and the remaining runtime boundaries have been verified. The copy-paste prompts require v0.7.1 or later; earlier releases do not implement this setup contract.

CI proves build/package consistency. Generated-media preflight proves the tested local processing paths on that device. Neither proves recipient OAuth, arbitrary real media or X historical completeness. State these boundaries in release notes. GitHub hosting does not provide signing/notarization, and this source-only distribution does not require them from the maintainer.

Publishing makes an update available, without modifying installed copies. Users follow `docs/UPDATE_PROMPT.md` to pause/stop the service, preserve their library/account, build locally and verify processing before using the new version. Do not retry paid work as part of an update.

For 0.7, record a completed subscription image/frame check and interruption recovery separately from local OCR/Parakeet/Gemma preflight. Validate the saved-evidence UI and migrations on an isolated copy. Include KNOWN_ISSUES.md and ARCHITECTURE.md in the release. Never package private ChatGPT registration/token files or diagnostic/library artifacts.
