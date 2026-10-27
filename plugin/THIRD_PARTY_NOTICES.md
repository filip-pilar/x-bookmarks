# Third-party components

- The packaged transcription adapter uses FluidAudio at revision `b68f484789d81fda21efbf81e2ca9fcfd9dc22aa` (Apache-2.0; license in `licenses/FluidAudio-Apache-2.0.txt`). Recipient setup builds `TranscribeAudio` locally from the accompanying Swift source and pinned Package.resolved; no native executable is distributed. Parakeet model files are downloaded separately by FluidAudio: https://huggingface.co/FluidInference/parakeet-unified-en-0.6b-coreml . Observe the model's published license and notices.
- EmbeddingGemma 2 is downloaded from Google, revision `914f7f89142e33e77833254d9c9b90c3cef7303b`: https://huggingface.co/google/embeddinggemma-2 . Model weights are not bundled. Google's Gemma terms apply: https://ai.google.dev/gemma/terms . Setup verifies the checkpoint checksum.
- OpenAI MCP Extensions: Apache-2.0; bundled notice in `licenses/OpenAI-MCP-Extensions.txt`.
- MCP TypeScript SDK and MCP Apps: Apache-2.0. Zod: MIT. Their licenses accompany the bundled release in `licenses/`.
- Subscription identity validation uses PyJWT 2.15.1 (MIT) and cryptography 50.0.2 (Apache-2.0 OR BSD-3-Clause). PDF text extraction uses pypdf 6.21.3 (BSD-3-Clause). These are installed on the recipient's device, not bundled in this source package.
- Python dependencies are installed from the pinned requirements and retain their package licenses. FFmpeg, yt-dlp, Node and Python are provisioned separately; no downloaded bookmark media or private library files are distributed in the plugin.
- Linked-page SVG rendering uses resvg-py 0.5.0, installed on the recipient's device with its package notices. SVG references to external resources are rejected; raster output and processing time are bounded.
- DigUp informed the design. No DigUp source code is included.

- The saved-post renderer bundles React, React DOM, react-tweet, SWR and clsx (MIT) and SWC helpers (Apache-2.0). Their license notices accompany the release. Tweet rendering uses saved X API data; it does not use a public syndication proxy. Remote media is loaded from X when viewed, with video playback on demand.

- Markdown evidence rendering uses markdown-it and its dependencies (MIT). HTML execution and embedded media are disabled. License notices accompany the release.
- The ChatGPT action uses the OpenAI logo path from `@openai/apps-sdk-ui` 0.2.2 (MIT), with its notice in `licenses/OpenAI-Apps-SDK-UI.txt`. The logo identifies the destination; the UI SDK is not bundled.

- Accessible choice menus use Radix Select and its bundled positioning/focus/scroll dependencies (MIT). Their license notices are included in `licenses/`.
