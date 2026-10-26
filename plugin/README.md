# Bookmark Library

A local ChatGPT plugin for X bookmarks. The panel connects X and controls imports. The assistant searches the processed text, images, video moments and transcripts.

## Install on an Apple silicon Mac

Requirements: Apple silicon, macOS 14+, Homebrew, Node 22+, Python 3.14, a compatible Swift 6.2+ toolchain/SDK, and a local Codex/ChatGPT desktop chat with Browser and computer access. Use the public repository's copy-paste setup or update prompt.

The ZIP includes built JavaScript and native source, with no native executables, model weights or X configuration. Setup compiles helpers into the private library's `bin/`, installs pinned Python dependencies, downloads/verifies Gemma, and downloads/loads Parakeet during generated-media preflight. It verifies OCR, text/image/audio/video/joint embeddings and real speech transcription before reporting ready. A version-matching `preflight.json` is required to resume work. Setup holds the service lock and finishes paused; stop an existing service before updating.

Each user supplies their own X app's public Client ID through `python3.14 scripts/setup.py --client-id 'PUBLIC_CLIENT_ID'`. Omit `--client-id` on updates to retain configuration. No client secret or Apple Developer membership is needed. Register the extracted marketplace root and install `bookmark-library@bookmark-library-local` using supported Codex CLI/desktop plugin controls, then connect X and leave paused until an import is authorized.

The X app must be a Native App/public client with OAuth 2.0 PKCE, read access and callback `http://127.0.0.1:8765/oauth/x/callback`. Requested scopes: `tweet.read users.read bookmark.read offline.access`. X bills the app owner's developer account. Disconnect before an authorized app change; use separate libraries for different X accounts.

Import walks the pages returned by the official X API, saves progress, and queues local processing. Valid records in partial responses are retained and pagination continues, with coverage gaps reported. Folder names and memberships appear incrementally; incomplete scans retain prior memberships. Inventory has a separate worker so local processing failures cannot block a folder check. Exhausting API pages does not prove X exposes every historical bookmark.

Ask ChatGPT to search bookmarks, use a saved source in a task, mention an individual bookmark/folder where supported, or ask for a specific video moment. Search does not contact X. Source reads use automatic page sizes; The assistant follows the returned continuation offset when it needs more content. The optional schedule button prepares an editable assistant request; the user chooses the timing before a schedule is created.

## Processing

EmbeddingGemma 2 encodes actual images, audio, video windows and text. Eight-second video windows sample one frame per second and combine corresponding audio and transcript text when present, alongside separate representations. English speech uses the established Parakeet CoreML runner. macOS Vision adds OCR. Native X Articles preserve text and image galleries; X does not provide inline image placement. Source blocks, code, URLs and linked-page images are saved to Markdown. The assistant can add saved AI summaries, topic tags and named entities alongside the original evidence. Every annotation cites evidence IDs and tracks its source revision. Changed sources make annotations stale until regenerated.

Compatible duplicate sync requests resume the same job. Conflicting operations are rejected without replacing unfinished work, including failed explicit refresh jobs. Partial bookmark pages cannot complete an inventory. Terminal X client errors stop the job; rate limits retain a scheduled retry.

Pause takes effect after the current request or local step; resume reuses completed files and vectors. Retry targets failures, including cached context failures, and excludes sample omissions and source limitations. A full import queues sample items to fill their skipped context even when their post text is unchanged.

The first import reads all exposed bookmark pages and folders. Later `sync` calls read recent bookmarks in pages of 10 until an entire page was saved before this run. Folder memberships refresh if their last complete check is at least 24 hours old. Skipped folder checks preserve memberships and their check times; new bookmarks have unknown membership until checked. `sync(mode="full")` explicitly checks every exposed page (100 records per page) and all folders. This incremental approach does not detect edits, removals or reordered older entries beyond its stopping point; use a full check when needed. Unchanged items do not automatically refresh after 24 hours. Context and media can be reused across bookmarks. Source refresh is explicit and uses X credits.

