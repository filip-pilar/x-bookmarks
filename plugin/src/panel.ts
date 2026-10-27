import {
  App,
  applyDocumentTheme,
  applyHostStyleVariables,
} from "@modelcontextprotocol/ext-apps";
import { OpenAIExtensions } from "@openai/mcp-extensions/app";
const app = new App({ name: "Bookmark Library", version: "0.7.0" }),
  extensions = new OpenAIExtensions(app);
const $ = (id: string) => document.getElementById(id)!;
const input = (id: string) => $(id) as HTMLInputElement;
const dialog = (id: string) => $(id) as HTMLDialogElement;
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
  metrics?: any;
};
type Stage = {
  id: string;
  item: string;
  kind: string;
  state: string;
  error?: string;
  next_at?: number;
  run: string;
};
type State = {
  connected: boolean;
  account?: { username: string };
  total: number;
  paused: boolean;
  stage_counts: { kind: string; state: string; count: number }[];
  stage_issues: Stage[];
  runs: { id: string; state: string; created: number }[];
  temporary_bytes: number;
  temporary_limit: number;
  storage_bytes: number;
  index_bytes: number;
  runtime_bytes: number;
  jobs: Stage[];
  job_counts: { kind: string; state: string; count: number }[];
  chatgpt: {
    connected: boolean;
    plan_enabled: boolean;
    model?: string;
    active?: string;
    accounts: { id: string; label: string }[];
  };
};
type Evidence = {
  id: string;
  kind: string;
  text: string;
  metadata: {
    url?: string;
    source_id?: string;
    start_seconds?: number;
    end_seconds?: number;
    generated?: boolean;
    coverage?: string[];
  };
};
type Detail = Omit<Row, "metrics"> & {
  evidence: Evidence[];
  next_offset: number | null;
  stages: Stage[];
  coverage: string[];
  metrics: { at: number; values: Record<string, number> }[];
};
let state: State | undefined,
  selected: Detail | undefined,
  rows: Row[] = [],
  next: number | null = null,
  gallery = false,
  searching = false,
  scroll = 0,
  epoch = 0;
const esc = (s: unknown) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ]!,
  );
const bytes = (n = 0) => `${(n / 1024 ** 3).toFixed(2)} GB`,
  date = (t: number) => new Date(t * 1000).toLocaleString();
