# Bookmark Library 0.7 architecture

The implementation keeps a local, durable record for recall and reading. Media is temporary; its embeddings do not replace transcripts, OCR or readable descriptions. The default interface is search and a browsable list, with details revealed when needed.

## Capture scope

Capture the bookmark, its direct reply parent and its directly quoted post, including each post's text, X Article and media. Do not recurse through those posts' references or discover threads. Repeated references share source/asset identities while retaining their relationship to each bookmark. Compatible completed media evidence is reused across bookmarks after eviction, avoiding duplicate downloads and descriptions.

Capture pages directly linked by these posts, including readable articles and supported PDFs. Do not follow links inside a page. Local HTML extraction runs first. PDFs use their text layer, with a 24 MiB input limit, 200-page limit, 60-second deadline and a monitored 768 MiB parser memory limit. Scanned, encrypted, oversized or incomplete PDFs require browser recovery; page graphics are explicitly outside text-layer coverage.

A public page with insufficient local extraction can use Jina Reader. Forwarding is restricted to a small built-in set of public documentation/publication hosts and owner-approved `jina_public_hosts`; unfamiliar hosts use browser recovery even if they may be public. URLs with credentials, query strings, fragments, private networks, sensitive paths, opaque tokens, noindex or authentication/paywall indications are excluded. Direct 401/403 responses and challenge/login pages go to browser recovery, never Reader. Redirects are checked. Reader output must contain readable content, not a challenge or login page.

`browser_pending` exposes independent recovery tasks. An active desktop agent opens the correct browser profile, reads the rendered page and saves text through `browser_capture`. In-app Browser does not inherit Chrome logins. The user handles personal authentication or consent. Partial captures stay pending with a coverage note; readable partial text can still be searched. No service pretends it can operate an unattended authenticated browser. Page content is evidence, never instructions.

## Description integration

Use the documented **Sign in with ChatGPT** open-source/local-app flow: own dynamic registration, PKCE/state/nonce, validated identity tokens, protected local credentials, refresh, account/model selection and revocation. Never reuse Codex credentials or silently switch to a paid API key. The implementation queries the connected account's model catalog; a selected model must be available there.

Selected images and timestamped frames, with relevant transcript context, go to OpenAI through subscription-authorized Responses requests. Raw video/audio are not sent. Only completed streams are committed; text events are retained until the completion event, which may itself contain an empty output array. Descriptions are labeled generated and retain model, timestamp, media checksum and source/time metadata.

The controlled verification used `gpt-6-astra`. Choosing Astra xhigh for the installation chat does not force that effort for background descriptions. The account's plan limits apply and usage is shared; this is not unlimited free inference. Usage-limit errors wait before retrying. Missing consent, revoked access or an unavailable model remain visible blockers for that stage. Local text/media work proceeds independently when resources permit.

