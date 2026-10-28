import { clearTweets, renderTweets, type PostCard } from "./tweet.js";
import { Choice } from "./choice.js";
import { renderMarkdown } from "./markdown.js";
import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import { OpenAIExtensions } from "@openai/mcp-extensions/app";
const appInfo = { name: "Bookmark Library", version: "0.7.9" };
const app = new App(appInfo),
  extensions = new OpenAIExtensions(app);
const $ = (id: string) => document.getElementById(id)!;
const input = (id: string) => $(id) as HTMLInputElement;
const choices = new Map<string, Choice>([
  ["folder", new Choice($("folder"), [{ value: "", label: "All folders" }])],
  [
    "sort",
    new Choice($("sort"), [
      { value: "", label: "Newest posts" },
      { value: "like_count", label: "Most likes" },
      { value: "impression_count", label: "Most impressions" },
      { value: "bookmark_count", label: "Most bookmarks" },
    ]),
  ],
  [
    "chatgpt-account",
    new Choice($("chatgpt-account"), [{ value: "", label: "Choose account" }]),
  ],
  ["model", new Choice($("model"), [], "Choose a model")],
]);
const choice = (id: string) => choices.get(id)!;
for (const [id, values, suffix] of [
  ["local-workers", [0, 1, 2, 3, 4], ""],
  ["description-workers", [0, 1, 2, 3, 4], ""],
  ["video-height", [240, 360, 480, 720, 1080], "p"],
  ["cache-gib", [4, 8, 16, 32, 64, 128], " GiB"],
  ["import-items", [100, 500, 1000, 0], ""],
  ["import-x-limit", [25, 100, 500, 1000, 0], ""],
  ["process-x-limit", [25, 100, 500, 1000, 0], ""],
  ["process-description-limit", [25, 100, 500, 1000, 0], ""],
] as const) {
  choices.set(
    id,
    new Choice(
      $(id),
      values.map((value) => ({
        value: String(value),
        label:
          value === 0
            ? id.endsWith("workers")
              ? "Automatic · 1"
              : "No limit"
            : String(value) + suffix,
      })),
    ),
  );
}
let changingPreferences = false;
function setChoiceValue(id: string, value: number) {
  const c = choice(id),
    v = String(value);
  if (!c.options.some((o) => o.value === v))
    c.setOptions([...c.options, { value: v, label: v }], v);
  else c.value = v;
}
const dialog = (id: string) => $(id) as HTMLDialogElement;
$("settings-version").textContent = `Version ${appInfo.version}`;
type Metadata = {
  url?: string;
  source_id?: string;
  asset_id?: string;
  start_seconds?: number;
  end_seconds?: number;
  start_char?: number;
  end_char?: number;
  generated?: boolean;
  coverage?: string[];
};
type Row = {
  id: string;
  item?: string;
  title: string;
  author: string;
  text?: string;
  excerpt?: string;
  url: string;
  state?: string;
  has_media?: boolean;
  content_types?: string[];
  folders?: { name: string }[];
  kind?: string;
  evidence?: Metadata;
  evidence_id?: string;
  presentation?: {
    name?: string;
    avatar?: string;
    created_at?: string;
    text?: string;
    preview?: string;
  };
};
type Stage = {
  id: string;
  item: string;
  title?: string;
  author?: string;
  kind: string;
  state: string;
  error?: string;
  next_at?: number;
  run: string;
};
type Run = {
  media_processing?: boolean;
  id: string;
  state: string;
  created: number;
  label: string;
  items: number;
  retryable_steps: number;
  pending_steps: number;
  limits?: {
    x_limit: number;
    x_used: number;
    description_limit: number;
    description_used: number;
    item_limit: number;
    reason: string;
  };
};
type State = {
  preferences: {
    cache_gib: number;
    local_workers: number;
    description_workers: number;
    video_height: number;
    x_requests: number;
    description_requests: number;
  };
  cooldowns: { until: number; reason: string }[];
  unprocessed: number;
  connected: boolean;
  account?: { username: string };
  total: number;
  searchable: number;
  paused: boolean;
  stage_counts: { kind: string; state: string; count: number }[];
  stage_issues: Stage[];
  runs: Run[];
  jobs: Stage[];
  progress: {
    unfinished: number;
    needs_attention: number;
    unfinished_media: number;
    unavailable: number;
    syncs: number;
    sync_running: number;
    sync_attention: number;
  };
  temporary_bytes: number;
  temporary_limit: number;
  storage_bytes: number;
  index_bytes: number;
  runtime_bytes: number;
  last_processed?: unknown;
  folder_inventory?: {
    at?: number;
    complete?: boolean;
    in_progress?: boolean;
    scope?: string;
  };
  last_inventory?: {
    at: number;
    mode: string;
    returned: number;
    partial_pages?: number;
    unavailable_records?: number;
  };
  full_inventory_at?: number;
  x_auth_error?: string;
  processing_issue_count: number;
  run_count: number;
  running_run_count: number;
  chatgpt: {
    connected: boolean;
    auth_error?: string;
    plan_enabled: boolean;
    model?: string;
    active?: string;
    accounts: { id: string; label: string }[];
  };
};
type Evidence = { id: string; kind: string; text: string; metadata: Metadata };
type Detail = Omit<Row, "evidence"> & {
  post_cards: PostCard[];
  evidence: Evidence[];
  matched_evidence?: Evidence;
  next_offset: number | null;
  stages: Stage[];
  coverage: string[];
  metrics: { at: number; values: Record<string, number> }[];
  retryable_steps: number;
  clearable_bytes: number;
  post: { referenced_tweets?: { id: string; type: string }[] };
};
let state: State | undefined,
  selected: Detail | undefined,
  rows: Row[] = [],
  next: number | null = null;
let gallery = false,
  searching = false,
  scroll = 0,
  epoch = 0,
  detailEpoch = 0,
  scopedTotal = 0,
  loading = false;
let loadedQuery = "",
  pendingQuery = "",
  loadingKey = "",
  librarySignature = "",
  folderSignature = "",
  refreshing = false;
let returnItem = "",
  currentMatch = "";
let availableModels: { value: string; label: string }[] = [],
  modelsState: "idle" | "loading" | "ready" | "error" = "idle",
  modelsLoadedAt = 0,
  modelsRequest = 0,
  changingChatGPT = false;
const esc = (s: unknown) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const number = (n = 0) => n.toLocaleString();
const count = (n: number, word = "bookmark") =>
  `${number(n)} ${word}${n === 1 ? "" : "s"}`;
const bytes = (n = 0) =>
  n >= 1024 ** 3
    ? `${(n / 1024 ** 3).toFixed(1)} GB`
    : n >= 1024 ** 2
      ? `${(n / 1024 ** 2).toFixed(1)} MB`
      : n >= 1024
        ? `${Math.ceil(n / 1024)} KB`
        : `${n} B`;
const date = (t: number) =>
  new Date(t * 1000).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