function notice(t: string) {
  $("notice").textContent = t;
  $("notice").hidden = !t;
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
        .join(" ") || "Operation failed.",
    );
  return r.structuredContent;
}
async function act(fn: () => Promise<void>) {
  $("error").hidden = true;
  try {
    await fn();
  } catch (e) {
    $("error").textContent = (e as Error).message;
    $("error").hidden = false;
  }
}
async function openUrl(url: string) {
  if (!/^https?:\/\//.test(url)) throw new Error("Invalid source link.");
  await app.openLink({ url });
}
async function draft(text: string) {
  if (!extensions.message) {
    notice(text);
    return;
  }
  await extensions.message.send({
    role: "user",
    content: [{ type: "text", text }],
    _meta: { "openai/message": { target: "active", send: false } },
  });
  notice("Request drafted in ChatGPT.");
}
function renderState(s: State) {
  state = s;
  const pending =
    [...(s.stage_counts || []), ...(s.job_counts || [])]
      ?.filter((x) => !["complete", "unavailable"].includes(x.state))
      .reduce((n, x) => n + x.count, 0) || 0;
  $("progress").hidden = !pending;
  $("progress-text").textContent =
    `${s.paused ? "Paused · " : ""}${pending} processing steps remaining`;
  $("pause").hidden = s.paused;
  $("x-account").textContent = s.connected
    ? `@${s.account?.username}`
    : "Not connected";
  $("connect-x").textContent = s.connected ? "Reconnect X" : "Connect X";
  $("chatgpt-state").textContent = s.chatgpt.connected
    ? s.chatgpt.plan_enabled
      ? `Connected${s.chatgpt.model ? " · " + s.chatgpt.model : ""}`
      : "Plan permission required"
    : "Not connected";
  $("storage").textContent =
    `Temporary media: ${bytes(s.temporary_bytes)} / ${bytes(s.temporary_limit)}. Evidence/index: ${bytes(s.storage_bytes + s.index_bytes)}. Models/runtime: ${bytes(s.runtime_bytes)}.`;
  $("chatgpt-account").innerHTML =
    '<option value="">Choose account</option>' +
    s.chatgpt.accounts
      .map((a) => `<option value="${esc(a.id)}">${esc(a.label)}</option>`)
      .join("");
  input("chatgpt-account").value = s.chatgpt.active || "";
  if (s.chatgpt.model && !input("model").value)
    $("model").innerHTML =
      `<option value="${esc(s.chatgpt.model)}">${esc(s.chatgpt.model)}</option>`;
  $("runs").innerHTML =
    s.runs
      .filter((r) => r.state !== "complete")
      .map(
        (r) =>
          `<p class="actions"><span class="muted">${esc(date(r.created))} · ${esc(s.paused && r.state === "running" ? "paused" : r.state)}</span><button data-run="${esc(r.id)}" data-action="${r.state === "running" && !s.paused ? "pause" : "resume"}">${r.state === "running" && !s.paused ? "Pause" : "Resume"}</button><button data-run="${esc(r.id)}" data-action="retry">Retry failed</button></p>`,
      )
      .join("") || '<p class="muted">No processing runs.</p>';
  for (const b of $("runs").querySelectorAll<HTMLButtonElement>("button"))
    b.onclick = () =>
      act(async () => {
        await call("queue_control", {
          action: b.dataset.action,
          run: b.dataset.run,
        });
        await refresh();
      });
  renderIssues([
    ...(s.stage_issues || []),
    ...(s.jobs || []).filter((j) => ["failed", "waiting"].includes(j.state)),
  ]);
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
    }) as Record<string, string>
  )[kind] || kind.replaceAll("_", " ");
const issueText = (error = "") =>
  (
    ({
      chatgpt_sign_in_required: "Reconnect ChatGPT in Settings.",
      chatgpt_plan_consent_required: "Allow this app to use your ChatGPT plan.",
      chatgpt_model_selection_required:
        "Choose a description model in Settings.",
      temporary_storage_full:
        "Temporary storage is full. Finish pending descriptions or clear selected failed media.",
      temporary_budget_too_small:
        "This media needs more temporary space than the configured budget allows.",
      source_retrieval_required:
        "The original must be retrieved again before this step can finish.",
      authentication_or_challenge:
        "This page needs browser access or personal sign-in.",
      incomplete_extraction:
        "The readable page content could not be captured completely.",
      partial_browser_capture:
        "Some page content still needs browser recovery.",
    }) as Record<string, string>
  )[error] || error;
