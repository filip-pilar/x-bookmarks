import { Choice } from "./choice.js";
import "./activity.css";

type Run = {
  id: string;
  label: string;
  created: number;
  items: number;
  pending_steps: number;
  retryable_steps: number;
  media_processing: boolean;
  activity_state: string;
  limits?: {
    reason: string;
    x_limit: number;
    x_used: number;
    description_limit: number;
    description_used: number;
    item_limit: number;
  };
};
type Item = {
  id: string;
  title: string;
  author: string;
  status: string;
  kind: string;
  stage_state: string;
  run: string;
  run_state: string;
  error?: string;
  next_at?: number;
  eligible: number;
  issues: number;
  presentation: {
    name?: string;
    preview?: string;
    avatar?: string;
    text?: string;
  };
  content_types: string[];
};
type Snapshot = {
  view: string;
  items: (Item | Run)[];
  visible_items: Item[];
  total: number;
  offset: number;
  next_offset: number | null;
  counts: Record<string, number>;
  paused: boolean;
  import:
    | (Run & {
        unfinished: boolean;
        returned: number;
        phase: string;
        mode: string;
        process: boolean;
        job_state: string;
        error?: string;
        next_at?: number;
      })
    | null;
  processing_runs: Run[];
  processing_run_count: number;
  processing_running: boolean;
  processing_pausing: boolean;
  coverage: {
    folder_inventory?: { complete?: boolean; in_progress?: boolean };
    full_inventory_at?: number;
    last_inventory?: { partial_pages?: number; unavailable_records?: number };
  };
};
type State = {
  connected: boolean;
  x_auth_error?: string;
  unprocessed: number;
  preferences: { x_requests: number; description_requests: number };
};
type Dependencies = {
  call: (name: string, args?: Record<string, unknown>) => Promise<any>;
  act: (fn: () => Promise<void>, button?: HTMLButtonElement) => Promise<void>;
  refresh: () => Promise<void>;
  openBookmark: (id: string) => Promise<void>;
  settings: () => void;
  recover: (item?: string) => Promise<void>;
  export: () => Promise<void>;
  escape: (value: unknown) => string;
  issue: (error?: string | null) => string;
  stage: (kind: string) => string;
  preview: (url?: string) => string;
};
const $ = (id: string) => document.getElementById(id)!;
const button = (id: string) => $(id) as HTMLButtonElement;
const n = (value = 0) => value.toLocaleString();
const plural = (value: number) =>
  `${n(value)} bookmark${value === 1 ? "" : "s"}`;
const clock = (value: number) =>
  new Date(value * 1000).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
const icons: Record<string, string> = {
  activity: '<path d="m2 12 4 0 3-8 5 16 3-8h5"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6"/>',
  play: '<path d="m8 4 12 8-12 8Z"/>',
  pause: '<path d="M8 4v16M16 4v16"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  attention: '<circle cx="12" cy="12" r="9"/><path d="M12 7v6m0 3v1"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  settings:
    '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="2"/><circle cx="15" cy="17" r="2"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  retry:
    '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M4 12a8 8 0 0 0 14 6"/>',
};
export const icon = (name: string) =>
  `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.activity}</svg>`;
const filters = [
  ["all", "All"],
  ["active", "Active"],
  ["waiting", "Waiting"],
  ["attention", "Needs attention"],
  ["complete", "Complete"],
  ["partial", "Partial"],
] as const;