const timestamp = (s: number) => {
  const n = Math.max(0, Math.floor(s));
  return n >= 3600
    ? `${Math.floor(n / 3600)}:${String(Math.floor((n % 3600) / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`
    : `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
};
const timeRange = (m: Metadata) =>
  m.start_seconds === undefined
    ? ""
    : timestamp(m.start_seconds) +
      (m.end_seconds !== undefined ? "–" + timestamp(m.end_seconds) : "");
function announce(text: string) {
  $("announcement").textContent = text;
}
function feedbackRoot(): HTMLElement {
  return document.querySelector<HTMLDialogElement>("dialog[open]") || $("main");
}
function feedback(
  type: "error" | "notice",
  text: string,
  root = feedbackRoot(),
) {
  const target =
    root.id === "main"
      ? $(type)
      : root.querySelector<HTMLElement>(`[data-${type}]`) || $(type);
  target.textContent = text;
  target.hidden = !text;
  if (text && type === "error" && root instanceof HTMLDialogElement)
    target.scrollIntoView({ block: "nearest" });
}
async function call(
  name: string,
  args: Record<string, unknown> = {},
): Promise<any> {
  const r = await app.callServerTool({ name, arguments: args });
  if (r.isError)
    throw new Error(
      r.content
        ?.filter((c: any) => c.type === "text")
        .map((c: any) => c.text)
        .join(" ") || "Operation failed. Try again.",
    );
  return r.structuredContent;
}
async function act(fn: () => Promise<void>, button?: HTMLButtonElement) {
  if (button?.disabled) return;
  const root = feedbackRoot();
  const restoreFocus = document.activeElement === button;
  feedback("error", "", root);
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  }
  try {
    await fn();
  } catch (e) {
    feedback(
      "error",
      (e as Error).message,
      root.isConnected && (!(root instanceof HTMLDialogElement) || root.open)
        ? root
        : $("main"),
    );
  } finally {
    if (button) {
      button.disabled = false;
      button.removeAttribute("aria-busy");
      if (
        restoreFocus &&
        document.activeElement === document.body &&
        button.isConnected &&
        !button.closest("[hidden]")
      )
        button.focus({ preventScroll: true });
      else if (
        restoreFocus &&
        document.activeElement === document.body &&
        !button.isConnected &&
        button.dataset.run &&
        dialog("issues-dialog").open
      ) {
        const replacement = $("review-list").querySelector<HTMLButtonElement>(
          `[data-run-id="${CSS.escape(button.dataset.run)}"] button`,
        );
        (replacement || $("review-count")).focus({ preventScroll: true });
      }
    }
  }
}
function action(id: string, fn: () => Promise<void>) {
  const b = $(id) as HTMLButtonElement;
  b.onclick = () => void act(fn, b);
}
function showDialog(id: string) {
  closeFilters();
  const d = dialog(id);
  for (const e of d.querySelectorAll<HTMLElement>(
    "[data-error], [data-notice]",
  ))
    e.hidden = true;
  for (const c of choices.values()) c.close();
  d.showModal();
  if (id === "settings-dialog") void loadModels();
  if (id === "sync-dialog") {
    setChoiceValue("import-items", 100);
    setChoiceValue("import-x-limit", state?.preferences?.x_requests ?? 100);
  }
  if (id === "processing-dialog") {
    $("processing-scope").textContent =
      `${count(state?.total || 0)} · Existing library only`;
    setChoiceValue("process-x-limit", state?.preferences?.x_requests ?? 100);
    setChoiceValue(
      "process-description-limit",
      state?.preferences?.description_requests ?? 100,
    );
  }
}
async function openUrl(url: string) {
  if (!/^https?:\/\//.test(url))
    throw new Error("This source link is unavailable.");
  await app.openLink({ url });
}
function wireLinks(root: HTMLElement) {
  for (const a of root.querySelectorAll<HTMLAnchorElement>("a[data-source]"))
    a.onclick = (e) => {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      void act(() => openUrl(a.href));
    };
}
const sourceLink = (url?: string) =>
  url && /^https?:\/\//.test(url)
    ? `<a class="small-link" data-source href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>`
    : "";
async function draft(text: string) {
  if (!extensions.message) {
    feedback("notice", `Copy this request into ChatGPT:\n${text}`);
    return;
  }
  await extensions.message.send({
    role: "user",
    content: [{ type: "text", text }],
    _meta: { "openai/message": { target: "active", send: false } },
  });
  feedback("notice", "Request drafted in ChatGPT.");
}
// Skip unchanged DOM and defer replacements while a user is interacting with it.
function patch(el: HTMLElement, html: string): boolean {
  if (el.dataset.render === html || el.contains(document.activeElement))
    return false;
  el.innerHTML = html;
  el.dataset.render = html;
  return true;
}
function options(
  id: string,
  values: { value: string; label: string }[],
  value: string,
) {
  choice(id).setOptions(values, value);
}
const stageLabel = (kind: string) =>
  (
    ({
      caption: "Image description",
      video_caption: "Video description",
      video_ocr: "Text in video",
      text: "Search indexing",
      context: "Reply or quote",
      media_lookup: "Media details",
      download: "Media download",
      page: "Linked page",
      transcript: "Transcript",
      image: "Image search",
      video: "Video search",
      audio: "Audio search",
      ocr: "Text in image",
      sync: "Bookmark sync",
      process: "Bookmark processing",
    }) as Record<string, string>
  )[kind] || kind.replaceAll("_", " ");
const evidenceLabel = (kind = "text") =>
  (
    ({
      text: "Post text",
      context: "Reply or quote",
      "alt-text": "Source alt text",
      "linked-document": "Linked page",
      description: "Visual description",
      transcript: "Transcript",
      ocr: "Text in image",
      summary: "AI summary",
      image: "Image",
      video: "Video",
      audio: "Audio",
      joint: "Media",
    }) as Record<string, string>
  )[kind] || kind;
const issueText = (error: string | null = "") =>
  error?.startsWith("temporary_budget_required:")
    ? `Increase temporary space in Settings to at least ${error.split(":")[1]} GiB. Progress is saved.`
    : (
        {
          chatgpt_sign_in_required: "Reconnect ChatGPT in Settings.",
          chatgpt_plan_consent_required:
            "Allow this app to use your ChatGPT plan.",
          chatgpt_model_selection_required:
            "Choose a description model in Settings.",
          temporary_storage_full:
            "Temporary space is full. Increase it in Settings, finish pending work or clear selected failed media.",
          disk_space_low:
            "Free disk space before continuing. Processing keeps a separate 2 GiB disk reserve.",
          chatgpt_cooldown:
            "ChatGPT is waiting for its allowance to reset. Local processing continues.",
          yt_dlp_missing: "Update setup to install yt-dlp for video downloads.",
          temporary_budget_too_small:
            "This media needs more temporary space than the configured budget allows.",
          source_retrieval_required:
            "The original must be downloaded again before this step can finish.",
          authentication_or_challenge:
            "This page needs browser access or personal sign-in.",
          incomplete_extraction:
            "The readable page content could not be captured completely.",
          partial_browser_capture:
            "Some page content still needs browser recovery.",
          processing_paused:
            "Saved progress will continue when this run is resumed.",
        } as Record<string, string>
      )[error || ""] || (error || "").replaceAll("_", " ");
const stateLabel = (s: string) =>
  (
    ({
      failed: "Needs a retry",
      blocked: "Needs attention",
      browser: "Needs browser recovery",
      waiting: "Waiting to retry",
      unavailable: "Source unavailable",
      queued: "Queued",
      running: "Processing",
    }) as Record<string, string>
  )[s] || s;
function runHtml(r: Run) {
  const active = r.state === "running" && !state?.paused;
  const b = r.limits,
    limited = !!b?.reason;
  const usage = b
    ? `<p class="muted">X requests ${number(b.x_used)} / ${b.x_limit ? number(b.x_limit) : "unlimited"}${r.media_processing ? ` · Image descriptions ${number(b.description_used)} / ${b.description_limit ? number(b.description_limit) : "unlimited"}` : ""}</p>`
    : "";
  return `<article class="run" data-run-id="${esc(r.id)}"><p class="run-title">${esc(r.label)}${r.items ? " · " + count(r.items) : ""}</p><p class="muted">${esc(date(r.created))} · ${r.state === "complete" ? "Complete" : limited ? "Limit reached" : active ? "Running" : "Paused"}</p>${usage}<div class="actions">${limited ? `<button data-extend="${esc(b!.reason)}" data-run="${esc(r.id)}">Add 100 ${b!.reason === "items" ? "bookmarks" : b!.reason === "x" ? "X requests" : "image descriptions"}</button>` : r.pending_steps ? `<button data-action="${active ? "pause" : "resume"}" data-run="${esc(r.id)}">${active ? "Pause" : "Resume"}</button>` : ""}${r.retryable_steps && !limited ? `<button data-action="retry" data-run="${esc(r.id)}">Retry ${count(r.retryable_steps, "step")}</button>` : ""}</div></article>`;
}
type ReviewItem = {
  key: string;
  item: string;
  run: string;
  title: string;
  author: string;
  error: string;
  state: string;
  steps: number;
  kinds: string[];
  retryable: number;
  browser: boolean;
  account: boolean;
  next_at?: number;
};
let reviewView: "issues" | "runs" = "issues",
  reviewOffset = 0,
  reviewRequest = 0;
let reviewNext: number | null = null;
async function loadReview(offset = 0) {
  const request = ++reviewRequest,
    view = reviewView;
  const focus = document.activeElement;
  const pageChanged = offset !== reviewOffset;
  $("review-list").setAttribute("aria-busy", "true");
  for (const id of ["review-prev", "review-next"])
    ($(id) as HTMLButtonElement).disabled = true;
  try {
    const r = await call("processing_details", { view, offset, limit: 20 });
    if (request !== reviewRequest) return;
    reviewOffset = r.offset;
    reviewNext = r.next_offset;
    $("review-list").innerHTML =
      view === "runs"
        ? r.items.map(runHtml).join("")
        : r.items
            .map(
              (g: ReviewItem) =>
                `<article><p class="issue-title">${esc(g.author ? "@" + g.author + " · " : "")}${esc(g.title)}</p><p class="muted">${esc(stateLabel(g.state))} · ${count(g.steps, "step")}</p><p class="muted">${esc(g.kinds.map(stageLabel).join(" · "))}</p>${g.error ? `<p>${esc(issueText(g.error))}</p>` : ""}<div class="actions">${g.item ? `<button data-item="${esc(g.item)}">Open bookmark</button>` : ""}${g.account || g.error?.startsWith("temporary_") ? "<button data-settings>Open settings</button>" : ""}${g.browser ? "<button data-recovery>Recover pages</button>" : ""}${g.retryable && !g.account ? `<button data-action="retry" data-item-retry="${esc(g.item)}" data-run="${esc(g.run)}">Retry failed steps</button>` : ""}</div></article>`,
            )
            .join("");
    $("review-empty").hidden = r.total > 0;
    $("review-empty").textContent =
      view === "runs" ? "No processing runs yet." : "No processing issues.";
    $("review-count").textContent = r.total
      ? `${r.offset + 1}–${r.offset + r.items.length} of ${number(r.total)}`
      : "";
    if (pageChanged) dialog("issues-dialog").scrollTop = 0;
    for (const button of $("review-list").querySelectorAll<HTMLButtonElement>(
      "button",
    ))
      button.onclick = () =>
        void act(async () => {
          if (button.dataset.extend) {
            await call("extend_run", {
              run: button.dataset.run,
              kind: button.dataset.extend,
              amount: 100,
            });
            await refresh();
            await loadReview(reviewOffset);
            feedback("notice", "Limit increased. Resume when ready.");
          } else if (button.dataset.item) {
            dialog("issues-dialog").close();
            await openItem(button.dataset.item);
          } else if (button.hasAttribute("data-settings")) {
            dialog("issues-dialog").close();
            showDialog("settings-dialog");
          } else if (button.hasAttribute("data-recovery"))
            await draft(recoveryPrompt);
          else {
            const action = button.dataset.action!,
              item = button.dataset.itemRetry;
            const r = await call("queue_control", {
              action,
              ...(item ? { item } : { run: button.dataset.run }),
            });
            if (action === "retry") retryNotice(r);
            else
              feedback(
                "notice",
                action === "resume"
                  ? "Run resumed."
                  : "Pausing after the current step.",
              );
            await refresh();
            await loadReview(reviewOffset);
          }
        }, button);
  } finally {
    if (request === reviewRequest) {
      $("review-list").setAttribute("aria-busy", "false");
      ($("review-prev") as HTMLButtonElement).disabled = reviewOffset === 0;
      ($("review-next") as HTMLButtonElement).disabled = reviewNext === null;
      if (
        focus instanceof HTMLButtonElement &&
        ["review-prev", "review-next"].includes(focus.id) &&
        (document.activeElement === focus ||
          document.activeElement === document.body)
      )
        $("review-count").focus({ preventScroll: true });
    }
  }
}
function openReview(view: "issues" | "runs") {
  if (view !== reviewView) {
    $("review-list").replaceChildren();
    $("review-count").textContent = "";
    $("review-empty").hidden = true;
    reviewOffset = 0;
    reviewNext = null;
    dialog("issues-dialog").scrollTop = 0;
  }
  reviewView = view;
  for (const name of ["issues", "runs"])
    $("review-" + name).setAttribute("aria-pressed", String(name === view));
  if (!dialog("issues-dialog").open) showDialog("issues-dialog");
  void act(() => loadReview());
}
function inventoryLabel() {
  const latest = state?.last_inventory;
  if (latest?.partial_pages || latest?.unavailable_records)
    return "Last sync incomplete";
  if (state?.full_inventory_at) return "API inventory checked";
  if (latest?.mode === "sample") return "Sample import";
  return state?.total ? "Full check incomplete" : "Not imported";
}
function renderCount() {
  if (loading) return;
  const coverage =
    !state?.full_inventory_at && state?.last_inventory?.mode === "sample"
      ? " · Sample"
      : "";
  $("count").textContent = searching
    ? `${number(rows.length)} top ${rows.length === 1 ? "match" : "matches"}`
    : `${rows.length === scopedTotal ? number(rows.length) : `${number(rows.length)} of ${number(scopedTotal)}`} saved${coverage}`;
}
function renderCoverage() {
  const folders = state?.folder_inventory;
  const label = folders?.in_progress
    ? "Check unfinished"
    : folders?.complete
      ? folders.scope === "existing"
        ? "Checked for saved bookmarks"
        : "Checked"
      : folders
        ? "Partially synced"
        : "Not synced";
  $("sync-coverage").innerHTML =
    `<div><dt>Saved locally</dt><dd>${number(state?.total)}</dd></div><div><dt>Bookmarks</dt><dd>${esc(inventoryLabel())}</dd></div><div><dt>Folders</dt><dd>${esc(label)}</dd></div>${state?.full_inventory_at ? `<div><dt>Last full check</dt><dd>${esc(date(state.full_inventory_at))}</dd></div>` : ""}`;
  $("folder-status").textContent =
    choice("folder").options.length > 1
      ? folders?.complete
        ? ""
        : label
      : folders?.complete
        ? "No folders"
        : label;
  $("folder-status").hidden = !$("folder-status").textContent;
  $("folder").hidden = choice("folder").options.length < 2;
  $("sync-new").textContent = state?.full_inventory_at
    ? "Sync new bookmarks"
    : "Check all bookmarks";
  $("sync-full").hidden = !state?.full_inventory_at;
  renderCount();
}
function connection(online: boolean) {
  $("connection-status").hidden = online;
  $("connection-message").textContent = state
    ? "Connection lost · Showing saved view"
    : "Library service unavailable";
  $("settings-connection").hidden = online;
  ($("sync") as HTMLButtonElement).disabled = !online;
}
function modelAccount(s = state) {
  return s?.chatgpt.connected && s.chatgpt.plan_enabled
    ? s.chatgpt.active || ""
    : "";
}
function renderModel() {
  const saved = state?.chatgpt.model || "";
  const selected = availableModels.find((m) => m.value === saved);
  const ready = modelsState === "ready";
  const selectable =
    ready &&
    availableModels.length > 0 &&
    (availableModels.length > 1 || !selected);
  $("model").hidden = !selectable;
  $("model-current").hidden = selectable;
  $("model-current").textContent =
    modelsState === "loading" || modelsState === "idle"
      ? "Loading models…"
      : selected?.label ||
        (saved
          ? saved
              .split("-")
              .map((part) =>
                part === "gpt"
                  ? "GPT"
                  : part.charAt(0).toUpperCase() + part.slice(1),
              )
              .join(" ")
          : "None available");
  choice("model").setOptions(availableModels, selected ? saved : "");
  choice("model").disabled = changingChatGPT || !selectable;
  const message =
    modelsState === "error"
      ? "Couldn’t load models."
      : ready && !availableModels.length
        ? "No models available for this account."
        : ready && saved && !selected
          ? "Saved model unavailable. Choose another."
          : "";
  $("model-message").textContent = message;
  $("model-feedback").hidden = !message;
  $("retry-models").hidden =
    modelsState !== "error" && !(ready && !availableModels.length);
  $("description-settings").setAttribute(
    "aria-busy",
    String(modelsState === "loading"),
  );
}
async function loadModels(force = false) {
  const account = modelAccount();
  if (
    !account ||
    modelsState === "loading" ||
    (!force &&
      modelsState === "ready" &&
      Date.now() - modelsLoadedAt < 5 * 60_000)
  )
    return;
  const request = ++modelsRequest;
  const fromRetry = document.activeElement === $("retry-models");
  modelsState = "loading";
  renderModel();
  try {
    const r = await call("chatgpt_models");
    if (request !== modelsRequest || account !== modelAccount()) return;
    availableModels = r.models.map((m: { id: string; name: string }) => ({
      value: m.id,
      label: m.name,
    }));
    modelsState = "ready";
    modelsLoadedAt = Date.now();
  } catch {
    if (request !== modelsRequest || account !== modelAccount()) return;
    modelsState = "error";
    try {
      await refresh();
    } catch {}
  }
  renderModel();
  if (fromRetry)
    requestAnimationFrame(() => {
      if (
        document.activeElement !== document.body ||
        !dialog("settings-dialog").open
      )
        return;
      if (!$("retry-models").hidden)
        $("retry-models").focus({ preventScroll: true });
      else if (!$("model").hidden) choice("model").focus();
      else $("model-current").focus({ preventScroll: true });
    });
}
function renderState(s: State) {
  const accountChanged = modelAccount() !== modelAccount(s);
  if (accountChanged) {
    modelsRequest++;
    modelsState = "idle";
    availableModels = [];
  }
  const signature = JSON.stringify([s.total, s.last_processed, s.stage_counts]);
  if (librarySignature && librarySignature !== signature)
    $("refresh-content").hidden = false;
  librarySignature = signature;
  state = s;
  for (const id of ["sync", "settings"])
    ($(id) as HTMLButtonElement).disabled = false;
  const p = s.progress;
  $("progress").hidden = !(
    p.unfinished ||
    p.unavailable ||
    p.syncs ||
    s.unprocessed
  );
  const parts = [`${number(s.searchable)} of ${count(s.total)} searchable`];
  if (s.unprocessed) parts.push(`${number(s.unprocessed)} ready to process`);
  if (p.unfinished_media)
    parts.push(`${count(p.unfinished_media)} with unfinished media`);
  else if (p.unfinished)
    parts.push(`${count(p.unfinished)} with unfinished work`);
  if (p.needs_attention)
    parts.push(`${number(p.needs_attention)} need attention`);
  if (p.unavailable)
    parts.push(`${number(p.unavailable)} with unavailable sources`);
  if (p.syncs)
    parts.push(
      p.sync_attention
        ? "Sync needs attention"
        : s.paused || !p.sync_running
          ? "Sync paused"
          : "Sync in progress",
    );
  $("progress-text").textContent =
    `${s.paused && (p.unfinished || p.syncs) ? "Paused · " : ""}${parts.join(" · ")}`;
  $("pause").hidden = s.paused || !s.running_run_count;
  $("issues").hidden = !s.processing_issue_count;
  $("manage-runs").hidden = !s.run_count;
  $("process-imported").hidden = !s.unprocessed;
  $("x-account").textContent = s.connected
    ? s.account?.username
      ? `@${s.account.username}`
      : "Connected"
    : s.x_auth_error
      ? "Sign-in needed"
      : "Not connected";
  $("connect-x").textContent = s.x_auth_error
    ? "Sign in again"
    : s.connected
      ? "Reconnect"
      : "Connect X";
  $("connect-x").classList.toggle("quiet", s.connected);
  const activeAccount = s.chatgpt.accounts.find(
    (a) => a.id === s.chatgpt.active,
  );
  $("chatgpt-state").textContent = s.chatgpt.connected
    ? s.chatgpt.plan_enabled
      ? activeAccount?.label || "Connected"
      : "Permission needed"
    : s.chatgpt.accounts.length
      ? "Sign-in needed"
      : "Not connected";
  $("connect-chatgpt").hidden = s.chatgpt.connected && s.chatgpt.plan_enabled;
  $("connect-chatgpt").textContent = s.chatgpt.connected
    ? "Authorize plan access"
    : s.chatgpt.accounts.length
      ? "Sign in again"
      : "Connect ChatGPT";
  $("reconnect-chatgpt").hidden =
    !s.chatgpt.connected || !s.chatgpt.plan_enabled;
  $("description-settings").hidden =
    !s.chatgpt.connected || !s.chatgpt.plan_enabled;
  $("account-settings").hidden = !s.chatgpt.accounts.length;
  $("usage").hidden = !s.chatgpt.connected;
  $("disconnect-chatgpt").hidden = !s.chatgpt.accounts.length;
  $("account-picker").hidden = s.chatgpt.accounts.length < 2;
  $("storage").innerHTML = [
    [
      "Temporary media",
      `${bytes(s.temporary_bytes)} / ${bytes(s.temporary_limit)}`,
    ],
    ["Evidence & index", bytes(s.storage_bytes + s.index_bytes)],
    ["Models & runtime", bytes(s.runtime_bytes)],
  ]
    .map(
      ([label, value]) =>
        `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`,
    )
    .join("");
  options(
    "chatgpt-account",
    [
      ...(s.chatgpt.accounts.length
        ? []
        : [{ value: "", label: "No connected account" }]),
      ...s.chatgpt.accounts.map((a) => ({ value: a.id, label: a.label })),
    ],
    s.chatgpt.active || "",
  );
  renderModel();
  if (modelsState === "idle" && dialog("settings-dialog").open)
    void loadModels();
  $("browser-recovery").hidden = !s.stage_counts.some(
    (x) => x.state === "browser" && x.count,
  );
  $("processing-state").textContent = s.paused
    ? p.unfinished || p.syncs
      ? "Paused"
      : "Paused · Nothing waiting"
    : s.running_run_count
      ? "Running"
      : "Nothing waiting";
  if (!changingPreferences && s.preferences) {
    for (const [id, key] of [
      ["cache-gib", "cache_gib"],
      ["local-workers", "local_workers"],
      ["description-workers", "description_workers"],
      ["video-height", "video_height"],
    ] as const) {
      if (!choice(id).focused) setChoiceValue(id, s.preferences[key]);
    }
  }
  const cooldown =
    s.cooldowns?.reduce((until, c) => Math.max(until, c.until), 0) || 0;
  $("description-cooldown").hidden = !cooldown;
  $("description-cooldown").textContent = cooldown
    ? `Image descriptions waiting until ${new Date(cooldown * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}. Local processing can continue.`
    : "";
  ($("open-processing") as HTMLButtonElement).disabled = !s.total;
  $("settings-runs").hidden = !s.run_count;
  $("settings-runs").textContent =
    `View ${number(s.run_count)} ${s.run_count === 1 ? "run" : "runs"}`;
  renderCoverage();
}
function retryNotice(r: { retried: number }) {
  feedback(
    "notice",
    r.retried
      ? `${count(r.retried, "step")} queued for retry. Completed evidence stays saved.`
      : "No steps currently need a retry.",
  );
}
const recoveryPrompt =
  "Recover Bookmark Library pages in browser_pending. Use an appropriate existing browser profile; in-app Browser does not share Chrome logins. Read rendered content and save it with browser_capture. Ask me only for personal login/consent. Keep challenges and partial extraction visible. Do not import more bookmarks or follow links inside pages.";
async function refresh() {
  let s: State;
  try {
    s = (await call("status")) as State;
  } catch (error) {
    connection(false);
    throw error;
  }
  renderState(s);
  connection(true);
  const signature = JSON.stringify(s.folder_inventory);
  if (signature !== folderSignature && !choice("folder").focused) {
    const c = await call("collections");
    const value = choice("folder").value;
    options(
      "folder",
      [
        { value: "", label: "All folders" },
        ...c.folders.map((f: { id: string; name: string }) => ({
          value: f.id,
          label: f.name,
        })),
      ],
      value,
    );
    folderSignature = signature;
    renderFilters();
    renderCoverage();
  }
}
function renderFilters() {
  const folder = choice("folder"),
    sort = choice("sort");
  const active = Number(!!folder.value) + Number(!!sort.value && !searching);
  $("filter-toggle").dataset.active = String(active > 0);
  $("filter-toggle").setAttribute(
    "aria-label",
    active ? `Filters, ${active} active` : "Filters",
  );
  $("filter-toggle").title = [
    folder.value ? folder.label : "",
    sort.value && !searching ? sort.label : "",
  ]
    .filter(Boolean)
    .join(" · ");
  ($("reset-filters") as HTMLButtonElement).disabled =
    !folder.value && !sort.value;
  sort.disabled = searching;
  sort.title = searching ? "Search results are ordered by relevance" : "";
  $("sort-hint").hidden = !searching;
  if ($("filters").matches(":popover-open")) positionFilters();
}
function positionFilters() {
  const trigger = $("filter-toggle").getBoundingClientRect();
  const popup = $("filters"),
    rect = popup.getBoundingClientRect();
  const bottom = $("library-scroll").getBoundingClientRect().bottom;
  popup.style.left = `${Math.max(16, Math.min(trigger.left, innerWidth - rect.width - 16))}px`;
  popup.style.top = `${Math.max(8, Math.min(trigger.bottom + 6, bottom - rect.height - 16))}px`;
}
function closeFilters(restoreFocus = false) {
  if (!$("filters").matches(":popover-open")) return;
  choice("folder").close();
  choice("sort").close();
  $("filters").hidePopover();
  if (restoreFocus) $("filter-toggle").focus({ preventScroll: true });
}
function updateSearchControl() {
  const query = input("query").value.trim();
  const busy = loading && query === pendingQuery;
  ($("search-submit") as HTMLButtonElement).disabled = busy;
  if (busy) $("search-submit").setAttribute("aria-busy", "true");
  else $("search-submit").removeAttribute("aria-busy");
  $("clear-search").style.visibility =
    query || searching || (loading && pendingQuery) ? "visible" : "hidden";
}
function clearSearch() {
  input("query").value = "";
  input("query").focus({ preventScroll: true });
  if (searching || (loading && pendingQuery)) {
    if (selected) showLibrary(false);
    void act(() => load());
  }
  updateSearchControl();
}
async function load(reset = true) {
  const query = input("query").value.trim(),
    key = JSON.stringify([
      query,
      choice("folder").value,
      choice("sort").value,
      reset ? 0 : next,
    ]);
  if (loading && key === loadingKey) return;
  loadingKey = key;
  pendingQuery = query;
  loading = true;
  const request = ++epoch;
  $("results").setAttribute("aria-busy", "true");
  ($(reset ? "search-submit" : "more") as HTMLButtonElement).disabled = true;
  $(reset ? "search-submit" : "more").setAttribute("aria-busy", "true");
  $("count").textContent = query ? "Searching…" : "Loading…";
  announce(query ? "Searching saved content" : "Loading bookmarks");
  try {
    let result;
    if (query) {
      result = await call("search", {
        query,
        folder: choice("folder").value || undefined,
        limit: 20,
      });
      while (result.pending) {
        await new Promise((r) => setTimeout(r, 1500));
        if (request !== epoch) return;
        result = await call("status", { query_id: result.query_id });
      }
      if (request !== epoch) return;
      rows = (result.matches || []).map((r: Row) => ({
        ...r,
        evidence_id: r.id,
        id: r.item || r.id,
      }));
      next = null;
    } else {
      result = await call("browse", {
        offset: reset ? 0 : next || 0,
        folder: choice("folder").value || undefined,
        sort: choice("sort").value || undefined,
      });
      if (request !== epoch) return;
      rows = reset
        ? result.items
        : [
            ...rows,
            ...result.items.filter(
              (r: Row) => !rows.some((old) => old.id === r.id),
            ),
          ];
      next = result.next_offset;
      scopedTotal = result.total;
    }
    searching = !!query;
    loadedQuery = query;
    renderFilters();
    renderList();
    if (reset) $("library-scroll").scrollTop = 0;
    $("refresh-content").hidden = true;
    announce(
      searching
        ? `${count(rows.length)} in top matches for ${query}.`
        : `${count(rows.length)} shown, ${number(scopedTotal)} in this view.`,
    );
  } catch (e) {
    if (request === epoch) {
      $("count").textContent = "Couldn’t load bookmarks";
      throw e;
    }
  } finally {
    if (request === epoch) {
      loading = false;
      $("results").setAttribute("aria-busy", "false");
      ($("search-submit") as HTMLButtonElement).disabled = false;
      ($("more") as HTMLButtonElement).disabled = false;
      $("search-submit").removeAttribute("aria-busy");
      $("more").removeAttribute("aria-busy");
      updateSearchControl();
      renderCount();
    }
  }
}
const thumbnailCache = new Map<string, string | null>();
function previewUrl(value?: string) {
  try {
    const url = new URL(value || "");
    return url.protocol === "https:" && url.hostname === "pbs.twimg.com"
      ? url.href
      : "";
  } catch {
    return "";
  }
}
const thumbnailObserver = new IntersectionObserver(
  (entries) => {
    for (const entry of entries)
      if (entry.isIntersecting) {
        thumbnailObserver.unobserve(entry.target);
        void thumbnail(entry.target as HTMLElement);
      }
  },
  { rootMargin: "200px" },
);
async function thumbnail(card: HTMLElement) {
  const id = card.dataset.id!;
  const slot = card.querySelector<HTMLElement>(".preview-slot");
  if (!slot) return;
  const unavailable = () => {
    const label = slot.querySelector("span");
    if (label) label.textContent = "Preview unavailable";
  };
  const show = (src: string, fallback?: () => void) => {
    if (!slot.isConnected || !gallery) return;
    const im = document.createElement("img");
    im.src = src;
    im.alt = "";
    im.width = 480;
    im.height = 320;
    im.loading = "lazy";
    im.referrerPolicy = "no-referrer";
    im.onerror = () => {
      im.remove();
      if (fallback) fallback();
      else unavailable();
    };
    slot.append(im);
  };
  const cached = async () => {
    try {
      if (!thumbnailCache.has(id)) {
        const r = await call("thumbnail", { item: id });
        thumbnailCache.set(id, r.image || null);
        if (thumbnailCache.size > 64)
          thumbnailCache.delete(thumbnailCache.keys().next().value!);
      }
      const image = thumbnailCache.get(id);
      if (image && slot.isConnected && gallery && !slot.querySelector("img"))
        show(image);
      else if (!image) unavailable();
    } catch {
      unavailable();
    }
  };
  if (slot.querySelector("img")) return;
  const remote = previewUrl(slot.dataset.preview);
  if (remote) show(remote, () => void cached());
  else await cached();
}
function cardHtml(r: Row) {
  const stateText =
    r.state && !["ready", "processing", "pending", "queued"].includes(r.state)
      ? r.state === "partial"
        ? "Some content needs attention"
        : stateLabel(r.state)
      : "";
  const kind =
      r.content_types?.join(" · ") || (r.kind ? evidenceLabel(r.kind) : "Post"),
    time = r.evidence ? timeRange(r.evidence) : "";
  const p = r.presentation,
    author =
      p?.name && /[\p{L}\p{N}\p{Emoji_Presentation}]/u.test(p.name)
        ? p.name
        : r.author
          ? "@" + r.author
          : "Saved post",
    avatar = previewUrl(p?.avatar),
    created = p?.created_at ? new Date(p.created_at) : null,
    posted =
      created && !isNaN(created.getTime())
        ? created.toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
          })
        : "",
    text = p?.text ?? r.text ?? r.title ?? r.excerpt ?? "Media bookmark";
  return `${gallery && r.has_media ? `<span class="preview-slot" data-preview="${esc(previewUrl(p?.preview))}"><span>${esc(kind.replace("Post · ", ""))}</span></span>` : ""}<span class="row-meta"><span class="row-identity"><span class="row-avatar" aria-hidden="true">${esc(author.replace(/^@/, "").slice(0, 1).toUpperCase())}${avatar ? `<img src="${esc(avatar)}" width="28" height="28" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</span><span class="author" title="${esc(r.author ? author + " · @" + r.author : author)}">${esc(author)}</span>${posted ? `<time class="row-date" datetime="${esc(p?.created_at)}">${esc(posted)}</time>` : ""}</span><span class="type">${esc(kind.replace(/^Post · /, ""))}</span></span><span class="body">${esc(text.replace(/\s+/g, " ").trim() || "Media bookmark")}</span><span class="row-footer">${r.folders?.length ? `<span>${esc(r.folders.map((f) => f.name).join(" · "))}</span>` : ""}${stateText ? `<span>${esc(stateText)}</span>` : ""}</span>${searching ? `<span class="match"><strong>Matched ${esc(evidenceLabel(r.kind).toLowerCase())}${time ? " · " + esc(time) : ""}</strong>${r.excerpt ? `<span class="match-snippet">${esc(r.excerpt)}</span>` : '<span class="match-snippet">Similar saved media. Open to read the available evidence.</span>'}</span>` : ""}`;
}
function renderList() {
  const list = $("results");
  list.className = gallery ? "gallery" : "";
  $("view-list").setAttribute("aria-pressed", String(!gallery));
  $("view-gallery").setAttribute("aria-pressed", String(gallery));
  thumbnailObserver.disconnect();
  const existing = new Map(
    Array.from(list.children).map((e) => [
      (e as HTMLElement).dataset.id!,
      e as HTMLButtonElement,
    ]),
  );
  const ids = new Set(rows.map((r) => r.id));
  for (const [id, el] of existing) if (!ids.has(id)) el.remove();
  rows.forEach((r, i) => {
    const card = existing.get(r.id) || document.createElement("button");
    card.className = "bookmark";
    card.dataset.id = r.id;
    card.setAttribute(
      "aria-label",
      `${r.author ? "@" + r.author : "Saved post"}: ${r.title || r.text?.slice(0, 160) || "Media bookmark"}${searching ? ". Matched " + evidenceLabel(r.kind).toLowerCase() + (r.evidence ? " " + timeRange(r.evidence) : "") : ""}`,
    );
    const html = cardHtml(r),
      signature = `${gallery}:${html}`;
    if (card.dataset.render !== signature) {
      card.innerHTML = html;
      card.dataset.render = signature;
      const avatar = card.querySelector<HTMLImageElement>(".row-avatar img");
      if (avatar) avatar.onerror = () => avatar.remove();
    }
    card.onclick = () => void act(() => openItem(r.id, r.evidence_id));
    if (list.children[i] !== card)
      list.insertBefore(card, list.children[i] || null);
    if (gallery) thumbnailObserver.observe(card);
  });
  renderCount();
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = searching
    ? `No matches for “${loadedQuery}”. Try another phrase${choice("folder").value ? " or clear the folder filter" : ""}.`
    : state?.total
      ? "No bookmarks in this folder yet."
      : state?.connected
        ? "No bookmarks yet. Choose Sync to import."
        : "Connect X in Settings to import your bookmarks.";
  $("more").hidden = next === null;
}
async function openItem(id: string, evidenceId?: string) {
  closeFilters();
  const request = ++detailEpoch;
  const card = Array.from($("results").children).find(
    (e) => (e as HTMLElement).dataset.id === id,
  );
  card?.setAttribute("aria-busy", "true");
  announce("Opening saved bookmark");
  try {
    const detail = await call("detail", { item: id, evidence_id: evidenceId });
    if (request !== detailEpoch) return;
    scroll = $("library-scroll").scrollTop;
    returnItem = id;
    selected = detail;
    currentMatch = evidenceId || "";
    $("list").hidden = true;
    $("detail").hidden = false;
    ($("source-details") as HTMLDetailsElement).open = false;
    renderDetail(false);
    $("reader-scroll").scrollTop = 0;
    const matched = $("evidence").querySelector<HTMLElement>(".matched");
    if (matched) {
      matched.focus({ preventScroll: true });
      matched.scrollIntoView({ block: "start" });
    } else $("detail-heading").focus({ preventScroll: true });
    announce("Bookmark opened");
  } catch (e) {
    if (request === detailEpoch) throw e;
  } finally {
    card?.removeAttribute("aria-busy");
  }
}
function relation(d: Detail, source?: string) {
  const ref = d.post?.referenced_tweets?.find((r) => r.id === source);
  return ref?.type === "replied_to"
    ? "Replying to"
    : ref?.type === "quoted"
      ? "Quoted post"
      : "Related post";
}
function evidenceHtml(e: Evidence, d: Detail, text = e.text, ids = [e.id]) {
  const matched = ids.includes(currentMatch),
    time = timeRange(e.metadata);
  const body = text
    ? ["description", "linked-document"].includes(e.kind)
      ? `<div class="markdown">${renderMarkdown(text)}</div>`
      : `<p class="evidence-text">${esc(text)}</p>`
    : `<p class="muted">Matched ${esc(evidenceLabel(e.kind).toLowerCase())}. Open the source to view the original.</p>`;
  return `<article data-evidence="${esc(e.id)}" ${matched ? 'class="matched" tabindex="-1"' : e.kind === "context" ? 'class="context-card"' : ""}>${matched ? '<span class="match-label">Search match</span>' : ""}${e.kind === "context" ? `<h3>${relation(d, e.metadata.source_id)}</h3>` : ""}${time ? `<div class="timestamp">${esc(time)}</div>` : ""}${body}${e.metadata.coverage?.length ? `<p class="muted">${esc(e.metadata.coverage.join(" "))}</p>` : ""}${e.metadata.url !== d.url ? sourceLink(e.metadata.url) : ""}</article>`;
}
function renderEvidence(d: Detail, preserve: boolean) {
  const root = $("evidence"),
    open = new Map(
      Array.from(
        root.querySelectorAll<HTMLDetailsElement>("details[data-kind]"),
      ).map((e) => [e.dataset.kind!, e.open]),
    );
  const y = $("reader-scroll").scrollTop,
    all = [...d.evidence];
  if (d.matched_evidence && !all.some((e) => e.id === d.matched_evidence!.id))
    all.push(d.matched_evidence);
  const groups = new Map<string, Evidence[]>();
  for (const e of all) {
    if (
      (e.kind === "text" && e.id !== currentMatch) ||
      (e.kind === "context" &&
        e.id !== currentMatch &&
        d.post_cards.some((card) => card.post.id === e.metadata.source_id)) ||
      (!e.text && e.id !== currentMatch)
    )
      continue;
    const list = groups.get(e.kind) || [];
    list.push(e);
    groups.set(e.kind, list);
  }
  root.innerHTML =
    [...groups]
      .map(([kind, entries]) => {
        const merged: {
          e: Evidence;
          text: string;
          ids: string[];
          end?: number;
        }[] = [];
        entries.sort(
          (a, b) =>
            (a.metadata.asset_id || a.metadata.source_id || "").localeCompare(
              b.metadata.asset_id || b.metadata.source_id || "",
            ) ||
            (a.metadata.start_seconds ?? a.metadata.start_char ?? 0) -
              (b.metadata.start_seconds ?? b.metadata.start_char ?? 0),
        );
        for (const e of entries) {
          const last = merged.at(-1),
            start = e.metadata.start_char;
          if (
            last &&
            ["context", "linked-document"].includes(kind) &&
            e.id !== currentMatch &&
            !last.ids.includes(currentMatch) &&
            last.e.metadata.source_id === e.metadata.source_id &&
            last.e.metadata.url === e.metadata.url &&
            start !== undefined &&
            last.end !== undefined &&
            start <= last.end &&
            start >= (last.e.metadata.start_char || 0)
          ) {
            last.text += e.text.slice(Math.max(0, last.end - start));
            last.end = Math.max(
              last.end,
              e.metadata.end_char ?? start + e.text.length,
            );
            last.ids.push(e.id);
          } else
            merged.push({
              e,
              text: e.text,
              ids: [e.id],
              end: e.metadata.end_char,
            });
        }
        const hasMatch = entries.some((e) => e.id === currentMatch),
          expanded =
            preserve && open.has(kind)
              ? open.get(kind)
              : hasMatch || kind === "context";
        const label =
          (
            {
              context: "Reply & quote",
              "linked-document": "Linked pages",
              description: "AI descriptions",
              transcript: "Transcripts",
              ocr: "Text in images",
              "alt-text": "Source alt text",
              text: "Matched post text",
            } as Record<string, string>
          )[kind] || evidenceLabel(kind);
        return `<details data-kind="${esc(kind)}" ${expanded ? "open" : ""}><summary>${esc(label)}</summary>${merged.map((m) => evidenceHtml(m.e, d, m.text, m.ids)).join("")}</details>`;
      })
      .join("") +
    (d.next_offset !== null
      ? '<button id="more-evidence">Load more</button>'
      : "");
  wireLinks(root);
  if (d.next_offset !== null)
    action("more-evidence", async () => {
      const request = detailEpoch,
        readingY = $("reader-scroll").scrollTop,
        more = await call("detail", { item: d.id, offset: d.next_offset });
      if (request !== detailEpoch || selected?.id !== d.id) return;
      selected = {
        ...selected,
        evidence: [
          ...selected.evidence,
          ...more.evidence.filter(
            (e: Evidence) => !selected!.evidence.some((old) => old.id === e.id),
          ),
        ],
        next_offset: more.next_offset,
      };
      renderEvidence(selected, true);
      (
        $("more-evidence") ||
        root.querySelector<HTMLElement>("details:last-of-type > summary")
      )?.focus({ preventScroll: true });
      $("reader-scroll").scrollTop = readingY;
      announce("More saved content loaded");
    });
  if (preserve) $("reader-scroll").scrollTop = y;
}
function renderDetail(preserve = true) {
  if (!selected) return;
  const d = selected;
  $("detail-heading").textContent = d.author ? "@" + d.author : "Saved post";
  $("detail-heading").classList.add("sr-only");
  renderTweets(
    $("detail-text"),
    d.post_cards,
    (url) => void act(() => openUrl(url)),
  );
  ($("source") as HTMLAnchorElement).href = /^https?:\/\//.test(d.url)
    ? d.url
    : "https://x.com";
  renderEvidence(d, preserve);
  const unfinished = d.stages.filter(
      (s) => !["complete", "unavailable"].includes(s.state),
    ),
    snapshot = d.metrics?.[0];
  const labels: Record<string, string> = {
    like_count: "Likes",
    retweet_count: "Reposts",
    reply_count: "Replies",
    quote_count: "Quotes",
    bookmark_count: "Bookmarks",
    impression_count: "Impressions",
  };
  $("detail-info").innerHTML = `${
    snapshot
      ? `<p class="muted">Engagement observed ${esc(date(snapshot.at))}</p><dl class="metrics">${Object.entries(
          snapshot.values,
        )
          .map(
            ([k, v]) =>
              `<div><dt>${esc(labels[k] || k.replaceAll("_", " "))}</dt><dd>${number(v)}</dd></div>`,
          )
          .join("")}</dl>`
      : '<p class="muted">No engagement snapshot saved yet.</p>'
  }${d.coverage?.length ? `<h3>Source coverage</h3>${d.coverage.map((c) => `<p class="muted">${esc(c)}</p>`).join("")}` : ""}${d.stages
    .filter((s) => s.state !== "complete")
    .map(
      (s) =>
        `<p class="muted">${esc(stageLabel(s.kind))} · ${esc(stateLabel(s.state))}${s.error ? ": " + esc(issueText(s.error)) : ""}</p>`,
    )
    .join(
      "",
    )}${unfinished.length ? `<p class="muted">${count(unfinished.length, "step")} unfinished. Saved evidence is already usable.</p>` : ""}<div class="actions">${d.retryable_steps ? `<button id="detail-retry">Retry ${count(d.retryable_steps, "step")}</button>` : ""}${d.clearable_bytes ? `<button id="clear-temp">Clear ${bytes(d.clearable_bytes)} of failed media…</button>` : ""}${d.stages.some((s) => s.state === "browser") ? '<button id="detail-recovery">Recover pages in ChatGPT</button>' : ""}</div>`;
  if (d.retryable_steps)
    action("detail-retry", async () => {
      retryNotice(await call("queue_control", { action: "retry", item: d.id }));
      await refreshDetail(d.id);
      await refresh();
    });
  if (d.clearable_bytes)
    action("clear-temp", async () => {
      $("clear-explanation").textContent =
        `Remove ${bytes(d.clearable_bytes)} of temporary input for this bookmark’s failed steps.`;
      showDialog("clear-dialog");
    });
  if (d.stages.some((s) => s.state === "browser"))
    action("detail-recovery", () => draft(recoveryPrompt));
}
async function refreshDetail(id: string, reloadEvidence = false) {
  const request = detailEpoch,
    fresh = await call("detail", {
      item: id,
      evidence_id: currentMatch || undefined,
    });
  if (request !== detailEpoch || selected?.id !== id) return;
  if (reloadEvidence) {
    const target = selected.evidence.length;
    while (fresh.next_offset !== null && fresh.evidence.length < target) {
      const page = await call("detail", {
        item: id,
        offset: fresh.next_offset,
      });
      if (request !== detailEpoch || selected?.id !== id) return;
      fresh.evidence.push(...page.evidence);
      fresh.next_offset = page.next_offset;
    }
  }
  const focused = document.activeElement?.id;
  selected = {
    ...fresh,
    evidence: reloadEvidence ? fresh.evidence : selected.evidence,
    next_offset: reloadEvidence ? fresh.next_offset : selected.next_offset,
  };
  renderDetail();
  if (focused && !document.getElementById(focused))
    $("source-details")
      .querySelector("summary")
      ?.focus({ preventScroll: true });
}
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  const query = input("query").value.trim();
  if (!query) {
    clearSearch();
    return;
  }
  if (query === loadedQuery && !loading) {
    if (selected) showLibrary();
    return;
  }
  closeFilters();
  if (selected) showLibrary(false);
  void act(() => load());
};
input("query").oninput = updateSearchControl;
input("query").onkeydown = (e) => {
  if (
    e.key === "Escape" &&
    (input("query").value || searching || (loading && pendingQuery))
  ) {
    e.preventDefault();
    clearSearch();
  }
};
$("clear-search").onclick = clearSearch;
$("filters").addEventListener("beforetoggle", (e) => {
  if ((e as ToggleEvent).newState === "open")
    requestAnimationFrame(() => {
      if (!$("filters").matches(":popover-open")) return;
      positionFilters();
      $("folder-trigger").focus({ preventScroll: true });
    });
});
$("filters").addEventListener("toggle", () => {
  const open = $("filters").matches(":popover-open");
  $("filter-toggle").setAttribute("aria-expanded", String(open));
  if (!open) {
    choice("folder").close();
    choice("sort").close();
  }
});
document.addEventListener("focusin", (e) => {
  if (
    e.target instanceof HTMLElement &&
    e.target !== document.body &&
    e.target !== $("filter-toggle") &&
    !$("filters").contains(e.target)
  )
    closeFilters();
});
window.addEventListener("resize", () => {
  if ($("filters").matches(":popover-open")) positionFilters();
});
$("close-filters").onclick = () => closeFilters(true);
$("reset-filters").onclick = () =>
  void act(async () => {
    choice("folder").value = "";
    choice("sort").value = "";
    renderFilters();
    await load();
  });
for (const [id, value] of [
  ["view-list", false],
  ["view-gallery", true],
] as const)
  $(id).onclick = () => {
    gallery = value;
    renderList();
  };
for (const id of ["folder", "sort"])
  choice(id).onChange = () => void act(() => load());
action("more", () => load(false));
function showLibrary(restoreFocus = true) {
  detailEpoch++;
  $("detail").hidden = true;
  clearTweets($("detail-text"));
  $("list").hidden = false;
  selected = undefined;
  const card = Array.from($("results").children).find(
    (e) => (e as HTMLElement).dataset.id === returnItem,
  ) as HTMLElement;
  if (restoreFocus) (card || $("query")).focus({ preventScroll: true });
  $("library-scroll").scrollTop = scroll;
}
$("back").onclick = () => showLibrary();
action("use", () =>
  draft(
    `Use my saved bookmark bookmark://item/${selected?.id}. Read its saved evidence and source coverage before making claims. Help me apply the useful ideas.`,
  ),
);
$("source").setAttribute("data-source", "");
wireLinks($("detail"));
$("settings").onclick = () => showDialog("settings-dialog");
$("issues").onclick = () => openReview("issues");
$("manage-runs").onclick = () => openReview("runs");
$("settings-runs").onclick = () => {
  dialog("settings-dialog").close();
  openReview("runs");
};
$("review-issues").onclick = () => openReview("issues");
$("review-runs").onclick = () => openReview("runs");
$("review-prev").onclick = () =>
  void act(() => loadReview(Math.max(0, reviewOffset - 20)));
$("review-next").onclick = () =>
  void act(() => loadReview(reviewNext ?? reviewOffset));
action("review-refresh", () => loadReview(reviewOffset));
action("retry-connection", refresh);
$("sync").onclick = () => showDialog("sync-dialog");
for (const [id, mode] of [
  ["sync-new", "new"],
  ["sync-full", "full"],
])
  action(id, async () => {
    await call("sync", {
      mode,
      process: false,
      limits: {
        items: Number(choice("import-items").value),
        x_requests: Number(choice("import-x-limit").value),
      },
    });
    dialog("sync-dialog").close();
    await refresh();
  });
action("pause", async () => {
  await call("queue_control", { action: "pause" });
  await refresh();
  feedback("notice", "Processing will pause after its current step.");
});
$("open-processing").onclick = () => {
  dialog("settings-dialog").close();
  showDialog("processing-dialog");
};
$("process-imported").onclick = () => showDialog("processing-dialog");
action("start-processing", async () => {
  const result = await call("process_saved", {
    mode: "repair",
    limits: {
      x_requests: Number(choice("process-x-limit").value),
      description_requests: Number(choice("process-description-limit").value),
    },
  });
  dialog("processing-dialog").close();
  await refresh();
  feedback(
    "notice",
    result.queued
      ? "Processing started."
      : "Existing work is already queued. Open Runs to resume it.",
  );
});
for (const [id, key] of [
  ["cache-gib", "cache_gib"],
  ["local-workers", "local_workers"],
  ["description-workers", "description_workers"],
  ["video-height", "video_height"],
] as const) {
  choice(id).onChange = () =>
    void act(async () => {
      const previous = state?.preferences[key] ?? 0;
      changingPreferences = true;
      choice(id).disabled = true;
      try {
        await call("processing_settings", { [key]: Number(choice(id).value) });
        await refresh();
      } catch (error) {
        setChoiceValue(id, previous);
        throw error;
      } finally {
        changingPreferences = false;
        choice(id).disabled = false;
        requestAnimationFrame(() => {
          if (
            document.activeElement === document.body &&
            dialog("settings-dialog").open
          )
            choice(id).focus();
        });
      }
    });
}
action("connect-x", async () => {
  const r = await call("connect_x");
  await openUrl(r.url);
});
action("connect-chatgpt", async () => {
  const r = await call("connect_chatgpt", { account: state?.chatgpt.active });
  await openUrl(r.url);
});
async function changeChatGPT(id: "chatgpt-account" | "model") {
  const control = choice(id),
    key = id === "model" ? "model" : "account";
  const previous =
    (id === "model" ? state?.chatgpt.model : state?.chatgpt.active) || "";
  if (!control.value) {
    control.value = previous;
    return;
  }
  changingChatGPT = true;
  choice("chatgpt-account").disabled = true;
  choice("model").disabled = true;
  try {
    await call("select_chatgpt", { [key]: control.value });
    await refresh();
  } catch (error) {
    control.value = previous;
    throw error;
  } finally {
    changingChatGPT = false;
    choice("chatgpt-account").disabled = false;
    renderModel();
    requestAnimationFrame(() => {
      if (
        document.activeElement !== document.body ||
        !dialog("settings-dialog").open
      )
        return;
      if (id === "model" && $("model").hidden)
        $("model-current").focus({ preventScroll: true });
      else choice(id).focus();
    });
  }
}
choice("chatgpt-account").onChange = () =>
  void act(() => changeChatGPT("chatgpt-account"));
$("retry-models").onclick = () => void loadModels(true);
action("reconnect-chatgpt", async () => {
  const r = await call("connect_chatgpt", { account: state?.chatgpt.active });
  await openUrl(r.url);
});
choice("model").onChange = () => void act(() => changeChatGPT("model"));
action("disconnect-chatgpt", async () => {
  const r = await call("disconnect_chatgpt");
  feedback(
    "notice",
    r.remote_revocation_confirmed
      ? "ChatGPT disconnected."
      : "Local access removed. Remote revocation was not confirmed; review connected apps in ChatGPT.",
  );
  await refresh();
});
action("usage", () => openUrl("https://chatgpt.com/settings/usage"));
let report: { path: string; name: string; blob: string } | undefined;
action("export", async () => {
  report = await call("export_diagnostics");
  $("report").hidden = false;
  $("report-message").textContent = "Report ready. Nothing sent.";
  $("report-open").hidden = !extensions.files;
  $("report-save").hidden = !app.getHostCapabilities()?.downloadFile;
  if ($("report-open").hidden && $("report-save").hidden)
    $("report-message").textContent =
      "Report saved on this Mac. Copy its path to open it in Finder.";
});
action("report-open", async () => {
  if (report && extensions.files) await extensions.files.open(report.path);
});
action("report-save", async () => {
  if (!report) return;
  const result = await app.downloadFile({
    contents: [
      {
        type: "resource",
        resource: {
          uri: `file:///${report.name}`,
          mimeType: "application/zip",
          blob: report.blob,
        },
      },
    ],
  });
  $("report-message").textContent = result.isError
    ? "Download cancelled. Report is still available."
    : "Report downloaded. Nothing sent.";
});
action("report-copy", async () => {
  if (report) {
    await navigator.clipboard.writeText(report.path);
    $("report-message").textContent = "File path copied.";
  }
});
action("browser-recovery", () => draft(recoveryPrompt));
action("confirm-clear", async () => {
  if (!selected) return;
  const id = selected.id,
    r = await call("clear_cache", { item: id });
  dialog("clear-dialog").close();
  feedback(
    "notice",
    r.removed_bytes
      ? `${bytes(r.removed_bytes)} cleared. Saved evidence retained.`
      : "No failed media can currently be cleared. It may be in use or already removed.",
  );
  await refreshDetail(id);
  await refresh();
});
for (const b of document.querySelectorAll<HTMLButtonElement>("[data-close]"))
  b.onclick = () => {
    for (const c of choices.values()) c.close();
    dialog(b.dataset.close!).close();
  };
const refreshButton = document.createElement("button");
refreshButton.id = "refresh-content";
refreshButton.className = "small-link";
refreshButton.textContent = "Saved content updated · Refresh";
refreshButton.hidden = true;
$("notice").after(refreshButton);
$("dismiss-feedback").onclick = () => {
  feedback("error", "", $("main"));
  feedback("notice", "", $("main"));
  refreshButton.hidden = true;
  (selected ? $("back") : $("query")).focus({ preventScroll: true });
};
action("refresh-content", async () => {
  if (selected) {
    await refreshDetail(selected.id, true);
    $("refresh-content").hidden = true;
  } else await load();
});
function theme(ctx: ReturnType<App["getHostContext"]>) {
  if (ctx?.theme) {
    applyDocumentTheme(ctx.theme);
    document.documentElement.dataset.theme = ctx.theme;
  }
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
  if (ctx?.displayMode) document.documentElement.dataset.mode = ctx.displayMode;
  const dimensions = ctx?.containerDimensions;
  if (dimensions) {
    const height =
      "height" in dimensions ? dimensions.height : dimensions.maxHeight;
    if (height && height > 0)
      document.documentElement.style.setProperty(
        "--host-height",
        `${height}px`,
      );
    else document.documentElement.style.removeProperty("--host-height");
  }
  if (ctx?.safeAreaInsets)
    for (const edge of ["top", "right", "bottom", "left"] as const)
      document.documentElement.style.setProperty(
        `--host-${edge}`,
        `${Math.max(0, ctx.safeAreaInsets[edge] || 0)}px`,
      );
}
// Reserve the same scrollbar space in the shared chrome and both content views.
const scrollbars = new ResizeObserver(() => {
  const port = $(selected ? "reader-scroll" : "library-scroll");
  document.documentElement.style.setProperty(
    "--scrollbar-size",
    `${port.offsetWidth - port.clientWidth}px`,
  );
});
scrollbars.observe($("library-scroll"));
scrollbars.observe($("reader-scroll"));
app.addEventListener("hostcontextchanged", theme);
app.ontoolresult = (r) => {
  if (
    !r.isError &&
    r.structuredContent &&
    "backend_version" in r.structuredContent
  )
    renderState(r.structuredContent as unknown as State);
};
await app.connect();
theme(app.getHostContext());
await act(async () => {
  await Promise.all([refresh(), load()]);
});
setInterval(() => {
  if (!document.hidden && !refreshing) {
    refreshing = true;
    void refresh()
      .catch(() => {})
      .finally(() => {
        refreshing = false;
      });
  }
}, 5000);