`process_saved(mode="rebuild")` rebuilds from retained sources without fetching them again. `repair` fetches missing sources; `refresh` deliberately refetches the bookmarked post and its sources. Both can target one item. Context pages, inventory pages and queue progress are persisted; partial writes of vectors and audio are repaired locally. Downloads preserve a 2 GB disk reserve and pause the queue if space runs low. Video audio is read in windows instead of loading the full soundtrack into memory.

An inventory never deletes source files. A completed full scan marks absent bookmarks as `unseen`; search/read expose that membership state. Incomplete or malformed scans retain previous folder membership. Folder-only post IDs are hydrated or reported unavailable. This is not a source-deletion compliance feed; a post being deleted or access being revoked still requires appropriate removal handling. Thread capture is bounded to 300 returned author posts and 24 parent/quote lookups. Links directly present in captured context are extracted; external articles are not recursively crawled.

For a local backup, stop only the Bookmark Library service, then run `BOOKMARK_LIBRARY_HOME=/path/to/library python python/maintenance.py backup /new/backup/path`. The matching `restore` command verifies file checksums, restores content and folder data, keeps displaced files, and leaves processing paused. Keychain credentials are excluded. Restore to the same library location so stored absolute evidence paths remain valid. Disconnect removes the Keychain credential and pauses synchronization, retaining the library.

## Saved AI enrichment

The library skill guides the active assistant conversation through `enrichment_pending` → paginated `enrichment_evidence` → `save_enrichment`. The assistant generates the metadata; the save tool validates it and embeds it with the local model. No separate API key or nested Codex process is used. Background downloads alone do not generate summaries. Enrichment can continue in an explicitly authorized assistant automation.

Search combines original evidence and labeled summaries. Optional tag/entity/content-type filters supplement original X folder, author and media filters. Generated metadata never overwrites X folders. Read results include both originals and annotations; substantive answers should cite the originals. `include_enrichment=false` limits search to source evidence for comparison.

`sync_folders` checks original folders for existing bookmarks without importing additional IDs. Folder API calls use X credits. Settings shows the folder check time and provides a Check now action with confirmation. Enrichment waits for source jobs to finish; changed evidence or newly queued processing rejects a pending annotation save.

## Local data and privacy

Default: `~/Library/Application Support/BookmarkLibrary`. Tokens are in macOS Keychain. The service binds only 127.0.0.1:8765; RPC requires a private random capability. A closed panel does not stop the queue. Scheduled tasks require the Mac and local ChatGPT runtime to be available. Selected search evidence enters ChatGPT context; source extraction, transcription and embeddings are local; AI enrichment uses the active assistant conversation and its normal usage.

`status` reports request and primary-record counts, excluding expanded resources and billing adjustments. X billing is authoritative; local transcription and embedding have no model API fees. The import review links current [X pricing](https://docs.x.com/x-api/getting-started/pricing) and labels its per-1,000-post examples as examples, not a total estimate. Source details distinguish sample omissions, retryable failures, unavailable content and extraction limitations. Searchable means indexed content is usable, not that every source was captured. A local rehearsal exercised 3,000 synthetic items and 60,000 vectors. Real full-history coverage and fresh-second-Mac performance remain unverified.

The release uses the documented Codex compatibility manifest (`.codex-plugin/plugin.json` and `.mcp.json`) required by the installed client. The source tree also keeps the portable manifest.

## Development

`npm ci && npm run check && npm run build` checks and bundles the server and iframe. `npm run build:native` optionally verifies native compilation with Swift 6.2+; `npm run package` creates a ZIP containing native source only. Recipient setup builds the helpers locally. Python source can be checked with `python3 -m compileall -q python scripts vendor/transcribe/scripts`. No unit tests. `requirements.txt`, `package-lock.json` and the vendored `Package.resolved` pin dependencies. See THIRD_PARTY_NOTICES.md for model/runtime licenses.

Maintainers publish versioned ZIPs through the repository's [release workflow](https://github.com/filip-pilar/x-bookmarks/blob/main/docs/RELEASING.md). Updating source alone does not replace installed cached plugins.
