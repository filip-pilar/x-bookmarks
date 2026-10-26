# Verification and remaining limitations

Version 0.6.0 uses native source distribution and a required local processing preflight. No native executables or models are included in the release ZIP; each recipient builds the helpers and downloads Gemma/Parakeet on their own Mac. No Apple Developer membership or signing certificate from the maintainer is required. System/toolchain installers and Keychain may still require personal macOS interaction.

## Verified behavior

- An isolated Apple silicon setup compiled both helpers and passed Vision OCR, Gemma text/image/audio/video/joint embeddings, and actual Parakeet transcription with word timings on generated media. Parakeet downloaded into that isolated library; Gemma and Python dependencies were reused from an existing matching local installation. Setup finished paused without X requests.
- A controlled inventory rehearsal saved 337 bookmarks across four pages, retained valid records from partial responses, and reported coverage gaps. Folder names appeared before membership completion. Interrupted/partial scans retained existing folders and memberships. Failed local processing did not block a folder-only sync.
- Inventory completed while a processing worker was deliberately held. A source changed during processing was requeued afterward, preventing a stale index from silently becoming final.
- Controlled HTTP rehearsal rejected non-image bytes returned with HTTP success, repaired an invalid destination/shared cache, and preserved subprocess stderr in the surfaced error. Missing X media expansions are explicitly looked up/reported. A complete generated article/image/video fixture saved OCR, image/audio/video embeddings, transcript and joint chunks while an injected optional thread-access failure was reported as partial coverage. Missing media expansion recovery also succeeded in that fixture.
- TypeScript/build, Python syntax and archive checks are separate from runtime verification. Release archive validation rejects native executables, private configuration, model files and library/cache data.

## Remaining boundaries

- **Fresh recipient installation and OAuth:** the full new flow has not been verified on the affected friend's device. The verification above reused Gemma and the Python environment; it does not establish a completely fresh Homebrew/Python/Gemma installation. The copy-paste prompt requires the preflight on every recipient's Mac before reporting ready.
- **Reported 300-bookmark cutoff:** there is no intentional bookmark import cap of 300. The exact cause on the affected device remains unconfirmed without saved queue/page/API diagnostics. More X credits do not remove endpoint rate limits, access restrictions or provider history limits. Do not buy more credits as a generic fix.
- **Historical completeness:** exhausting the pages X returns does not independently prove every historical bookmark is exposed. Partial responses remain visible as coverage gaps. Incomplete folders and unresolved folder-post IDs are reported; prior membership is retained until a complete reconciliation can replace it.
- **Real-source coverage:** synthetic preflight cannot prove that every remote image, video or protected/deleted post is accessible. Static linked-page extraction can omit dynamically rendered images/layout; linked PDFs and standalone external-video platforms are unsupported. X Article images are galleries because inline placement is not supplied. Parent/quote capture is capped at 24 lookups and author-thread expansion at 300 returned posts; that thread limit is separate from bookmark inventory.
- **Recovery and spending:** updates preserve and pause old jobs. Retrying inventory, Repair or Refresh is a separate authorized operation and may make paid X reads. Diagnose saved state first. Partial-response gaps may require a new full scan; access/billing failures require resolving the reported X error. Model downloads can require network access and Google model-term acceptance.

Private diagnostics include `preflight.json`, job errors/payloads in `library.sqlite`, the `calls.details` records, saved responses under `scans/`, per-item coverage, and transcription `run.log`/`metadata.json`. Read them before restarting services or retrying work. Starting a previously unpaused service can resume its queue.

Public reports must omit credentials, OAuth/callback URLs, capability tokens, Client IDs, personal bookmark contents and unredacted logs. Share versions, phase/page counts, HTTP statuses, preflight results and redacted error summaries.