function renderIssues(issues: Stage[]) {
  $("issue-list").innerHTML =
    issues
      .map(
        (s) =>
          `<div><p>${esc(stageLabel(s.kind))} · ${esc(s.state)}</p><p class="muted">${esc(issueText(s.error))}${s.state === "waiting" && s.next_at ? " · next check " + esc(date(s.next_at)) : ""}</p><div class="actions">${s.item ? `<button data-item="${esc(s.item)}">Open bookmark</button>` : ""}${s.state === "browser" ? '<span class="muted">Desktop browser recovery needed</span>' : s.state === "unavailable" ? "" : `<button data-retry="${esc(s.item || "")}" data-run="${esc(s.run)}">Retry failed</button>`}</div></div>`,
      )
      .join("") || "<p>No failures need attention.</p>";
  for (const b of $("issue-list").querySelectorAll<HTMLButtonElement>(
    "[data-item]",
  ))
    b.onclick = () =>
      act(async () => {
        dialog("issues-dialog").close();
        await openItem(b.dataset.item!);
      });
  for (const b of $("issue-list").querySelectorAll<HTMLButtonElement>(
    "[data-retry]",
  ))
    b.onclick = () =>
      act(async () => {
        await call("queue_control", {
          action: "retry",
          item: b.dataset.retry || undefined,
          run: b.dataset.retry ? undefined : b.dataset.run,
        });
        await refresh();
      });
}
async function refresh() {
  renderState(await call("status"));
}
async function load(reset = true) {
  const request = ++epoch,
    query = input("query").value.trim();
  $("count").textContent = query
    ? "Searching saved content…"
    : "Loading bookmarks…";
  searching = !!query;
  (input("sort") as unknown as HTMLSelectElement).disabled = searching;
  let result;
  if (query) {
    result = await call("search", {
      query,
      folder: input("folder").value || undefined,
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
      id: r.item || r.id,
    }));
    next = null;
  } else {
    result = await call("browse", {
      offset: reset ? 0 : next || 0,
      folder: input("folder").value || undefined,
      sort: input("sort").value || undefined,
    });
    if (request !== epoch) return;
    rows = reset ? result.items : [...rows, ...result.items];
    next = result.next_offset;
  }
  renderList();
}
function renderList() {
  $("results").className = gallery ? "gallery" : "";
  $("results").innerHTML = rows
    .map(
      (r) =>
        `<button class="bookmark" data-id="${esc(r.id)}">${gallery ? `<div class="placeholder" data-thumbnail="${esc(r.id)}">${r.has_media ? "Media available at source" : "Saved bookmark"}</div>` : ""}<span class="author">${r.author ? "@" + esc(r.author) : "Saved post"}</span><div class="body">${esc(r.excerpt || r.text || r.title)}</div><span class="muted">${esc(r.state || "Search match")}${r.metrics?.like_count !== undefined ? " · " + r.metrics.like_count.toLocaleString() + " likes" : ""}</span></button>`,
    )
    .join("");
  $("count").textContent = searching
    ? `${rows.length} matching bookmarks`
    : `${rows.length} shown${state ? " · " + state.total.toLocaleString() + " saved" : ""}`;
  $("empty").hidden = rows.length > 0;
  $("empty").textContent = searching
    ? "No matching bookmarks. Try different words."
    : state?.total
      ? "No bookmarks in this filter."
      : "No bookmarks yet. Connect your accounts in Settings, then start an import.";
  $("more").hidden = next === null;
  for (const b of $("results").querySelectorAll<HTMLButtonElement>("[data-id]"))
    b.onclick = () => act(() => openItem(b.dataset.id!));
  if (gallery) void thumbnails();
}
async function thumbnails() {
  for (const el of Array.from(
    $("results").querySelectorAll<HTMLElement>("[data-thumbnail]"),
  ).slice(-30)) {
    try {
      const r = await call("thumbnail", { item: el.dataset.thumbnail });
      if (r.image && el.isConnected) {
        const im = document.createElement("img");
        im.src = r.image;
        im.alt = "Cached bookmark image";
        el.replaceWith(im);
      }
    } catch {}
  }
}
async function openItem(id: string) {
  scroll = window.scrollY;
  selected = await call("detail", { item: id });
  $("list").hidden = true;
  $("detail").hidden = false;
  renderDetail();
  window.scrollTo(0, 0);
}
function renderDetail() {
  if (!selected) return;
  const d = selected;
  $("detail-author").textContent =
    `${d.author ? "@" + d.author : "Saved post"} · ${d.state}`;
  $("detail-text").textContent = d.text || d.title;
  const groups = new Map<string, Evidence[]>();
  for (const e of d.evidence) {
    if (!e.text || e.kind === "text") continue;
    const list = groups.get(e.kind) || [];
    list.push(e);
    groups.set(e.kind, list);
  }
  const labels: Record<string, string> = {
    context: "Reply and quote context",
    "alt-text": "Source alt text",
    "linked-document": "Linked pages",
    description: "Visual descriptions",
    transcript: "Transcript",
    ocr: "Text in images",
    summary: "Summary",
  };
  $("evidence").innerHTML =
    [...groups]
      .map(
        ([kind, entries]) =>
          `<details ${kind === "context" ? "open" : ""}><summary>${esc(labels[kind] || kind)}</summary>${entries.map((e) => `<article><div class="muted">${e.metadata.start_seconds !== undefined ? esc(e.metadata.start_seconds.toFixed(1) + "–" + e.metadata.end_seconds?.toFixed(1) + " seconds") : ""}${e.metadata.generated ? " · AI description" : ""}</div><p class="evidence-text">${esc(e.text)}</p>${e.metadata.coverage?.length ? `<p class="muted">${esc(e.metadata.coverage.join(" "))}</p>` : ""}${e.metadata.url ? `<button class="small-link" data-url="${esc(e.metadata.url)}">Open source</button>` : ""}</article>`).join("")}</details>`,
      )
      .join("") +
    (d.next_offset !== null
      ? '<button id="more-evidence">More saved content</button>'
      : "");
  for (const b of $("evidence").querySelectorAll<HTMLButtonElement>(
    "[data-url]",
  ))
    b.onclick = () => act(() => openUrl(b.dataset.url!));
  const moreButton = $("more-evidence");
  if (moreButton)
    moreButton.onclick = () =>
      act(async () => {
        const more = await call("detail", {
          item: d.id,
          offset: d.next_offset,
        });
        selected = {
          ...d,
          evidence: [...d.evidence, ...more.evidence],
          next_offset: more.next_offset,
        };
        renderDetail();
      });
  $("detail-info").innerHTML =
    d.stages
      .filter((s) => s.state !== "complete")
      .map(
        (s) =>
          `<p class="muted">${esc(stageLabel(s.kind))}: ${esc(s.state)}${s.error ? " · " + esc(issueText(s.error)) : ""}</p>`,
      )
      .join("") +
    `<p class="muted">${esc(d.coverage?.join("\n") || "")}</p>${
      d.metrics
        ?.slice(0, 3)
        .map(
          (m) =>
            `<p class="muted">Observed ${esc(date(m.at))}</p><p>${Object.entries(
              m.values,
            )
              .map(
                ([k, v]) =>
                  esc(k.replaceAll("_", " ")) +
                  ": " +
                  Number(v).toLocaleString(),
              )
              .join(" · ")}</p>`,
        )
        .join("") || '<p class="muted">No engagement snapshot saved yet.</p>'
    }<p class="muted">${d.stages?.filter((s) => s.state !== "complete").length || 0} processing steps unfinished. Original media may no longer be cached.</p><button id="detail-retry">Retry failed steps</button><button id="clear-temp">Clear failed temporary media</button>`;
  $("clear-temp").onclick = () =>
    act(async () => {
      const r = await call("clear_cache", { item: d.id });
      notice(
        `${bytes(r.removed_bytes)} cleared. Saved evidence retained; retry may need media retrieval.`,
      );
      await refresh();
    });
  $("detail-retry").onclick = () =>
    act(async () => {
      await call("queue_control", { action: "retry", item: d.id });
      notice("Failed steps queued; completed evidence retained.");
    });
}
$("search-form").onsubmit = (e) => {
  e.preventDefault();
  void act(() => load());
};
$("filter-toggle").onclick = () => {
  $("filters").hidden = !$("filters").hidden;
  $("filter-toggle").setAttribute(
    "aria-expanded",
    String(!$("filters").hidden),
  );
};
$("view-toggle").onclick = () => {
  gallery = !gallery;
  $("view-toggle").textContent = gallery ? "List" : "Gallery";
  $("view-toggle").setAttribute("aria-pressed", String(gallery));
  renderList();
};
for (const id of ["folder", "sort"]) $(id).onchange = () => act(() => load());
$("more").onclick = () => act(() => load(false));
$("back").onclick = () => {
  $("detail").hidden = true;
  $("list").hidden = false;
  selected = undefined;
  window.scrollTo(0, scroll);
};
$("use").onclick = () =>
  act(() =>
    draft(
      `Use my saved bookmark bookmark://item/${selected?.id}. Read its saved evidence and source coverage before making claims. Help me apply the useful ideas.`,
    ),
  );
