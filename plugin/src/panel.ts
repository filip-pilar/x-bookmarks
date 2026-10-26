import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import { OpenAIExtensions } from "@openai/mcp-extensions/app";
const app = new App({ name: "Bookmark Library", version: "0.5.1" });
const extensions = new OpenAIExtensions(app);
const $ = (id: string) => document.getElementById(id)!;
const dialog = (id: string) => $(id) as HTMLDialogElement;
type Job = {
  kind: string;
  state: string;
  error?: string;
  payload: string;
  next_at?: number;
};
type IssueKind = "sample" | "retryable" | "unavailable" | "limitation";
type LibraryState = {
  connected: boolean;
  account?: { username?: string };
  total: number;
  counts: Record<string, number>;
  searchable: number;
  vectors: number;
  content: Record<
    string,
    { sources: number; bookmarks: number; segments: number }
  >;
  issues: { message: string; bookmarks: number; kind: IssueKind }[];
  issue_counts: Record<IssueKind, number>;
  issue_groups: number;
  jobs: Job[];
  job_counts: { kind: string; state: string; count: number }[];
  processing_remaining: number;
  paused: boolean;
  pause_reason?: string;
  sample_only: boolean;
  last_inventory?: {
    at: number;
    mode?: string;
    unresolved_folder_ids?: string[];
    unseen_retained?: number;
    endpoint_exhausted: boolean;
    unavailable_records?: number;
    folders_complete: boolean;
    folders_checked?: boolean;
  };
  last_processed?: number;
  storage_bytes: number;
  folders: number;
  folder_inventory?: { complete: boolean; at: number };
  enrichment?: { complete: number; pending: number; stale: number };
  library_path: string;
  usage: { requests: number; records: number };
  processing?: { item: string; title: string; stage?: string; detail?: string };
};
let state: LibraryState | undefined;
let busy = false;
let actionError = "";
let connectionError = "";
const number = (n = 0) => n.toLocaleString();
const plural = (n: number, one: string, many = one + "s") =>
  `${number(n)} ${n === 1 ? one : many}`;