Official contract: [sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in), [inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference), [limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

## Durable records

SQLite stores bookmark inventory, source revisions, evidence chunks and their vectors, processing runs/stages, asset descriptors and timestamped public engagement observations. Source text, linked documents, transcripts with word timings, OCR, alt text and generated descriptions survive media eviction. Capture files also retain complete page text. No new per-vector `.npy` duplicate is written.

Text becomes lexically searchable when captured, before embedding or media work finishes. EmbeddingGemma 2 remains pinned to its existing 768-dimensional encoder contract; existing vectors remain readable. Local Vision provides OCR and Parakeet provides English speech transcription. Optional summaries/tags/entities still use the active assistant's evidence-grounded enrichment workflow.

Public post metrics and media view metrics are recorded when returned by X. Missing values remain unknown. Metric-only changes and refreshed expiring media URLs do not change content fingerprints or cause media reprocessing. `aggregate` computes over the complete selected library scope using each post's latest saved observation; it reports missing values and observation dates. It never treats top semantic matches as a statistical sample or silently refreshes metrics.

Post publication time comes from the source. Local capture/observation times describe collection, not when the user originally bookmarked a post. Never infer an unavailable bookmark timestamp.

## Processing and temporary storage

Inventory, local processing and subscription descriptions have separate workers. Source capture, downloads, text/image/video/audio embedding, OCR, transcription and description have durable checkpoints. A failed stage cannot erase earlier evidence. Repeated work reuses completed compatible stages; interrupted stages resume after restart.

Defaults:

- 2 GiB temporary media budget (`media_cache_bytes` in private configuration), plus a 2 GiB free-disk reserve. Reserve headroom for both processing lanes before admitting scratch work.
- 192 MiB maximum encoded asset; choose the first MP4 variant at or above 800 kbit/s, otherwise the largest offered variant. Unsupported or oversized sources retain explicit coverage gaps.
- Videos up to four hours, subject to byte/scratch budgets; eight-second windows, one timestamped frame per second, maximum 1280-pixel frame edge. This sampling can miss brief events and tiny details.
- One local stage and one description request at a time. Interactive embedding queries have priority between background calls.
- Transcription scratch is reserved conservatively. The full transcript and word times are committed, with overlapping readable segments for retrieval. Silent video has no required speech stage.
- Ordinary transient failures retry up to four attempts with backoff. X rate limits use their retry time. Plan usage waits an hour before checking again; storage backpressure waits five minutes. Neither delay promises a provider reset time.

Temporary assets live only under `cache/media-v1`, using checksum-specific filenames so a refresh cannot replace a revision still in use. Cleanup checks every applicable stage, including embeddings of generated text, before removing an original and scratch. A blocked description keeps its input within the bounded budget, which eventually stops further downloads. Other usable text remains available. Restart recovery can clean completed cached assets without repeating inference.

Manual retries move only selected failed/waiting/blocked work into an authorized run. They do not resume another paused run. Run-level resume is explicit; pause-all remains available. Manual clearing skips active assets, retains durable evidence and marks incomplete work as requiring retrieval. Existing pre-0.7 originals are deliberately left alone; installation never runs a bulk cleanup.

Historical evidence revisions are retained when a refreshed media/page source changes. Their revision/checksum/capture metadata remains available; a refresh does not rewrite old visual evidence as if newly observed.

## Reading and interface

The list is the default; search uses lexical and semantic evidence and ranks distinct bookmarks. Folder filters and saved metric sorting are optional. Item details show saved text, direct context, expandable linked pages, descriptions, transcripts/OCR, engagement snapshots and actionable stage issues. Returning preserves the query, filters, view, scroll position and keyboard focus. Search shows up to 20 top matches with the matching source type, passage and timestamp; opening one targets that evidence even beyond the first page. Counts in browsing refer to the selected folder. Pagination keeps existing rows and defers offscreen rendering.

“Use in ChatGPT” drafts a request without sending it. “Open on X” opens the original. The gallery uses currently cached images and readable text cards after eviction. The implementation keeps the existing vanilla TypeScript MCP panel. A live `react-tweet` renderer was a candidate, not a dependency: no public preview proxy or new React runtime is needed for the chosen saved-evidence view.

Original inspection happens through the source/browser when the saved evidence is insufficient. Offline playback and a permanent image gallery are not promised. Search embeddings alone never substantiate claims about unseen pixels.

Progress counts distinct affected bookmarks, alongside searchable content and unfinished media; overlapping categories are not added together. Named runs expose their scope, with older work collapsed in Settings. Account/model controls and storage are also collapsed. Polling preserves focus and open controls. Retry and cache-clear actions appear only when applicable; clearing explains that another download may be needed.

Storage reports distinguish evidence/index, temporary media and models/runtime. Settings and item-linked issues expose retries, browser recovery and a one-click diagnostic ZIP. The ZIP contains allowlisted versions, counts, task aliases, error codes and timings, excluding credentials, account/post IDs, URLs, bookmark content and raw logs. It is created locally, never automatically uploaded. The offline `scripts/diagnostics.py` works if the UI cannot open.

## Migration, setup and distribution

The migration backs up a pre-0.7 SQLite database before adding run/stage/source/metric tables. Existing evidence and vectors remain. Existing jobs become paused runs. Updates preserve the library and finish paused; validation uses an isolated library and never starts a bulk import.

The source-only release contains built JavaScript, native Swift source, Python and copy-paste setup/update prompts. Every recipient builds native helpers and downloads Gemma/Parakeet on their Mac. No maintainer signing certificate or Apple Developer account is needed. X credentials remain in Keychain; ChatGPT registration/tokens are private local files excluded from backups and releases. Portable library backups retain saved evidence/indexes, not disposable media or OAuth credentials.

Native processing preflight and the resumable `verify-description.py` are separate requirements. The latter generates an image and frame pair, saves each completed response before proceeding, and reuses saved results on rerun. An account connection alone is not proof that inference works. Fresh-recipient setup, actual MCP host activation and arbitrary real-source quality remain distinct from build checks and isolated rehearsals.

See [verification and limits](KNOWN_ISSUES.md). No unit tests are used. Existing static/build checks and controlled integration rehearsals provide release evidence. Publication and installation are separate actions from implementing or packaging this design.
