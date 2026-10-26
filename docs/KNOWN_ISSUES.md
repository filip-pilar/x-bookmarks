# Known issues

These issues apply to the 0.5.1 baseline. Public GitHub distribution does not resolve them.

- **macOS may block TranscribeAudio.** The helper has an ad hoc signature and is not Developer ID signed/notarized. Setup checks files but does not prove the helper can launch. Use only the standard approval for the specific verified executable; do not disable Gatekeeper globally. The transcription adapter selects the installed plugin's `bin/TranscribeAudio`, rather than the extra copy under the library's `bin/` directory.
- **Import can stop at a page boundary.** Any partial errors in an X bookmark response reject the whole page, including valid records. Access failures can stop a sync and rate limits schedule retries. There is no intentional 300-bookmark import cap. The cause of the reported 300-record cutoff has not been verified on the affected device.
- **Folders are published after the complete scan.** A failed folder/membership scan can leave staged folders invisible while bookmarks are already saved. Pending or failed processing can block a new folder-only sync.
- **Image failures can persist.** Referenced media missing from X expansions is not explicitly reported. Successful HTTP responses are cached before image decoding; invalid image bytes may be reused by Repair.
- **Some errors lose detail.** The outer transcription wrapper replaces stderr with a generic failure. Per-recording `run.log` and `metadata.json` may contain more detail. X HTTP response bodies are not retained in job errors.
- **Setup and history have separate verification limits.** Downloaded files and a connected account do not prove successful image/video/transcription processing. Exhausting X's returned pages does not independently prove access to every historical bookmark. Speech models download on first transcription.

For diagnosis, inspect saved queue state, page metadata, coverage warnings and transcription logs before retrying paid API work. Public issue reports should omit tokens, OAuth callback URLs, capability values, Client IDs, personal bookmark contents and unredacted logs.