const date = (seconds?: number) =>
  seconds
    ? new Date(seconds * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not yet";
const text = (id: string, value: string) => {
  if ($(id).textContent !== value) $(id).textContent = value;
};
function message(id: string, value: string) {
  text(id, value);
  $(id).hidden = !value;
}
async function call(name: string, args: Record<string, unknown> = {}) {
  const r = await app.callServerTool({ name, arguments: args });
  if (r.isError)
    throw new Error(
      r.content
        ?.filter((c: { type: string; text?: string }) => c.type === "text")
        .map((c: { type: string; text?: string }) => c.text)
        .join(" ") || "Operation failed.",
    );
  return r.structuredContent;
}
async function refresh() {
  try {
    const s = await call("status");
    connectionError = "";
    render(s as unknown as LibraryState);
  } catch (e) {
    connectionError = `Couldn’t refresh the library. ${(e as Error).message}`;
    message("error", connectionError);
  }
}
function issueLabel(raw: string) {
  const labels: Record<string, string> = {
    "Thread coverage not verified in the bounded sample.":
      "Threads not expanded",
    "Quoted/parent posts not fetched in the bounded sample.":
      "Quoted and parent posts not fetched",
    "Linked source: Source missing from cached sample; network disabled.":
      "Linked pages not fetched",
    "X Article images retained as a gallery; inline positions were not supplied by X.":
      "Article images saved as a gallery",
  };
  return labels[raw] || raw;
}
function render(s: LibraryState) {
  state = s;
  const pending = s.job_counts.filter((j) =>
    ["queued", "running", "waiting"].includes(j.state),
  );
  const running = pending.some((j) => j.count > 0);
  const failedCount = s.job_counts
    .filter((j) => j.state === "failed")
    .reduce((sum, j) => sum + j.count, 0);
  const sync = s.jobs.find(
    (j) =>
      j.kind === "sync" && ["queued", "running", "waiting"].includes(j.state),
  );
  const waiting = s.jobs.find((j) => j.state === "waiting");
  const partial = s.counts.partial || 0;
  const failed = s.counts.error || 0;
  const stages: Record<string, string> = {
    context: "Fetching thread context",
    downloading: "Downloading sources",
    transcribing: "Transcribing audio",
    preparing_audio: "Preparing audio",
    reading_images: "Reading image text",
    indexing: "Indexing for ChatGPT",
    saving: "Saving results",
  };
  const processingStage = s.processing?.stage;
  const pausing = s.paused && s.jobs.some((j) => j.state === "running");
  const waitingForRetry =
    !!waiting && !s.jobs.some((j) => j.state === "running");
  text(
    "account",
    s.connected
      ? s.account?.username
        ? "@" + s.account.username
        : "Connected to X"
      : "Not connected",
  );
  $("dot").classList.toggle("on", s.connected);
  text("saved", number(s.total));
  text("searchable", number(s.searchable));
  text(
    "remaining",
    number(Math.max(s.processing_remaining, s.total - s.searchable)),
  );
  let title = s.total
    ? s.sample_only
      ? "Sample imported"
      : partial || failed
        ? "Searchable · see source details"
        : "Ready in ChatGPT"
    : "Not imported";
  let detail = "";
  if (s.searchable < s.total) title = "Processing incomplete";
  if (failedCount) title = "Retry needed";
  if (running) {
    title = s.paused
      ? pausing
        ? "Pausing after this step…"
        : "Paused"
      : waitingForRetry
        ? "Waiting to retry"
        : sync
          ? "Fetching bookmarks"
          : stages[processingStage || ""] || "Preparing processing…";
    if (sync && !s.paused) {
      let phase: { phase?: string; returned?: number } = {};
      try {
        phase = JSON.parse(sync.payload);
      } catch {
        /* The first import checkpoint may not exist yet. */
      }
      detail =
        phase.phase === "bookmarks"
          ? `${number(phase.returned || 0)} received`
          : phase.phase === "finish"
            ? "Saving sync results…"
            : "Checking folders…";
      if (phase.phase !== "bookmarks")
        title = phase.phase === "finish" ? "Finishing sync" : "Syncing folders";
    } else if (s.processing?.title && !s.paused && !waitingForRetry) {
      detail =
        s.processing.title.slice(0, 85) +
        (s.processing.title.length > 85 ? "…" : "");
    }
    if (waitingForRetry && !s.paused && waiting?.next_at)
      detail = `Retry at ${date(waiting.next_at)}`;
  }
  text("status-title", title);
  message("detail", detail);
  const progress = $("progress") as HTMLProgressElement;
  progress.hidden = !running;
  if (sync) progress.removeAttribute("value");
  else {
    progress.max = Math.max(s.total, s.processing_remaining, 1);
    progress.value = Math.max(0, s.total - s.processing_remaining);
  }
  $("workflow").hidden = !running;
  $("step-fetch").classList.toggle("complete", !sync);
  $("step-fetch").setAttribute("aria-current", sync ? "step" : "false");
  $("step-process").setAttribute("aria-current", !sync ? "step" : "false");
  message(
    "progress-note",
    running
      ? s.paused
        ? "Completed work is kept. Resume when ready."
        : sync
          ? "X API credits apply. Processing starts after the bookmark check."
          : `${plural(s.processing_remaining, "bookmark")} left · ${processingStage === "context" ? "X API credits apply" : "local processing"}`
      : "",
  );
  text(
    "main",
    !s.connected
      ? "Connect X"
      : s.paused && running
        ? "Resume"
        : running
          ? "Working…"
          : failedCount
            ? "Review failed work"
            : s.sample_only
              ? "Import all bookmarks"
              : "Sync now",
  );
  ($("main") as HTMLButtonElement).disabled =
    busy || (s.connected && running && !s.paused);
  $("main").hidden = s.connected && running && !s.paused;
  $("pause").hidden = !running || s.paused;
  for (const id of ["pause", "retry", "disconnect", "schedule"])
    ($(id) as HTMLButtonElement).disabled = busy;
  ($("retry") as HTMLButtonElement).disabled = busy || running;
  $("content").hidden = !s.searchable;
  text("index-note", `${number(s.vectors)} search segments`);
  const content = s.content;
  text("text-count", number(content.text?.bookmarks));
  text("image-count", number(content.image?.sources));
  text("video-count", number(content.video?.sources));
  message(
    "video-moments",
    content.video?.segments ? `${number(content.video.segments)} moments` : "",
  );
  text("transcript-count", number(content.transcript?.sources));
  const hasIssues = s.issues.length > 0 || failedCount > 0;
  const retryable = (s.issue_counts?.retryable || 0) + failed + failedCount;
  $("attention").hidden = !hasIssues;
  text(
    "attention-title",
    retryable
      ? "Processing needs attention"
      : s.issue_counts?.sample
        ? "Sample · extra sources skipped"
        : "Source notes available",
  );
  message(
    "coverage-intro",
    s.searchable
      ? "Searchable content is ready to use. These details describe its source coverage."
      : "",
  );
  const rows = s.issues.map((issue) => ({
    message: issueLabel(issue.message),
    detail: plural(issue.bookmarks, "bookmark"),
    kind: issue.kind || "retryable",
  }));
  for (const job of s.jobs.filter(
    (j) => ["failed", "waiting"].includes(j.state) && j.error,
  )) {
    if (!rows.some((row) => row.message === job.error))
      rows.unshift({
        message: job.error!,
        detail: job.kind === "sync" ? "X import" : "Local processing",
        kind: "retryable",
      });
  }
  const list = document.createDocumentFragment();
  const groups: Record<IssueKind, string> = {
    retryable: "Can be retried",
    sample: "Skipped in the sample",
    unavailable: "Unavailable at the source",
    limitation: "Source limitations",
  };
  for (const [kind, label] of Object.entries(groups)) {
    const groupRows = rows.filter((row) => row.kind === kind);
    if (!groupRows.length) continue;
    const heading = document.createElement("h3");
    heading.className = "issue-heading";
    heading.textContent = label;
    const group = document.createElement("ul");
    group.className = "issue-list";
    for (const row of groupRows) {
      const li = document.createElement("li");
      li.textContent = row.message;
      const meta = document.createElement("span");
      meta.textContent = row.detail;
      li.append(meta);
      group.append(li);
    }
    list.append(heading, group);
  }
  $("issues").replaceChildren(list);
  text(
    "issues-note",
    s.issue_groups > s.issues.length
      ? `${number(s.issue_groups - s.issues.length)} more source details are available through ChatGPT.`
      : s.issue_counts?.sample
        ? "The full import will fetch the skipped sources. Counts may overlap."
        : "Counts may overlap. Source limitations do not need retries.",
  );
  $("retry").hidden = !retryable;
  text(
    "settings-account",
    s.connected
      ? s.account?.username
        ? "@" + s.account.username
        : "Connected"
      : "Not connected",
  );
  $("disconnect").hidden = !s.connected;
  text(
    "storage",
    s.storage_bytes >= 1e9
      ? `${(s.storage_bytes / 1e9).toFixed(1)} GB`
      : `${(s.storage_bytes / 1e6).toFixed(1)} MB`,
  );
  text(
    "folders",
    s.folder_inventory?.complete
      ? plural(s.folders, "folder")
      : s.folders
        ? `${number(s.folders)} · sync incomplete`
        : "Not synced",
  );
  text(
    "folder-checked",
    s.folder_inventory?.complete
      ? `Checked ${date(s.folder_inventory.at)}`
      : "Not checked yet",
  );
  ($("check-folders") as HTMLButtonElement).disabled =
    busy ||
    !s.connected ||
    s.job_counts.some(
      (j) =>
        ["queued", "running", "waiting", "failed"].includes(j.state) &&
        j.count > 0,
    );
  text("last-processed", date(s.last_processed));
  text(
    "enrichment-status",
    `${s.enrichment?.complete || 0} of ${s.total} · Generated in ChatGPT`,
  );
  ($("enrich") as HTMLButtonElement).disabled = busy || !s.enrichment?.pending;
  text(
    "last-sync",
    s.last_inventory
      ? `Synced ${date(s.last_inventory.at)}`
      : "No full import yet",
  );
  $("last-sync").hidden = !s.last_inventory;
  text(
    "usage",
    `${plural(s.usage.requests, "X API request")} · ${plural(s.usage.records, "primary record")} recorded. Excludes expanded resources and billing adjustments; not a cost total.`,
  );
  text(
    "coverage",
    s.last_inventory?.endpoint_exhausted
      ? "Reached the end of the bookmark pages X returned. " +
          (s.last_inventory.unavailable_records
            ? "Some records were unavailable. "
            : "") +
          ((s.last_inventory.folders_checked ??
          s.last_inventory.folders_complete)
            ? "Folders checked."
            : "Saved folder memberships reused.")
      : s.last_inventory?.mode === "new"
        ? `New bookmarks checked. ${s.last_inventory.folders_checked ? "Folders checked." : "Saved folder memberships reused."}`
        : "Full-library coverage has not been checked.",
  );
  if (s.last_inventory?.unresolved_folder_ids?.length)
    text(
      "folders",
      `${plural(s.folders, "folder")} · ${s.last_inventory.unresolved_folder_ids.length} unavailable items`,
    );
  if (s.last_inventory?.unseen_retained)
    text(
      "coverage",
      $("coverage").textContent +
        ` ${plural(s.last_inventory.unseen_retained, "saved bookmark")} absent from X’s returned pages; retained locally.`,
    );
  text("library-path", s.library_path);
  message("error", actionError || connectionError || s.pause_reason || "");
}
async function act(fn: () => Promise<void>) {
  if (busy) return;
  const trigger = document.activeElement;
  busy = true;
  actionError = "";
  message("notice", "");
  if (state) render(state);
  try {
    await fn();
    await refresh();
  } catch (e) {
    actionError = (e as Error).message;
  } finally {
    busy = false;
    if (state) render(state);
    else message("error", actionError);
    if (
      document.activeElement === document.body &&
      trigger instanceof HTMLButtonElement
    ) {
      const parent = trigger.closest("dialog");
      if (!trigger.disabled && !trigger.hidden && (!parent || parent.open))
        trigger.focus();
    }
  }
}
$("confirm-cancel").onclick = () => dialog("confirm-dialog").close("cancel");
$("confirm-action").onclick = () => dialog("confirm-dialog").close("confirm");
function confirmAction(
  title: string,
  body: string,
  action: string,
  destructive = false,
): Promise<boolean> {
  const d = dialog("confirm-dialog");
  text("confirm-title", title);
  text("confirm-body", body);
  text("confirm-action", action);
  $("confirm-action").classList.toggle("danger", destructive);
  d.returnValue = "cancel";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "confirm"), {
      once: true,
    });
    d.showModal();
  });
}
$("import-cancel").onclick = () => dialog("import-dialog").close("cancel");
$("import-start").onclick = () => dialog("import-dialog").close("confirm");
function reviewImport(): Promise<boolean> {
  const first = state?.sample_only;
  text("import-title", first ? "Import bookmarks" : "Sync bookmarks");
  text("import-start", first ? "Start import" : "Start sync");
  text(
    "import-description",
    first
      ? "Import all bookmarks X makes available, then prepare them for search."
      : "Checks new bookmarks. Folders refresh if last checked over 24 hours ago. X reads may use credits.",
  );
  const d = dialog("import-dialog");
  d.returnValue = "cancel";
  return new Promise((resolve) => {
    d.addEventListener("close", () => resolve(d.returnValue === "confirm"), {
      once: true,
    });
    d.showModal();
  });
}
async function openExternal(url: string) {
  try {
    await app.openLink({ url });
  } catch {
    message("error", "Couldn’t open the link. Try again from Settings.");
  }
}
$("billing").onclick = () => void openExternal("https://console.x.com/");
$("pricing").onclick = () =>
  void openExternal("https://docs.x.com/x-api/getting-started/pricing");