export class Activity {
  visible = false;
  private state?: State;
  private data?: Snapshot;
  private view = "all";
  private offset = 0;
  private query = "";
  private request = 0;
  private loading = false;
  private initialized = false;
  private online = false;
  private items = new Map<string, Item>();
  private expanded = "";
  private detailRequest = 0;
  private changed = false;
  private choices = new Map<string, Choice>();
  private popupTrigger?: HTMLElement;
  private scroll = 0;
  constructor(private d: Dependencies) {
    const section = document.createElement("section");
    section.id = "activity";
    section.hidden = true;
    section.setAttribute("aria-labelledby", "activity-heading");
    section.innerHTML = `<div id="activity-scroll" class="scrollport" tabindex="-1">
   <div class="activity-heading"><h2 id="activity-heading" tabindex="-1">Activity</h2><div class="actions"><button id="activity-history" class="quiet icon-button">${icon("clock")}<span>History</span></button><button id="activity-more" class="quiet icon-only" aria-label="Activity options" aria-expanded="false">${icon("more")}</button></div></div>
   <section class="activity-section" aria-labelledby="activity-import-heading"><div class="activity-section-head"><div class="activity-title">${icon("download")}<h3 id="activity-import-heading">Import</h3><span id="activity-import-state" class="muted"></span></div><div class="actions"><button id="activity-import-pause" class="icon-button" hidden>${icon("pause")}Pause</button><button id="activity-import-toggle" class="icon-button" aria-expanded="false">Import bookmarks ${icon("chevron")}</button></div></div>
    <p id="activity-import-count" class="activity-count">Loading…</p><div class="activity-section-foot"><p id="activity-import-note" class="muted"></p><button id="activity-coverage" class="quiet">Coverage</button></div>
   </section>
   <section class="activity-section" aria-labelledby="activity-processing-heading"><div class="activity-section-head"><div class="activity-title">${icon("activity")}<h3 id="activity-processing-heading">Processing</h3><span id="activity-processing-state" class="muted"></span></div><div class="actions activity-processing-actions"><button id="activity-new-work" hidden>Process new bookmarks</button><button id="activity-process" class="primary icon-button" disabled>${icon("play")}<span>Process bookmarks</span></button></div></div>
    <p id="activity-counts" class="activity-count">Loading…</p><progress id="activity-progress" max="100" value="0" aria-label="Fully processed bookmarks"></progress><div class="activity-section-foot"><p class="muted">Imported text is searchable.</p><button id="activity-process-limits" class="quiet icon-button" aria-expanded="false">${icon("settings")}Limits</button></div><p id="activity-limit-summary" class="muted"></p>
   </section>
   <div id="activity-feedback" role="status" class="activity-feedback"></div>
   <div class="activity-queue-head"><nav id="activity-filters" aria-label="Work status">${filters.map(([key, label]) => `<button data-view="${key}" class="quiet" aria-pressed="${key === "all"}">${label}<span data-count="${key}">0</span></button>`).join("")}</nav><form id="activity-search" role="search"><label class="sr-only" for="activity-query">Find in activity</label>${icon("search")}<input id="activity-query" type="search" placeholder="Find bookmark" maxlength="300"><button class="quiet icon-only" type="submit" aria-label="Search activity">${icon("chevron")}</button></form></div>
   <div class="activity-results-state"><span id="activity-results-label" role="status"></span><button id="activity-update" class="quiet" disabled>Refresh</button></div>
   <div id="activity-rows" aria-busy="true"></div><div id="activity-empty" class="activity-empty" hidden><p></p><button id="activity-retry" hidden>Try again</button></div>
   <div class="activity-footer"><span id="activity-page" tabindex="-1"></span><div class="actions"><button id="activity-prev" class="quiet icon-only" aria-label="Previous page" disabled><span class="back-icon">${icon("chevron")}</span></button><button id="activity-next" class="quiet icon-only" aria-label="Next page" disabled>${icon("chevron")}</button></div></div>
  </div>
  <div id="activity-import-options" class="activity-popover" popover="auto" aria-labelledby="activity-import-options-heading"><div class="activity-popover-head"><h3 id="activity-import-options-heading">Import bookmarks</h3><button class="quiet icon-only" data-close-activity aria-label="Close import options">${icon("close")}</button></div><div id="activity-sign-in" hidden><p>Connect X to import bookmarks.</p><button id="activity-connect">Open settings</button></div><div id="activity-import-form"><label id="activity-scope-label" for="activity-scope-trigger">Import</label><div id="activity-scope" class="choice"></div><p class="muted">Uses X credits. Processing starts separately.</p><details class="activity-import-limits"><summary>Limits</summary><label id="activity-x-label" for="activity-x-trigger">X request limit</label><div id="activity-x" class="choice"></div></details><p id="activity-import-budget" class="muted"></p><button id="activity-start-import" class="primary">Import bookmarks</button><button id="activity-recheck" class="quiet" hidden>Recheck from newest</button></div></div>
  <div id="activity-limits-options" class="activity-popover" popover="auto" aria-labelledby="activity-limits-heading"><div class="activity-popover-head"><h3 id="activity-limits-heading">Processing limits</h3><button class="quiet icon-only" data-close-activity aria-label="Close limits">${icon("close")}</button></div><label id="activity-process-x-label" for="activity-process-x-trigger">X requests</label><div id="activity-process-x" class="choice"></div><label id="activity-descriptions-label" for="activity-descriptions-trigger">Image descriptions</label><div id="activity-descriptions" class="choice"></div><p class="muted">For the next processing run. X requests use credits; descriptions use your ChatGPT plan. These are not spending caps.</p></div>
  <div id="activity-coverage-options" class="activity-popover" popover="auto" aria-labelledby="activity-coverage-heading"><div class="activity-popover-head"><h3 id="activity-coverage-heading">Import coverage</h3><button class="quiet icon-only" data-close-activity aria-label="Close coverage">${icon("close")}</button></div><dl id="activity-coverage-values" class="storage-list"></dl><p class="muted">X may not return every historical bookmark.</p></div>
  <div id="activity-more-options" class="activity-popover activity-menu" popover="auto" aria-label="Activity options"><button id="activity-diagnostics">Export diagnostics</button><button id="activity-settings">Processing settings</button></div>`;
    $("search-form").before(section);
    for (const [id, values] of [
      [
        "activity-scope",
        [
          { value: "100", label: "Next 100" },
          { value: "500", label: "Next 500" },
          { value: "0", label: "All available" },
        ],
      ],
      ...["activity-x", "activity-process-x", "activity-descriptions"].map(
        (id) => [
          id,
          [25, 100, 500, 1000, 0].map((v) => ({
            value: String(v),
            label: v ? n(v) : "No limit",
          })),
        ],
      ),
    ] as [string, { value: string; label: string }[]][]) {
      const c = new Choice($(id), values);
      c.value = "100";
      this.choices.set(id, c);
      c.onChange = () => this.budgetCopy();
    }
    const on = (id: string, fn: () => Promise<void>) =>
      (button(id).onclick = () => void this.perform(fn, button(id)));
    button("activity-import-toggle").onclick = () =>
      this.popover("activity-import-options", button("activity-import-toggle"));
    button("activity-process-limits").onclick = () =>
      this.popover(
        "activity-limits-options",
        button("activity-process-limits"),
      );
    button("activity-coverage").onclick = () =>
      this.popover("activity-coverage-options", button("activity-coverage"));
    button("activity-more").onclick = () =>
      this.popover("activity-more-options", button("activity-more"));
    button("activity-connect").onclick = () => {
      this.closePopovers();
      this.d.settings();
    };
    button("activity-settings").onclick = () => {
      this.closePopovers();
      this.d.settings();
    };
    on("activity-diagnostics", async () => {
      this.closePopovers();
      await this.d.export();
    });
    on("activity-start-import", () => this.startImport(false));
    on("activity-recheck", () => this.startImport(true));
    on("activity-import-pause", async () => {
      await this.d.call("queue_control", {
        action: "pause",
        run: this.data!.import!.id,
      });
      await this.update();
    });
    on("activity-process", () => this.process());
    on("activity-new-work", () => this.startProcessing());
    button("activity-history").onclick = () =>
      this.filter(this.view === "history" ? "all" : "history");
    $("activity-filters")
      .querySelectorAll<HTMLButtonElement>("button")
      .forEach((b) => (b.onclick = () => this.filter(b.dataset.view!)));
    $("activity-search").onsubmit = (e) => {
      e.preventDefault();
      const query = ($("activity-query") as HTMLInputElement).value.trim();
      if (query === this.query) return;
      this.query = query;
      this.offset = 0;
      void this.load(true);
    };
    $("activity-query").addEventListener("input", () => {
      if (!($("activity-query") as HTMLInputElement).value && this.query) {
        this.query = "";
        this.offset = 0;
        void this.load(true);
      }
    });
    on("activity-update", () => this.load(true));
    on("activity-retry", () => this.load(true));
    on("activity-prev", async () => {
      this.offset = Math.max(0, this.offset - 20);
      await this.load(true, true);
    });
    on("activity-next", async () => {
      this.offset = this.data?.next_offset ?? this.offset;
      await this.load(true, true);
    });
    section
      .querySelectorAll<HTMLButtonElement>("[data-close-activity]")
      .forEach((b) => (b.onclick = () => this.closePopovers(true)));
    section.querySelectorAll<HTMLElement>("[popover]").forEach((p) =>
      p.addEventListener("toggle", () => {
        if (!p.matches(":popover-open")) {
          this.choices.forEach((c) => c.close());
          this.popupTrigger?.setAttribute("aria-expanded", "false");
        }
      }),
    );
    window.addEventListener("resize", () => this.closePopovers());
    $("activity-scroll").addEventListener(
      "scroll",
      () => this.closePopovers(),
      { passive: true },
    );
  }
  private value(id: string) {
    return Number(this.choices.get(id)!.value);
  }
  private text(id: string, text: string) {
    if ($(id).textContent !== text) $(id).textContent = text;
  }
  private disable(id: string, disabled = false) {
    const b = button(id);
    b.disabled = disabled || b.hasAttribute("aria-busy");
  }
  private updateActions() {
    $("activity")
      .querySelectorAll<HTMLButtonElement>("[data-requires-online]")
      .forEach((b) => {
        b.disabled =
          !this.online ||
          b.hasAttribute("aria-busy") ||
          b.dataset.unavailable === "true";
      });
  }
  private async perform(fn: () => Promise<void>, b?: HTMLButtonElement) {
    if (b?.disabled) return;
    const focus = document.activeElement;
    if (b) {
      b.disabled = true;
      b.setAttribute("aria-busy", "true");
    }
    this.text("activity-feedback", "");
    $("activity-feedback").setAttribute("role", "status");
    $("activity")
      .querySelectorAll("[data-activity-error]")
      .forEach((e) => e.remove());
    try {
      await fn();
    } catch (e) {
      this.text("activity-feedback", (e as Error).message);
      $("activity-feedback").setAttribute("role", "alert");
      const popup = $("activity").querySelector<HTMLElement>(
        "[popover]:popover-open",
      );
      if (popup) {
        const error = document.createElement("p");
        error.dataset.activityError = "";
        error.className = "error";
        error.setAttribute("role", "alert");
        error.textContent = (e as Error).message;
        popup.append(error);
        error.scrollIntoView({ block: "nearest" });
      }
    } finally {
      if (b) {
        b.disabled = false;
        b.removeAttribute("aria-busy");
      }
      this.renderSummary();
      if (this.view === "history" && this.data)
        this.renderHistory(this.data, false);
      this.updateActions();
      if (
        document.activeElement === document.body &&
        focus instanceof HTMLElement &&
        focus.isConnected
      )
        focus.focus({ preventScroll: true });
    }
  }
  private popover(id: string, trigger: HTMLElement) {
    const p = $(id);
    const open = p.matches(":popover-open");
    this.closePopovers();
    if (open) return;
    this.popupTrigger = trigger;
    p.showPopover();
    trigger.setAttribute("aria-expanded", "true");
    const rect = trigger.getBoundingClientRect(),
      bounds = $("main").getBoundingClientRect();
    p.style.maxHeight = `${Math.max(120, bounds.bottom - bounds.top - 24)}px`;
    p.style.left = `${Math.max(12, Math.min(rect.right - p.offsetWidth, innerWidth - p.offsetWidth - 12))}px`;
    p.style.top = `${Math.max(bounds.top + 8, Math.min(rect.bottom + 8, bounds.bottom - p.offsetHeight - 8))}px`;
    Array.from(
      p.querySelectorAll<HTMLElement>(
        ".choice button, input, button:not([data-close-activity])",
      ),
    )
      .find((e) => e.getClientRects().length && !e.matches(":disabled"))
      ?.focus({ preventScroll: true });
  }
  closePopovers(focus = false) {
    this.choices.forEach((c) => c.close());
    $("activity")
      .querySelectorAll<HTMLElement>("[popover]")
      .forEach((p) => {
        if (p.matches(":popover-open")) p.hidePopover();
      });
    if (focus) this.popupTrigger?.focus({ preventScroll: true });
  }
  setOnline(online: boolean) {
    this.online = online;
    this.renderSummary();
    this.updateActions();
  }
  setState(s: State) {
    this.state = s;
    if (!this.initialized) {
      for (const id of ["activity-x", "activity-process-x"])
        this.setChoice(id, s.preferences.x_requests);
      this.setChoice(
        "activity-descriptions",
        s.preferences.description_requests,
      );
      this.initialized = true;
    }
    this.renderSummary();
  }
  private setChoice(id: string, value: number) {
    const c = this.choices.get(id)!;
    if (!c.options.some((o) => o.value === String(value)))
      c.options.push({ value: String(value), label: n(value) });
    c.value = String(value);
  }
  show() {
    this.visible = true;
    $("activity").hidden = false;
    $("activity-scroll").scrollTop = this.scroll;
    void this.load(!this.data);
  }
  focusItem(id: string) {
    $("activity-rows")
      .querySelector<HTMLElement>(
        `[data-item="${CSS.escape(id)}"] .activity-item`,
      )
      ?.focus({ preventScroll: true });
  }
  hide() {
    this.scroll = $("activity-scroll").scrollTop;
    this.visible = false;
    this.request++;
    this.loading = false;
    this.closePopovers();
    $("activity").hidden = true;
  }
  poll() {
    if (this.visible && !this.loading) void this.load();
  }
  private filter(view: string) {
    if (this.loading && view === this.view) return;
    this.view = view;
    this.offset = 0;
    this.expanded = "";
    this.query = "";
    ($("activity-query") as HTMLInputElement).value = "";
    void this.load(true);
  }
  private budgetCopy() {
    const x = this.value("activity-x"),
      px = this.value("activity-process-x"),
      descriptions = this.value("activity-descriptions");
    this.text(
      "activity-import-budget",
      `${x ? `Up to ${n(x)} ${this.data?.import?.unfinished ? "more " : ""}X requests.` : "No X request limit."} Request counts are not a spending cap.`,
    );
    this.text(
      "activity-limit-summary",
      `Next run: ${px ? n(px) : "unlimited"} X requests · ${descriptions ? n(descriptions) : "unlimited"} image descriptions`,
    );
  }
  private renderSummary() {
    const s = this.data,
      state = this.state;
    this.budgetCopy();
    $("activity-sign-in").hidden = !!state?.connected;
    $("activity-import-form").hidden = !state?.connected;
    this.disable("activity-start-import", !this.online || !state?.connected);
    if (!s) return;
    const imp = s.import,
      unfinished = !!imp?.unfinished,
      active =
        unfinished && ["running", "pausing"].includes(imp!.activity_state);
    this.text(
      "activity-import-state",
      active
        ? imp!.activity_state === "pausing"
          ? "Pausing…"
          : "Running"
        : unfinished
          ? imp!.activity_state === "attention"
            ? "Needs attention"
            : "Paused"
          : s.coverage.full_inventory_at
            ? "Checked"
            : "Idle",
    );
    this.text("activity-import-count", `${plural(s.counts.total)} saved`);
    const reason = imp?.limits?.reason;
    this.text(
      "activity-import-note",
      unfinished
        ? reason === "items"
          ? `${n(imp!.limits!.item_limit)}-bookmark limit reached`
          : reason === "x"
            ? "X request limit reached"
            : imp!.job_state === "waiting" && active
              ? `Next attempt ${clock(imp!.next_at || 0)}`
              : imp!.job_state === "failed"
                ? this.d.issue(imp!.error)
                : imp!.phase === "folders"
                  ? "Checking folders"
                  : active
                    ? `${n(imp!.returned)} returned this run`
                    : "Progress saved"
        : s.coverage.last_inventory?.partial_pages ||
            s.coverage.last_inventory?.unavailable_records
          ? "Last import incomplete"
          : s.coverage.full_inventory_at
            ? "Available bookmarks imported"
            : "Import your saved X posts",
    );
    this.text(
      "activity-import-toggle",
      unfinished
        ? "Continue import"
        : s.counts.total
          ? "Import more"
          : "Import bookmarks",
    );
    this.disable("activity-import-toggle", active || !this.online);
    $("activity-import-pause").hidden = !active;
    this.disable(
      "activity-import-pause",
      !this.online || imp?.activity_state === "pausing",
    );
    this.text(
      "activity-start-import",
      unfinished ? "Continue import" : "Import bookmarks",
    );
    $("activity-recheck").hidden = unfinished || !s.coverage.full_inventory_at;
    const f = s.coverage.folder_inventory;
    $("activity-coverage-values").innerHTML =
      `<div><dt>Bookmarks</dt><dd>${this.d.escape(s.coverage.last_inventory?.partial_pages || s.coverage.last_inventory?.unavailable_records ? "Last import incomplete" : unfinished ? "Import unfinished" : s.coverage.full_inventory_at ? "Available bookmarks imported" : s.counts.total ? "Import unfinished" : "Not imported")}</dd></div><div><dt>Folders</dt><dd>${f?.in_progress ? "Check unfinished" : f?.complete ? "Checked" : f ? "Partially checked" : "Not checked"}</dd></div>`;
    const c = s.counts,
      r = s.processing_runs[0],
      running = c.active > 0 || s.processing_running;
    this.text(
      "activity-processing-state",
      s.processing_pausing && !s.processing_running
        ? "Pausing…"
        : running
          ? "Running"
          : c.attention
            ? "Needs attention"
            : c.waiting || c.retrying
              ? "Idle"
              : c.partial
                ? "Partial"
                : c.total
                  ? "Complete"
                  : "Idle",
    );
    this.text(
      "activity-counts",
      [
        [c.complete, "complete"],
        [c.active, "active"],
        [c.waiting + c.retrying, "waiting"],
        [c.attention, "need attention"],
        [c.partial, "partial"],
      ]
        .filter(([value]) => Number(value) > 0)
        .map(([value, label]) => `${n(Number(value))} ${label}`)
        .join(" · ") || "No bookmarks yet",
    );
    const progress = $("activity-progress") as HTMLProgressElement;
    progress.max = Math.max(1, c.total);
    progress.value = c.complete;
    progress.setAttribute(
      "aria-valuetext",
      `${plural(c.complete)} fully processed of ${n(c.total)}`,
    );
    $("activity-new-work").hidden = !c.eligible || !s.processing_run_count;
    this.text("activity-new-work", `Process ${n(c.eligible)} new`);
    this.disable("activity-new-work", !this.online);
    const b = button("activity-process");
    let label: string,
      glyph = "play",
      disabled = false;
    if (s.processing_run_count > 1) {
      label = "Manage runs";
      glyph = "clock";
    } else if (r?.activity_state === "pausing") {
      label = "Pausing…";
      glyph = "pause";
      disabled = true;
    } else if (r?.activity_state === "running") {
      label = "Pause";
      glyph = "pause";
    } else if (r?.limits?.reason) {
      label = "Review limit";
      glyph = "settings";
    } else if (r?.pending_steps) {
      label = "Resume processing";
    } else if (c.eligible) {
      label = `Process ${plural(c.eligible)}`;
    } else if (c.attention) {
      label = "Review issues";
      glyph = "attention";
    } else {
      label = "Nothing waiting";
      disabled = true;
    }
    const html = `${icon(glyph)}<span>${label}</span>`;
    if (b.innerHTML !== html) b.innerHTML = html;
    b.classList.toggle("primary", glyph === "play" && !disabled);
    this.disable("activity-process", disabled || !this.online);
    filters.forEach(([key]) => {
      const count =
        key === "all"
          ? c.total
          : key === "waiting"
            ? c.waiting + c.retrying
            : c[key];
      this.textCount(key, count);
    });
    $("activity-filters").hidden = this.view === "history";
    $("activity-search").hidden = this.view === "history";
    this.text(
      "activity-heading",
      this.view === "history" ? "Activity history" : "Activity",
    );
    this.text(
      "activity-history",
      this.view === "history" ? "Back to activity" : "History",
    );
    $("activity-filters")
      .querySelectorAll<HTMLButtonElement>("button")
      .forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.view === this.view)),
      );
  }
  private textCount(key: string, value: number) {
    const node = $("activity-filters").querySelector(`[data-count="${key}"]`)!;
    node.textContent = n(value);
  }
  async load(replace = false, page = false) {
    const request = ++this.request;
    this.loading = true;
    $("activity-rows").setAttribute("aria-busy", "true");
    if (replace) {
      button("activity-prev").disabled = true;
      button("activity-next").disabled = true;
    }
    try {
      const data = (await this.d.call("activity", {
        view: this.view,
        query: this.query,
        offset: this.offset,
        limit: 20,
        visible_ids:
          replace || this.view === "history" ? [] : [...this.items.keys()],
      })) as Snapshot;
      if (request !== this.request || !this.visible) return;
      this.data = data;
      this.renderSummary();
      button("activity-retry").hidden = true;
      if (this.view === "history") {
        this.renderHistory(data, replace);
        this.changed = false;
      } else {
        const rows = data.items as Item[],
          same =
            JSON.stringify(rows.map((r) => r.id)) ===
            JSON.stringify([...this.items.keys()]);
        if (replace || !this.items.size) {
          this.items = new Map(rows.map((r) => [r.id, r]));
          this.expanded = "";
          this.changed = false;
          $("activity-rows").replaceChildren();
          rows.forEach((r) => this.renderRow(r));
        } else {
          this.changed = !same;
          for (const row of data.visible_items) {
            this.items.set(row.id, row);
            this.renderRow(row);
          }
        }
      }
      this.updateActions();
      this.offset = data.offset;
      $("activity-empty").hidden = !!(this.view === "history"
        ? data.items.length
        : this.items.size);
      $("activity-empty").querySelector("p")!.textContent = this.query
        ? "No matching bookmarks."
        : this.view === "history"
          ? "No runs yet."
          : this.view === "all"
            ? "Import bookmarks to get started."
            : `No ${this.view === "attention" ? "bookmarks need attention" : this.view === "complete" ? "completed bookmarks" : this.view === "partial" ? "partially processed bookmarks" : this.view + " bookmarks"}.`;
      this.text(
        "activity-results-label",
        this.changed
          ? "Results changed"
          : this.query
            ? `Results for “${this.query}”`
            : this.view === "history"
              ? "Saved runs"
              : "",
      );
      this.text("activity-update", this.changed ? "Update results" : "Refresh");
      this.disable("activity-update");
      const shown =
        this.view === "history" ? data.items.length : this.items.size;
      this.text(
        "activity-page",
        shown
          ? this.changed
            ? `${n(shown)} shown`
            : `${n(data.offset + 1)}–${n(data.offset + shown)} of ${n(data.total)}`
          : "",
      );
      button("activity-prev").disabled = !data.offset;
      button("activity-next").disabled = data.next_offset === null;
      if (page) {
        $("activity-rows").scrollIntoView({ block: "start" });
        $("activity-page").focus({ preventScroll: true });
      }
    } catch (e) {
      if (request !== this.request) return;
      this.text(
        "activity-results-label",
        this.data
          ? "Connection lost · Showing saved view"
          : "Couldn’t load activity",
      );
      this.disable("activity-update");
      $("activity-empty").hidden = !!$("activity-rows").children.length;
      button("activity-retry").hidden = false;
      $("activity-empty").querySelector("p")!.textContent = (
        e as Error
      ).message;
    } finally {
      if (request === this.request) {
        this.loading = false;
        $("activity-rows").setAttribute("aria-busy", "false");
      }
    }
  }
  private label(row: Item) {
    if (row.status === "active")
      return this.data?.paused || row.run_state !== "running"
        ? "Pausing…"
        : (
            {
              transcript: "Transcribing",
              video: "Indexing video",
              image: "Indexing image",
              caption: "Describing image",
              download: "Downloading media",
              context: "Capturing context",
              page: "Capturing page",
              text: "Indexing text",
              process: "Preparing sources",
              ocr: "Reading image text",
              video_ocr: "Reading video text",
              audio: "Indexing audio",
            } as Record<string, string>
          )[row.kind] || "Processing";
    if (row.status === "attention")
      return row.stage_state === "browser"
        ? "Browser needed"
        : row.error?.startsWith("chatgpt_")
          ? "ChatGPT needed"
          : row.error?.startsWith("x_http_")
            ? "X needs attention"
            : row.kind === "download"
              ? "Download failed"
              : "Needs attention";
    if (row.status === "retrying")
      return (row.next_at || 0) > Date.now() / 1000
        ? `Retry at ${clock(row.next_at!)}`
        : "Waiting to retry";
    if (row.status === "waiting")
      return (row.run && row.run_state !== "running") ||
        (this.data?.paused && !row.eligible)
        ? "Paused"
        : "Waiting";
    return row.status === "partial" ? "Source unavailable" : "Complete";
  }
  private renderRow(row: Item) {
    const esc = this.d.escape;
    let node = $("activity-rows").querySelector<HTMLElement>(
      `[data-item="${CSS.escape(row.id)}"]`,
    );
    if (!node) {
      node = document.createElement("article");
      node.className = "activity-row";
      node.dataset.item = row.id;
      const preview = this.d.preview(
        row.presentation.preview || row.presentation.avatar,
      );
      node.innerHTML = `<div class="activity-row-main"><button class="activity-item" aria-expanded="false" aria-controls="activity-detail-${row.id}"><span class="activity-thumbnail">${preview ? `<img src="${esc(preview)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : icon("download")}</span><span class="activity-item-text"><strong>${esc(row.presentation.name || (row.author && "@" + row.author) || "Saved post")}</strong><span>${esc((row.presentation.text || row.title || "Media bookmark").replace(/\s+/g, " "))}</span></span></button><span class="activity-type">${esc(row.content_types.filter((x) => x !== "Post").join(" · ") || "Post")}</span><span class="activity-row-state"></span><button class="activity-row-action quiet" hidden></button></div><div class="activity-item-detail" id="activity-detail-${row.id}" hidden></div>`;
      node
        .querySelector("img")
        ?.addEventListener("error", (e) =>
          (e.currentTarget as HTMLElement).remove(),
        );
      const rowButton =
        node.querySelector<HTMLButtonElement>(".activity-item")!;
      rowButton.onclick = () =>
        void this.perform(() => this.expand(row.id), rowButton);
      $("activity-rows").append(node);
    }
    const status = node.querySelector<HTMLElement>(".activity-row-state")!,
      label = this.label(row),
      glyph =
        row.status === "active"
          ? "activity"
          : row.status === "attention" || row.status === "partial"
            ? "attention"
            : row.status === "complete"
              ? "check"
              : "clock";
    const html = `${icon(glyph)}<span>${esc(label)}</span>`;
    if (status.innerHTML !== html) status.innerHTML = html;
    status.dataset.state = row.status;
    const action = node.querySelector<HTMLButtonElement>(
      ".activity-row-action",
    )!;
    const account = /^(chatgpt_|x_http_40[12]|temporary_|disk_space)/.test(
      row.error || "",
    );
    const kind =
      row.status === "attention"
        ? row.stage_state === "browser"
          ? "browser"
          : account
            ? "settings"
            : "retry"
        : "";
    if (action.hasAttribute("aria-busy")) return;
    action.toggleAttribute("data-requires-online", kind === "retry");
    action.disabled = kind === "retry" && !this.online;
    action.hidden = !kind;
    action.textContent =
      kind === "browser"
        ? "Open browser"
        : kind === "settings"
          ? "Settings"
          : "Retry";
    action.onclick = () =>
      void this.perform(async () => {
        if (kind === "browser") await this.d.recover(row.id);
        else if (kind === "settings") this.d.settings();
        else {
          const result = await this.d.call("queue_control", {
            action: "retry",
            item: row.id,
            failed_only: true,
          });
          this.text(
            "activity-feedback",
            result.retried
              ? "Failed steps queued."
              : "No failed steps to retry.",
          );
          await this.update();
        }
      }, action);
  }
  private async expand(id: string) {
    const node = $("activity-rows").querySelector<HTMLElement>(
      `[data-item="${CSS.escape(id)}"]`,
    )!;
    const body = node.querySelector<HTMLElement>(".activity-item-detail")!;
    const open = this.expanded !== id;
    $("activity-rows")
      .querySelectorAll<HTMLElement>(".activity-item-detail")
      .forEach((e) => (e.hidden = true));
    $("activity-rows")
      .querySelectorAll("button[aria-expanded]")
      .forEach((e) => e.setAttribute("aria-expanded", "false"));
    this.expanded = open ? id : "";
    this.detailRequest++;
    if (!open) return;
    body.hidden = false;
    node.querySelector("button")!.setAttribute("aria-expanded", "true");
    body.textContent = "Loading source progress…";
    await this.loadDetails(id, body, 0);
  }
  private async loadDetails(id: string, body: HTMLElement, offset: number) {
    const request = this.detailRequest;
    try {
      const data = await this.d.call("activity_item", { item: id, offset });
      if (request !== this.detailRequest || this.expanded !== id) return;
      if (!offset)
        body.innerHTML = `<div class="activity-detail-actions"><button data-open-bookmark>Open bookmark</button><button class="quiet" data-refresh-stages>Refresh progress</button></div><div class="activity-stages"></div>`;
      body.querySelector<HTMLButtonElement>("[data-open-bookmark]")!.onclick =
        () => void this.d.act(() => this.d.openBookmark(id));
      body.querySelector<HTMLButtonElement>("[data-refresh-stages]")!.onclick =
        () => void this.perform(() => this.loadDetails(id, body, 0));
      body.querySelector("[data-more-stages]")?.remove();
      const stages = body.querySelector(".activity-stages")!;
      for (const s of data.stages) {
        const row = document.createElement("div");
        row.className = "activity-stage";
        const label =
          (
            {
              running: "Active",
              queued: "Waiting",
              waiting: "Automatic retry",
              failed: "Failed",
              blocked: "Needs attention",
              browser: "Browser needed",
              complete: "Complete",
              unavailable: "Unavailable",
            } as Record<string, string>
          )[s.state] || s.state;
        row.innerHTML = `<span>${this.d.escape(this.d.stage(s.kind))}</span><span class="muted">${label}${s.state === "waiting" && s.next_at ? ` · ${clock(s.next_at)}` : ""}</span>${s.error ? `<p class="muted">${this.d.escape(this.d.issue(s.error))}</p>` : ""}`;
        stages.append(row);
      }
      if (!offset && !data.stages.length)
        stages.textContent = "Source processing hasn’t started.";
      if (data.next_offset !== null) {
        const more = document.createElement("button");
        more.textContent = "More sources";
        more.dataset.moreStages = "";
        more.onclick = () =>
          void this.perform(
            () => this.loadDetails(id, body, data.next_offset),
            more,
          );
        body.append(more);
      }
    } catch (e) {
      body.textContent = (e as Error).message;
      const retry = document.createElement("button");
      retry.textContent = "Try again";
      retry.onclick = () =>
        void this.perform(() => this.loadDetails(id, body, 0), retry);
      body.append(retry);
    }
  }
  private renderHistory(data: Snapshot, replace: boolean) {
    const root = $("activity-rows");
    if (replace) root.replaceChildren();
    const ids = new Set((data.items as Run[]).map((r) => r.id));
    for (const node of root.querySelectorAll<HTMLElement>("[data-run]"))
      if (!ids.has(node.dataset.run!)) node.remove();
    for (const r of data.items as Run[]) {
      let row = root.querySelector<HTMLElement>(
        `[data-run="${CSS.escape(r.id)}"]`,
      );
      const focused = !!row?.contains(document.activeElement);
      if (!row) {
        row = document.createElement("article");
        row.className = "activity-run";
        row.dataset.run = r.id;
        root.append(row);
      }
      const b = r.limits;
      const limit = b?.reason;
      const usage = b
        ? `${n(b.x_used)} / ${b.x_limit ? n(b.x_limit) : "∞"} X requests${r.media_processing ? ` · ${n(b.description_used)} / ${b.description_limit ? n(b.description_limit) : "∞"} descriptions` : ""}`
        : "";
      const html = `<div><strong>${this.d.escape(r.label)}</strong><p class="muted">${new Date(r.created * 1000).toLocaleString()} · ${r.activity_state === "limited" ? "Limit reached" : r.activity_state === "attention" ? "Needs attention" : r.activity_state === "pausing" ? "Pausing…" : r.activity_state.charAt(0).toUpperCase() + r.activity_state.slice(1)}</p><p class="muted">${usage}</p></div><div class="actions">${limit ? `<button data-extend>Add 100 ${limit === "items" ? "bookmarks" : limit === "x" ? "X requests" : "descriptions"}</button>` : r.pending_steps ? `<button data-control="${r.activity_state === "running" ? "pause" : "resume"}" ${r.activity_state === "pausing" ? "disabled" : ""}>${r.activity_state === "running" ? "Pause" : r.activity_state === "pausing" ? "Pausing…" : "Resume"}</button>` : ""}${r.retryable_steps && !limit ? '<button data-control="retry">Retry failures</button>' : ""}</div>`;
      if (row.innerHTML === html || row.querySelector("[aria-busy]")) continue;
      row.innerHTML = html;
      row.querySelectorAll<HTMLButtonElement>("button").forEach((b) => {
        b.dataset.requiresOnline = "";
        b.dataset.unavailable = String(b.disabled);
      });
      this.updateActions();
      if (focused) {
        row.tabIndex = -1;
        (row.querySelector<HTMLElement>("button:not(:disabled)") || row).focus({
          preventScroll: true,
        });
      }
      row.querySelectorAll<HTMLButtonElement>("button").forEach(
        (b) =>
          (b.onclick = () =>
            void this.perform(async () => {
              if (b.hasAttribute("data-extend")) {
                await this.d.call("extend_run", {
                  run: r.id,
                  kind: limit,
                  amount: 100,
                });
                this.text(
                  "activity-feedback",
                  "Limit increased. Resume when ready.",
                );
              } else
                await this.d.call("queue_control", {
                  run: r.id,
                  action: b.dataset.control,
                  failed_only: true,
                });
              await this.update();
            }, b)),
      );
    }
  }
  private async startImport(recheck: boolean) {
    const imp = this.data?.import;
    const limits = {
      items: this.value("activity-scope"),
      x_requests: this.value("activity-x"),
    };
    if (imp?.unfinished) {
      await this.d.call("continue_import", { run: imp.id, limits });
    } else
      await this.d.call("sync", {
        mode: recheck ? "full" : "new",
        process: false,
        limits,
      });
    this.closePopovers(true);
    await this.update();
  }
  private async process() {
    const s = this.data;
    if (!s) return;
    const r = s.processing_runs[0];
    if (s.processing_run_count > 1 || r?.limits?.reason) {
      this.filter("history");
      return;
    }
    if (r?.activity_state === "running")
      await this.d.call("queue_control", { action: "pause", run: r.id });
    else if (r?.pending_steps)
      await this.d.call("queue_control", { action: "resume", run: r.id });
    else if (s.counts.eligible) {
      await this.startProcessing();
      return;
    } else {
      this.filter("attention");
      return;
    }
    await this.update();
  }
  private async startProcessing() {
    await this.d.call("process_saved", {
      mode: "repair",
      scope: "pending",
      limits: {
        x_requests: this.value("activity-process-x"),
        description_requests: this.value("activity-descriptions"),
      },
    });
    await this.update();
  }
  private async update() {
    await this.d.refresh();
    await this.load();
  }
}
