# X Bookmark Library

Import your own X bookmarks and search their text, images, videos and transcripts in the Codex/ChatGPT desktop app. Source processing and embeddings run on your Apple silicon Mac; selected evidence and optional AI summaries use your assistant conversation.

**Preview:** the current baseline has known import, media and macOS helper-trust issues. Read [known issues](docs/KNOWN_ISSUES.md) before importing. A completed setup does not verify full media processing or historical coverage.

## Install

Use an Apple silicon Mac with macOS 14+, Homebrew, and a local Codex/ChatGPT desktop chat with computer access and Browser. Each user brings their own X developer app, API credits and X account. No separate OpenAI API key is required.

Give your assistant this request, then let it follow the [setup prompt](docs/SETUP_PROMPT.md):

> Install Bookmark Library from https://github.com/filip-pilar/x-bookmarks. Read docs/SETUP_PROMPT.md and use Browser to handle setup. Use my own X app and billing. Finish connected and paused, without importing bookmarks.

The assistant downloads a built ZIP from [Releases](https://github.com/filip-pilar/x-bookmarks/releases), verifies its checksum, installs dependencies and helps with X authorization. You handle required sign-ins, financial decisions and macOS approvals. Using GPT-6 Astra with xhigh reasoning is the suggested handoff configuration; model availability depends on your account.

Download **bookmark-library-release.zip**, not GitHub's source-code ZIP. The source checkout omits built helpers and JavaScript bundles. Preview releases are labeled; the assistant reports those limitations before setup.

## Update an existing installation

> Update my existing Bookmark Library from https://github.com/filip-pilar/x-bookmarks. Follow docs/UPDATE_PROMPT.md. Keep my X app, authorization, bookmarks and queued work. Leave it paused and do not sync or import.

Updates use a versioned release ZIP and the existing local library. They do not require creating another X app or paying again. New X API reads still use your own app's credits. The prompt pauses the queue and stops the existing Bookmark Library service before replacing its cached plugin code.

Updates are applied when requested; publishing a release does not silently update anyone's Mac. On GitHub, **Watch → Custom → Releases** lets you receive new-release notifications. [GitHub notification controls](https://docs.github.com/en/account-and-profile/managing-subscriptions-and-notifications-on-github/setting-up-notifications/configuring-notifications)

## Data and credentials

Bookmarks, downloaded media, vectors, configuration and models stay outside Git in `~/Library/Application Support/BookmarkLibrary`; OAuth credentials are in macOS Keychain. This public repository and its default release contain no X Client ID or shared billing configuration. Each user's library remains independent.

Read the [plugin guide](plugin/README.md) for processing behavior, X callback/scopes and local backup instructions. For failures, use the [bug report template](https://github.com/filip-pilar/x-bookmarks/issues/new?template=bug_report.yml) and share only redacted diagnostic summaries.

## Develop and release

Build requirements: Apple silicon, macOS 14+, Node.js 22+, Python 3 and Swift 6.2+.

```sh
cd plugin
npm ci
npm run check
npm run build
npm run build:native
npm run package
cd ..
python3 scripts/check-release.py --archive release/bookmark-library-release.zip
```

CI checks TypeScript, bundles the UI/server, checks Python syntax and checks version consistency. A version tag starts an Apple silicon build and prepares a draft GitHub release with the installable ZIP, checksums and setup/update prompts. See [releasing](docs/RELEASING.md) for the maintainer workflow.

The source catalog is `.agents/plugins/marketplace.json`; the built ZIP contains its own installable marketplace. Generated builds, private files and local data are ignored by Git. No unit tests are used.

MIT licensed. Model and dependency terms are separate; see [third-party notices](plugin/THIRD_PARTY_NOTICES.md).