$("main").onclick = () =>
  act(async () => {
    if (!state) return;
    if (!state.connected) {
      const r = await call("connect_x");
      await app.openLink({ url: String(r?.url) });
      return;
    }
    const pending = state.job_counts.some(
      (j) => ["queued", "running", "waiting"].includes(j.state) && j.count,
    );
    if (state.paused && pending) {
      await call("queue_control", { action: "resume" });
      return;
    }
    if (state.job_counts.some((j) => j.state === "failed" && j.count)) {
      dialog("coverage-dialog").showModal();
      return;
    }
    if (!(await reviewImport())) return;
    await call("sync");
  });
$("pause").onclick = () =>
  act(async () => {
    await call("queue_control", { action: "pause" });
  });
$("retry").onclick = () =>
  act(async () => {
    if (
      !(await confirmAction(
        "Retry failed work?",
        "Retries failed downloads and processing, reusing completed work. X context requests may use credits. Sample omissions and source limitations are excluded.",
        "Retry",
      ))
    )
      return;
    dialog("coverage-dialog").close();
    await call("queue_control", { action: "retry" });
  });
$("settings").onclick = () => dialog("settings-dialog").showModal();
$("check-folders").onclick = () =>
  act(async () => {
    if (
      !(await confirmAction(
        "Check X folders?",
        "Updates folders for saved bookmarks only. X reads may use credits.",
        "Check folders",
      ))
    )
      return;
    await call("sync_folders");
  });
