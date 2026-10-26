# X Bookmark Library

Search your X bookmarks, images, videos and transcripts from a local Codex/ChatGPT desktop chat. Processing and embeddings run on your Apple silicon Mac; selected evidence and optional summaries can enter your assistant conversation.

**Preview:** generated-media processing and controlled import recovery have passed locally. Fresh-recipient installation and X historical coverage remain separate verification steps. See [remaining limitations](docs/KNOWN_ISSUES.md).

## Copy and paste to install

Open a **local** desktop chat with Browser and file/command access. Select **GPT-6 Astra · xhigh** if available, then paste:

```text
Install Bookmark Library from https://github.com/filip-pilar/x-bookmarks. Read and follow docs/SETUP_PROMPT.md using @Browser and local computer tools. I will use my own X developer app, billing and X account.

Handle downloads, dependencies, local compilation, app configuration and recoverable problems yourself. Ask only for materially missing information or personal sign-in/2FA, legal acceptance, exact payment approval, administrator prompts or final X consent. Never ask for secrets in chat.

Use the newest published release at version 0.6.0 or later and verify its ZIP checksum. Compile the native helpers on my Mac and download Gemma and Parakeet locally. No Apple Developer membership or signing from the repository owner is needed. Require the generated-media OCR, text/image/audio/video/joint embedding and real speech preflight to pass before calling setup ready.

Finish with the plugin discovered, my X account connected and the library paused. Do not import bookmarks, check folders, fetch bookmark sources, process saved content or create schedules. Preserve any existing library and use docs/UPDATE_PROMPT.md if this is an update. Report what you verified and any blocker.
```

The [full setup prompt](docs/SETUP_PROMPT.md) is also included with each [release](https://github.com/filip-pilar/x-bookmarks/releases). Your assistant handles installation; you complete required personal approvals. Each user needs their own X app and credits. No separate OpenAI API key is required.

Native helpers are **source only** in the ZIP; JavaScript is prebuilt. Setup needs Apple silicon, macOS 14+, Homebrew and a compatible Swift 6.2+ toolchain/SDK. It builds OCR/Keychain and Parakeet helpers locally and downloads the models on the recipient's device. Older macOS versions may need a compatible toolchain from Swift.org. Model terms are separate from the MIT code license.

## Copy and paste to update

```text
Update my existing Bookmark Library from https://github.com/filip-pilar/x-bookmarks. Read and follow docs/UPDATE_PROMPT.md. Handle routine work yourself. Preserve my app/account, credentials, bookmarks and queued work. Pause and stop the verified service before changes, build helpers locally, and pass the new version's generated-media processing preflight. Finish paused. Do not import/sync, check folders, fetch bookmark sources, retry/process saved content, create schedules or buy credits.
```

Publishing a release makes updates available; it does not silently change anyone's Mac. GitHub **Watch → Custom → Releases** provides release notifications. Updates retain the existing X app, billing and account connection.

## Data and diagnosis

The private library, models, helpers and caches live under `~/Library/Application Support/BookmarkLibrary`; OAuth credentials live in macOS Keychain. Public releases contain no Client ID, credentials, library data, model weights or native executables.

See the [plugin guide](plugin/README.md) for processing and backups. For problems, use the [bug report](https://github.com/filip-pilar/x-bookmarks/issues/new?template=bug_report.yml) with a redacted summary. Preserve saved queue/page diagnostics before retrying paid reads. Reaching the last API page does not independently prove every historical bookmark is available.

## Development

```sh
cd plugin
npm ci
npm run check
npm run build
npm run build:native  # Optional local build verification; executables are not packaged
npm run package
cd ..
python3 -m compileall -q plugin/python plugin/scripts plugin/vendor/transcribe/scripts scripts
python3 scripts/check-release.py --archive release/bookmark-library-release.zip
```

CI checks source/build consistency, compiles native source on Apple silicon, and validates the ZIP excludes native executables and private data. Tags prepare draft releases with the ZIP, checksums and copy-paste prompts. See [releasing](docs/RELEASING.md). No unit tests are used.

[MIT license](LICENSE). [Third-party notices](plugin/THIRD_PARTY_NOTICES.md).