$("source").onclick = () => act(() => openUrl(selected!.url));
$("settings").onclick = () => dialog("settings-dialog").showModal();
$("issues").onclick = () => dialog("issues-dialog").showModal();
$("sync").onclick = () => dialog("sync-dialog").showModal();
for (const [id, mode] of [
  ["sync-new", "new"],
  ["sync-full", "full"],
])
  $(id).onclick = () =>
    act(async () => {
      await call("sync", { mode });
      dialog("sync-dialog").close();
      await refresh();
    });
$("pause").onclick = () =>
  act(async () => {
    await call("queue_control", { action: "pause" });
    await refresh();
  });
$("connect-x").onclick = () =>
  act(async () => {
    const r = await call("connect_x");
    await openUrl(r.url);
  });
$("connect-chatgpt").onclick = () =>
  act(async () => {
    const r = await call("connect_chatgpt");
    await openUrl(r.url);
  });
$("chatgpt-account").onchange = () =>
  act(async () => {
    if (input("chatgpt-account").value) {
      await call("select_chatgpt", { account: input("chatgpt-account").value });
      await refresh();
    }
  });
$("load-models").onclick = () =>
  act(async () => {
    const r = await call("chatgpt_models");
    $("model").innerHTML =
      '<option value="">Select a model</option>' +
      r.models
        .map(
          (m: { id: string; name: string }) =>
            `<option value="${esc(m.id)}">${esc(m.name)}</option>`,
        )
        .join("");
    input("model").value = state?.chatgpt.model || "";
  });