$("review").onclick = () => dialog("coverage-dialog").showModal();
$("disconnect").onclick = () =>
  act(async () => {
    if (
      !(await confirmAction(
        "Disconnect your X account?",
        "Stops syncing. Your local library is kept.",
        "Disconnect",
        true,
      ))
    )
      return;
    await call("disconnect_x");
    dialog("settings-dialog").close();
  });
$("enrich").onclick = () =>
  act(async () => {
    const prompt =
      "Use Bookmark Library's library skill to generate and save evidence-grounded AI summaries, tags and entities for already processed bookmarks that need enrichment. Read all evidence pages and preserve source limitations. Do not import bookmarks, sync X or fetch missing sources.";
    if (!extensions.message) {
      message(
        "notice",
        "Ask ChatGPT to enrich my already processed bookmarks without importing or fetching sources.",
      );
      return;
    }
    await extensions.message.send({
      role: "user",
      content: [{ type: "text", text: prompt }],
      _meta: { "openai/message": { target: "active", send: false } },
    });
    dialog("settings-dialog").close();
    message(
      "notice",
      "Request drafted in ChatGPT. Send it to enrich saved bookmarks.",
    );
  });
$("schedule").onclick = () =>
  act(async () => {
    if (!extensions.message) {
      dialog("settings-dialog").close();
      message("notice", "Ask ChatGPT: Help me schedule Bookmark Library sync.");
      return;
    }
    await extensions.message.send({
      role: "user",
      content: [
        {
          type: "text",
          text: "Help me manage automatic Bookmark Library sync. Check for an existing schedule first. Explain that sync checks new bookmarks, refreshes folders if their last check is over 24 hours old, and reuses saved sources; X requests may still use credits. Ask for my preferred cadence, then use the available automation tool to create or update it. The Mac and ChatGPT must be available. Keep it quiet unless action is needed. Do not start an import now.",
        },
      ],
      _meta: { "openai/message": { target: "active", send: false } },
    });
    dialog("settings-dialog").close();
    message(
      "notice",
      "Request drafted in ChatGPT. Send it to set up or change the schedule.",
    );
  });
for (const button of document.querySelectorAll<HTMLButtonElement>(
  "[data-close]",
))
  button.onclick = () => dialog(button.dataset.close!).close();
for (const d of document.querySelectorAll("dialog"))
  d.addEventListener("click", (e) => {
    if (e.target !== d) return;
    const box = d.getBoundingClientRect();
    if (
      e.clientX < box.left ||
      e.clientX > box.right ||
      e.clientY < box.top ||
      e.clientY > box.bottom
    )
      d.close("cancel");
  });
function theme(ctx: ReturnType<App["getHostContext"]>) {
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}
app.addEventListener("hostcontextchanged", theme);
app.ontoolresult = (r) => {
  if (r.isError) {
    actionError =
      r.content
        ?.filter((c: { type: string; text?: string }) => c.type === "text")
        .map((c: { type: string; text?: string }) => c.text)
        .join(" ") || "Setup required.";
    message("error", actionError);
  } else if (r.structuredContent)
    render(r.structuredContent as unknown as LibraryState);
};
await app.connect();
theme(app.getHostContext());
await refresh();
setInterval(() => {
  if (!document.hidden && !busy) void refresh();
}, 5000);