$("model").onchange = () =>
  act(async () => {
    if (input("model").value) {
      await call("select_chatgpt", { model: input("model").value });
      await refresh();
    }
  });
$("disconnect-chatgpt").onclick = () =>
  act(async () => {
    const r = await call("disconnect_chatgpt");
    notice(
      r.remote_revocation_confirmed
        ? "ChatGPT disconnected."
        : "Local access removed. Remote revocation was not confirmed; review connected apps in ChatGPT.",
    );
    await refresh();
  });
$("usage").onclick = () =>
  act(() => openUrl("https://chatgpt.com/settings/usage"));
$("export").onclick = () =>
  act(async () => {
    const r = await call("export_diagnostics");
    $("report").textContent = r.summary + "\nSaved locally: " + r.path;
  });
$("browser-recovery").onclick = () =>
  act(() =>
    draft(
      "Recover Bookmark Library pages in browser_pending. Use an appropriate existing browser profile; in-app Browser does not share Chrome logins. Read rendered content and save it with browser_capture. Ask me only for personal login/consent. Keep challenges and partial extraction visible. Do not import more bookmarks or follow links inside pages.",
    ),
  );
for (const b of document.querySelectorAll<HTMLButtonElement>("[data-close]"))
  b.onclick = () => dialog(b.dataset.close!).close();
function theme(ctx: ReturnType<App["getHostContext"]>) {
  if (ctx?.theme) applyDocumentTheme(ctx.theme);
  if (ctx?.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}
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
  await refresh();
  const c = await call("collections");
  $("folder").innerHTML =
    '<option value="">All folders</option>' +
    c.folders
      .map(
        (f: { id: string; name: string }) =>
          `<option value="${esc(f.id)}">${esc(f.name)}</option>`,
      )
      .join("");
  await load();
});
setInterval(() => {
  if (!document.hidden) void refresh().catch(() => {});
}, 5000);
