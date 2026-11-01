// Review Desk screen. No framework and no outside requests: everything loads from this server.
// All document text goes in through textContent, so a hostile PDF can't inject markup.

const app = document.getElementById("app");
const toasts = document.getElementById("toasts");

const LABELS = {
  document_type: "Type", vendor_name: "Vendor", document_number: "Number", issue_date: "Issue date", due_date: "Due date",
  purchase_order: "PO", bill_to_name: "Bill to", currency: "Currency", subtotal: "Subtotal", discount: "Discount",
  shipping: "Shipping", tax: "Tax", total: "Total",
};
const GROUPS = [
  ["Document", ["document_type", "vendor_name", "document_number", "issue_date", "due_date", "purchase_order", "bill_to_name"]],
  ["Amounts", ["currency", "subtotal", "discount", "shipping", "tax", "total"]],
];
const CHECK_NAMES = { items_sum: "Line items and subtotal", totals_sum: "Subtotal, tax and total" };
const STATUS = {
  queued: ["Waiting", "busy"], reading: ["Reading", "busy"], needs_review: ["Needs review", "flag"],
  approved: ["Approved", "ok"], failed: ["Failed", "bad"], rejected: ["Refused", "bad"],
};
const ACTIONS = {
  document_received: "Received", upload_refused: "Upload refused", reading_started: "Reading started", reading_interrupted: "Reading interrupted",
  reading_failed: "Reading failed", extraction_stored: "Extraction stored", manual_entry_started: "Manual entry started", retry_requested: "Retry requested",
  field_edited: "Value edited", field_confirmed: "Value confirmed", row_added: "Row added", row_deleted: "Row deleted",
  check_acknowledged: "Check acknowledged", approved: "Approved", approval_voided: "Approval voided", exported: "Exported", export_refused: "Export refused",
};

// --- Small helpers ----------------------------------------------------------------------------

function h(tag, props, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") element.className = value;
    else if (key.startsWith("on")) element.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key === "value") element.value = value;
    else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    element.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return element;
}

function toast(message, kind) {
  const item = h("div", { class: `toast ${kind || ""}` }, message);
  toasts.append(item);
  setTimeout(() => item.remove(), kind === "error" ? 7000 : 3500);
}

function remember(key, value) {
  try { localStorage.setItem(key, value); } catch { /* Private windows can refuse this. The app still works. */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

class ApiError extends Error {
  constructor(status, payload) {
    super(payload.message || "Something went wrong.");
    this.status = status;
    this.code = payload.code;
    this.payload = payload;
  }
}

async function api(method, path, body) {
  const headers = { "X-Review-Desk": "1" };
  if (state.reviewer) headers["X-Reviewer"] = state.reviewer;
  const options = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) {
    let payload = {};
    try { payload = await response.json(); } catch { payload = { message: `The server answered ${response.status}.` }; }
    throw new ApiError(response.status, payload);
  }
  return response.json();
}

function formatTime(iso) {
  if (!iso) return "";
  const date = new Date(iso);
  const today = new Date();
  const time = date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (date.toDateString() === today.toDateString()) return time;
  return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function grouped(amount) {
  const [whole, fraction] = String(amount).split(".");
  const digits = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return fraction === undefined ? digits : `${digits}.${fraction}`;
}

function money(amount, currency) {
  if (amount === null || amount === undefined) return "";
  return currency ? `${grouped(amount)} ${currency}` : grouped(amount);
}

function shownValue(field) {
  if (field.value === null) return null;
  return field.kind === "amount" ? grouped(field.value) : field.value;
}

function pathLabel(path) {
  const row = /^row\.(\d+)\.(\w+)$/.exec(path || "");
  if (!row) return LABELS[path] || path;
  return `Line ${row[1]} ${{ description: "description", quantity: "quantity", unit_price: "price", amount: "amount" }[row[2]] || row[2]}`;
}

// --- Shared state and routing -------------------------------------------------------------------

const state = {
  reviewer: recall("reviewer") || "",
  info: null,
  queueFilter: "needs_review",
  queueSelected: 0,
  documents: [],
  doc: null,
  selected: null,
  editing: false,
  editError: null,
  zoom: 1,
  nextDocument: null,
  timer: null,
  view: null,
};

function stopPolling() {
  if (state.timer) clearTimeout(state.timer);
  state.timer = null;
}

function route() {
  stopPolling();
  toasts.replaceChildren();
  const hash = location.hash.replace(/^#\/?/, "");
  const parts = hash.split("/").filter(Boolean);
  if (parts[0] === "doc" && parts[1]) return openDocument(parts[1]);
  if (parts[0] === "audit") return renderAudit(parts[1]);
  return renderQueue();
}
window.addEventListener("hashchange", route);

function appBar(active) {
  return h("header", { class: "appbar" },
    h("a", { class: "brand", href: "#/" }, h("img", { src: "/static/favicon.svg", alt: "" }), "Review Desk"),
    h("nav", { class: "nav", "aria-label": "Main" },
      h("a", { href: "#/", class: active === "queue" ? "active" : "" }, "Queue"),
      h("a", { href: "#/audit", class: active === "audit" ? "active" : "" }, "Audit log"),
    ),
    h("div", { class: "spacer" }),
    whoControl(),
  );
}

function whoControl() {
  return h("div", { class: "who" },
    state.reviewer ? [`Reviewing as ${state.reviewer} · `, h("button", { onclick: () => askReviewer() }, "change")] : h("button", { onclick: () => askReviewer() }, "Set your name"),
  );
}

function askReviewer() {
  return new Promise((resolve) => {
    const input = h("input", { "aria-label": "Your name", value: state.reviewer, autocomplete: "name", placeholder: "Your name" });
    const finish = (event) => {
      event?.preventDefault();
      const name = input.value.trim();
      if (!name) { input.focus(); return; }
      state.reviewer = name;
      remember("reviewer", name);
      dialog.close();
      dialog.remove();
      route();
      resolve(name);
    };
    const dialog = h("dialog", {},
      h("form", { class: "dialog-body", onsubmit: finish },
        h("h2", {}, "Who's reviewing?"),
        h("p", {}, "Your name goes in the audit log beside everything you confirm, change or approve."),
        input,
        h("div", { class: "dialog-actions" }, h("button", { class: "btn primary", type: "submit" }, "Continue")),
      ),
    );
    dialog.addEventListener("cancel", () => { dialog.remove(); resolve(null); });
    document.body.append(dialog);
    dialog.showModal();
    input.select();
  });
}

async function needReviewer() {
  return state.reviewer || (await askReviewer());
}

// --- Queue ---------------------------------------------------------------------------------------

const FILTERS = [
  ["needs_review", "Needs review", (d) => d.status === "needs_review"],
  ["active", "In progress", (d) => d.status === "queued" || d.status === "reading"],
  ["problem", "Failed", (d) => d.status === "failed" || d.status === "rejected"],
  ["approved", "Approved", (d) => d.status === "approved"],
  ["all", "All", () => true],
];

async function renderQueue() {
  state.view = "queue";
  try {
    const { documents } = await api("GET", "/api/documents");
    state.documents = documents;
  } catch (error) {
    app.replaceChildren(appBar("queue"), h("div", { class: "empty" }, h("h2", {}, "Couldn't load the queue"), h("p", {}, error.message)));
    return;
  }
  if (state.view !== "queue") return;
  paintQueue();
  const busy = state.documents.some((d) => d.status === "queued" || d.status === "reading");
  state.timer = setTimeout(() => { if (state.view === "queue") renderQueue(); }, busy ? 2000 : 8000);
}

function visibleDocuments() {
  const filter = FILTERS.find((f) => f[0] === state.queueFilter) || FILTERS[0];
  return state.documents.filter(filter[2]);
}

function statusPill(document_) {
  const [label, kind] = STATUS[document_.status] || [document_.status, ""];
  return h("span", { class: `pill ${kind}` }, h("span", { class: "dot" }), label);
}

function paintQueue() {
  const fileInput = h("input", { type: "file", accept: "application/pdf,.pdf", multiple: true, hidden: true, id: "file-input", onchange: (event) => uploadFiles(event.target.files) });
  const rows = visibleDocuments();
  state.queueSelected = Math.min(state.queueSelected, Math.max(rows.length - 1, 0));

  const tabs = h("div", { class: "tabs", role: "tablist" },
    FILTERS.map(([key, label, test]) => h("button", {
      class: state.queueFilter === key ? "active" : "", role: "tab", "aria-selected": state.queueFilter === key ? "true" : "false",
      onclick: () => { state.queueFilter = key; state.queueSelected = 0; paintQueue(); },
    }, label, h("span", { class: "count" }, state.documents.filter(test).length))),
  );

  let body;
  if (!state.documents.length) {
    body = h("div", { class: "table-card" }, h("div", { class: "empty" },
      h("h2", {}, "Nothing to review yet"),
      h("p", {}, "Drop an invoice or receipt PDF anywhere on this page. It stays on this machine: the only network call goes to the model endpoint you configured."),
      h("button", { class: "btn primary", onclick: () => fileInput.click() }, "Choose a PDF"),
      h("div", { class: "hints" }, "A few synthetic documents to try are in the repo's ", h("code", {}, "samples"), " folder."),
    ));
  } else if (!rows.length) {
    body = h("div", { class: "table-card" }, h("div", { class: "empty" },
      h("h2", {}, state.queueFilter === "needs_review" ? "Queue clear" : "Nothing here"),
      h("p", {}, state.queueFilter === "needs_review" ? "Every document has been handled. New ones show up here as soon as they've been read." : "No documents match this filter."),
    ));
  } else {
    body = h("div", { class: "table-card" }, h("table", { class: "docs" },
      h("colgroup", {}, h("col", { style: null }), h("col", { class: "optional" }), h("col", { class: "optional" })),
      h("thead", {}, h("tr", {},
        h("th", {}, "Document"), h("th", { class: "optional" }, "Vendor"), h("th", { class: "optional", width: "130" }, "Number"),
        h("th", { class: "num", width: "130" }, "Total"), h("th", { class: "num", width: "90" }, "To check"), h("th", { width: "130" }, "Status"), h("th", { class: "optional", width: "110" }, "Received"),
      )),
      h("tbody", {}, rows.map((row, index) => h("tr", {
        class: index === state.queueSelected ? "selected" : "",
        dataset: { id: row.id },
        onclick: () => { location.hash = `#/doc/${row.id}`; },
      },
        h("td", { title: row.filename }, h("span", { class: "docname" }, row.filename), row.document_type ? h("div", { class: "docsub" }, row.document_type === "invoice" ? "Invoice" : "Receipt") : null),
        h("td", { class: "optional", title: row.vendor_name || "" }, row.vendor_name || h("span", { class: "faint" }, "Not read yet")),
        h("td", { class: "optional", title: row.document_number || "" }, row.document_number || ""),
        h("td", { class: "num" }, money(row.total, row.currency)),
        h("td", { class: "num" }, row.status === "needs_review" ? (row.flagged ? h("span", { class: "pill flag" }, row.flagged) : h("span", { class: "muted" }, "0")) : ""),
        h("td", {}, statusPill(row), row.last_exported_at ? h("div", { class: "docsub" }, "Exported") : null),
        h("td", { class: "optional muted" }, formatTime(row.created_at)),
      ))),
    ));
  }

  app.replaceChildren(
    appBar("queue"),
    h("main", { class: "queue" }, h("div", { class: "queue-inner" },
      h("div", { class: "queue-head" },
        h("h1", {}, "Queue"), tabs, h("div", { class: "spacer" }), fileInput,
        h("button", { class: "btn primary", id: "upload", onclick: () => fileInput.click() }, "Upload PDF"),
      ),
      body,
      rows.length ? h("div", { class: "hints" }, h("kbd", {}, "J"), h("kbd", {}, "K"), " move · ", h("kbd", {}, "Enter"), " open · ", h("kbd", {}, "U"), " upload") : null,
    )),
    h("div", { class: "dropveil" }, "Drop PDFs to add them to the queue"),
  );
}

async function uploadFiles(files) {
  let added = 0;
  for (const file of files) {
    try {
      const response = await fetch(`/api/documents?filename=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/pdf", "X-Review-Desk": "1", ...(state.reviewer ? { "X-Reviewer": state.reviewer } : {}) },
        body: file,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        toast(`${file.name}: ${payload.message || "the server refused it."}`, "error");
        continue;
      }
      added += 1;
      state.queueFilter = "active";
    } catch (error) {
      toast(`${file.name}: ${error.message}`, "error");
    }
  }
  if (added) toast(added === 1 ? "Added. Reading it now." : `${added} added. They're read one at a time.`);
  if (state.view === "queue") { stopPolling(); renderQueue(); }
}

let dragDepth = 0;
window.addEventListener("dragenter", (event) => {
  if (state.view !== "queue" || !event.dataTransfer?.types?.includes("Files")) return;
  dragDepth += 1;
  document.body.classList.add("dragging");
});
window.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) document.body.classList.remove("dragging");
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  document.body.classList.remove("dragging");
  if (state.view === "queue" && event.dataTransfer?.files?.length) uploadFiles(event.dataTransfer.files);
});

// --- Review screen -------------------------------------------------------------------------------

async function openDocument(id) {
  state.view = "review";
  state.nextDocument = null;
  state.selected = null;
  state.editing = false;
  state.editError = null;
  await refreshDocument(id, true);
}

async function refreshDocument(id, first) {
  try {
    state.doc = await api("GET", `/api/documents/${id}`);
  } catch (error) {
    app.replaceChildren(appBar("queue"), h("div", { class: "empty" }, h("h2", {}, "Couldn't open this document"), h("p", {}, error.message), h("a", { class: "btn", href: "#/" }, "Back to queue")));
    return;
  }
  if (state.view !== "review") return;
  if (first || !state.selected) state.selected = nextFlaggedFrom(null) || (reviewable() ? fieldOrder()[0] : null);
  if (first && state.doc.status === "approved") await findNextDocument();
  paintReview();
  if (state.doc.status === "queued" || state.doc.status === "reading") {
    state.timer = setTimeout(() => { if (state.view === "review" && state.doc?.id === id) refreshDocument(id, true); }, 2000);
  }
}

function reviewable() {
  return state.doc && (state.doc.status === "needs_review" || state.doc.status === "approved");
}

function fieldMap() {
  return new Map(state.doc.fields.map((field) => [field.path, field]));
}

function rowsOfDocument() {
  const rows = new Map();
  for (const field of state.doc.fields) {
    if (field.row_key === null) continue;
    if (!rows.has(field.row_key)) rows.set(field.row_key, {});
    rows.get(field.row_key)[field.path.split(".").pop()] = field;
  }
  return [...rows.entries()];
}

function fieldOrder() {
  const order = [];
  for (const [, names] of GROUPS) order.push(...names);
  for (const [, cells] of rowsOfDocument()) {
    for (const name of ["description", "quantity", "unit_price", "amount"]) if (cells[name]) order.push(cells[name].path);
  }
  return order.filter((path) => fieldMap().has(path));
}

function nextFlaggedFrom(path) {
  const order = fieldOrder();
  const map = fieldMap();
  const start = path ? order.indexOf(path) + 1 : 0;
  for (let offset = 0; offset < order.length; offset += 1) {
    const candidate = order[(start + offset) % order.length];
    if (map.get(candidate)?.state === "flagged") return candidate;
  }
  return null;
}

function move(delta) {
  const order = fieldOrder();
  if (!order.length) return;
  const index = order.indexOf(state.selected);
  select(order[Math.max(0, Math.min(order.length - 1, (index === -1 ? 0 : index + delta)))]);
}

function select(path) {
  state.selected = path;
  state.editing = false;
  state.editError = null;
  paintReview();
}

function paintReview() {
  const { doc } = state;
  const panelScroll = document.querySelector(".panel-scroll")?.scrollTop || 0;
  const viewerNode = document.querySelector(".viewer");
  const viewerScroll = viewerNode ? [viewerNode.scrollLeft, viewerNode.scrollTop] : null;

  const [label, kind] = STATUS[doc.status] || [doc.status, ""];
  const canExport = Boolean(doc.approval);
  const remaining = doc.remaining.length + doc.unacknowledged_checks.length;

  const bar = h("div", { class: "review-bar" },
    h("a", { class: "btn small", href: "#/", title: "Back to queue (Esc)" }, "← Queue"),
    h("span", { class: "title", title: doc.filename }, doc.filename),
    h("span", { class: `pill ${kind}`, id: "doc-status" }, h("span", { class: "dot" }), label),
    h("div", { class: "spacer" }),
    h("a", { class: "btn small", href: `#/audit/${doc.id}` }, "History"),
    reviewable() ? h("button", { class: "btn small", id: "export-json", disabled: !canExport, title: canExport ? "Download as JSON" : "Export unlocks after approval", onclick: () => exportDocument("json") }, "Export JSON") : null,
    reviewable() ? h("button", { class: "btn small", id: "export-csv", disabled: !canExport, title: canExport ? "Download as CSV" : "Export unlocks after approval", onclick: () => exportDocument("csv") }, "Export CSV") : null,
    doc.status === "needs_review" ? h("button", {
      class: "btn primary", id: "approve", disabled: remaining > 0,
      title: remaining ? `${remaining} still need a look` : "Approve this document (A)",
      onclick: approveDocument,
    }, remaining ? `${remaining} left to check` : ["Approve ", h("kbd", {}, "A")]) : null,
    doc.status === "approved" ? h("button", { class: "btn primary", id: "next-document", onclick: goToNextDocument },
      state.nextDocument ? ["Next document ", h("kbd", {}, "]")] : ["Back to queue ", h("kbd", {}, "]")]) : null,
  );

  const viewer = paintViewer();
  const panel = reviewable() ? paintPanel() : paintStatusPanel();
  app.replaceChildren(appBar("queue"), h("div", { class: "review" }, bar, h("div", { class: "review-body" }, viewer, panel)));

  const newPanel = document.querySelector(".panel-scroll");
  if (newPanel) newPanel.scrollTop = panelScroll;
  const newViewer = document.querySelector(".viewer");
  if (newViewer && viewerScroll) { newViewer.scrollLeft = viewerScroll[0]; newViewer.scrollTop = viewerScroll[1]; }
  document.querySelector(".field.selected, .cell.selected")?.scrollIntoView({ block: "nearest" });
  document.querySelector(".why")?.scrollIntoView({ block: "nearest" });
  scrollToHighlight();
  if (state.editing) {
    const input = document.querySelector("input.edit");
    input?.focus();
    input?.select();
  }
}

function scrollToHighlight() {
  const box = document.querySelector(".box.selected");
  const viewer = document.querySelector(".viewer");
  if (!box || !viewer) return;
  const boxRect = box.getBoundingClientRect();
  const viewRect = viewer.getBoundingClientRect();
  const margin = 70;
  if (boxRect.top < viewRect.top + margin) viewer.scrollTop -= viewRect.top + margin - boxRect.top;
  else if (boxRect.bottom > viewRect.bottom - margin) viewer.scrollTop += boxRect.bottom - viewRect.bottom + margin;
  if (boxRect.left < viewRect.left + 10) viewer.scrollLeft -= viewRect.left + 10 - boxRect.left;
  else if (boxRect.right > viewRect.right - 10) viewer.scrollLeft += boxRect.right - viewRect.right + 10;
}

function paintViewer() {
  const { doc } = state;
  if (!doc.pages.length) {
    return h("div", { class: "viewer" }, h("div", { class: "empty" }, h("p", {}, doc.status === "rejected" ? "This file wasn't opened, so there's no page to show." : "The page appears here as soon as it's been read.")));
  }
  const selectedField = fieldMap().get(state.selected);
  const pages = doc.pages.map((page) => {
    const boxes = [];
    for (const field of doc.fields) {
      field.sources.forEach((source) => {
        if (source.page !== page.number) return;
        const box = h("div", {
          class: `box ${field.state === "flagged" ? "flagged" : ""} ${field.path === state.selected ? "selected" : ""}`,
          title: fieldLabel(field),
          dataset: { path: field.path },
          onclick: () => select(field.path),
        });
        box.style.left = `${(source.x0 / page.width) * 100}%`;
        box.style.top = `${(source.top / page.height) * 100}%`;
        box.style.width = `${((source.x1 - source.x0) / page.width) * 100}%`;
        box.style.height = `${((source.bottom - source.top) / page.height) * 100}%`;
        boxes.push(box);
      });
    }
    const node = h("div", { class: "page", dataset: { page: page.number } },
      h("img", { src: `/api/documents/${doc.id}/pages/${page.number}`, alt: `Page ${page.number + 1} of ${doc.filename}`, draggable: "false" }),
      boxes,
    );
    node.style.width = `${Math.min(1, page.width / 420) * state.zoom * 100}%`;
    return node;
  });
  let note = null;
  if (selectedField && !selectedField.sources.length) {
    note = h("div", { class: "source-note", id: "source-note" }, selectedField.value === null ? "Nothing was read for this field, so there's nothing to highlight." : "This value wasn't found on the page.");
  }
  return h("div", { class: "viewer", tabindex: "-1" },
    h("div", { class: "zoomdock" }, h("div", { class: "zoombar", role: "group", "aria-label": "Zoom" },
      h("button", { title: "Zoom out (-)", "aria-label": "Zoom out", onclick: () => setZoom(state.zoom / 1.2) }, "−"),
      h("span", { class: "level" }, `${Math.round(state.zoom * 100)}%`),
      h("button", { title: "Zoom in (+)", "aria-label": "Zoom in", onclick: () => setZoom(state.zoom * 1.2) }, "+"),
    )),
    note,
    h("div", { class: "pages" }, pages),
  );
}

function setZoom(value) {
  state.zoom = Math.max(0.5, Math.min(3, value));
  paintReview();
}

function fieldLabel(field) {
  if (field.row_key === null) return LABELS[field.path] || field.path;
  const cell = field.path.split(".").pop();
  const row = rowsOfDocument().findIndex(([key]) => key === field.row_key) + 1;
  return `Line ${row} ${{ description: "description", quantity: "quantity", unit_price: "price", amount: "amount" }[cell]}`;
}

function stateTag(field) {
  if (field.state === "edited") return h("span", { class: "tag edited" }, "Edited");
  if (field.state === "confirmed") return h("span", { class: "tag ok" }, "✓ Checked");
  const percent = field.confidence === null ? "–" : `${Math.round(field.confidence * 100)}%`;
  if (field.state === "flagged") return h("span", { class: "tag flag" }, percent);
  return h("span", { class: "tag", title: "Confidence from the checks below" }, percent);
}

function paintPanel() {
  const { doc } = state;
  const map = fieldMap();
  const handled = doc.fields.filter((f) => f.state === "confirmed" || f.state === "edited").length;
  const flagged = doc.remaining.length;
  const checks = doc.unacknowledged_checks.length;
  const progress = handled + flagged ? Math.round((handled / (handled + flagged)) * 100) : 100;
  const left = [flagged ? `${flagged} ${flagged === 1 ? "value" : "values"}` : null, checks ? `${checks} ${checks === 1 ? "check" : "checks"}` : null].filter(Boolean).join(" and ");

  const groups = GROUPS.map(([title, names]) => [
    h("div", { class: "group-title" }, title),
    names.filter((name) => map.has(name)).map((name) => fieldRow(map.get(name))),
  ]);

  const rows = rowsOfDocument();
  const items = [
    h("div", { class: "group-title" }, `Line items (${rows.length})`),
    rows.map(([key, cells], index) => itemRow(key, cells, index + 1)),
    h("div", { class: "add-row" }, h("button", { class: "btn small", id: "add-row", onclick: addRow }, "+ Add a line the model missed")),
  ];

  return h("aside", { class: "panel", "aria-label": "Extracted fields" },
    doc.approval
      ? h("div", { class: "progress", id: "approved-note" }, h("span", { class: "pill ok" }, "Approved"), h("span", { class: "muted" }, `by ${doc.approval.approved_by}, ${formatTime(doc.approval.approved_at)}. Changing a value withdraws this.`))
      : h("div", { class: "progress" },
        h("span", { id: "left-to-check" }, left ? h("strong", {}, `${left} to clear`) : "Nothing left to check"),
        h("div", { class: "bar" }, progressFill(progress)),
        flagged ? h("button", { class: "btn small", onclick: () => select(nextFlaggedFrom(state.selected)) }, "Next ", h("kbd", {}, "N")) : null,
      ),
    paintAlerts(),
    h("div", { class: "panel-scroll" }, groups, items),
    h("div", { class: "panel-foot" },
      h("span", {}, h("kbd", {}, "J"), h("kbd", {}, "K"), " move"), h("span", {}, h("kbd", {}, "N"), " next to check"),
      h("span", {}, h("kbd", {}, "Enter"), " looks right"), h("span", {}, h("kbd", {}, "E"), " edit"), h("span", {}, h("kbd", {}, "?"), " all keys"),
    ),
  );
}

function progressFill(percent) {
  const fill = h("i");
  fill.style.width = `${percent}%`;
  return fill;
}

function paintAlerts() {
  const { doc } = state;
  const alerts = [];
  for (const page of doc.pages) {
    for (const warning of page.warnings || []) alerts.push(h("div", { class: "alert warn" }, h("div", { class: "text" }, `Page ${page.number + 1}: ${warning}`)));
  }
  if (doc.active_content.length) {
    alerts.push(h("div", { class: "alert warn", id: "active-content" }, h("div", { class: "text" }, `This PDF carries active content (${doc.active_content.join(", ").replace(/_/g, " ")}). None of it ran.`)));
  }
  for (const check of doc.checks) {
    if (check.outcome === "fail" && !check.acknowledged_by) {
      alerts.push(h("div", { class: "alert bad", dataset: { check: check.name } },
        h("div", { class: "text" }, h("strong", {}, CHECK_NAMES[check.name] || check.name), h("div", {}, check.detail)),
        doc.status === "needs_review" ? h("button", { class: "btn small", onclick: () => acknowledge(check.name), title: "The document itself doesn't add up, and you've seen that" }, "Accept as is") : null,
      ));
    } else if (check.outcome === "fail") {
      alerts.push(h("div", { class: "alert done" }, h("div", { class: "text" }, `${CHECK_NAMES[check.name] || check.name}: ${check.detail} Acknowledged by ${check.acknowledged_by}.`)));
    }
  }
  const passing = doc.checks.filter((c) => c.outcome === "pass");
  if (passing.length) {
    alerts.push(h("div", { class: "alert ok" }, h("div", { class: "text" }, `✓ ${passing.map((c) => (CHECK_NAMES[c.name] || c.name)).join(" · ")} add up`)));
  }
  return alerts.length ? h("div", { class: "alerts" }, alerts) : null;
}

function fieldRow(field) {
  const selected = field.path === state.selected;
  const row = h("div", {
    class: `field ${field.state === "flagged" ? "flagged" : ""} ${selected ? "selected" : ""}`,
    dataset: { path: field.path, state: field.state },
    onclick: () => { if (!selected) select(field.path); },
  },
    h("div", { class: "label" }, LABELS[field.path] || field.path),
    selected && state.editing ? null : h("div", { class: `value ${field.value === null ? "none" : ""}` }, field.value === null ? "None" : shownValue(field)),
    selected && state.editing ? h("div", {}) : null,
    stateTag(field),
  );
  return selected ? [row, whyPanel(field)] : row;
}

function itemRow(key, cells, number) {
  const cell = (name, extra) => {
    const field = cells[name];
    if (!field) return null;
    const selected = field.path === state.selected;
    return h("span", {
      class: `cell ${name === "description" ? "description" : ""} ${name === "amount" ? "amount" : ""} ${field.state} ${selected ? "selected" : ""} ${field.value === null ? "empty-cell" : ""}`,
      dataset: { path: field.path, state: field.state },
      title: `${fieldLabel(field)}: ${field.confidence === null ? "no score" : `${Math.round(field.confidence * 100)}%`}`,
      onclick: () => { if (!selected) select(field.path); },
    }, field.value === null ? extra : shownValue(field));
  };
  const selectedField = ["description", "quantity", "unit_price", "amount"].map((name) => cells[name]).find((field) => field?.path === state.selected);
  const flagged = Object.values(cells).some((field) => field.state === "flagged");
  return [
    h("div", { class: `item ${flagged ? "has-flag" : ""}`, dataset: { row: key } },
      h("div", { class: "row-top" }, h("span", { class: "no" }, number), cell("description", "No description")),
      h("div", { class: "row-numbers" }, cell("quantity", "–"), h("span", { class: "times" }, "×"), cell("unit_price", "–"), h("span", { class: "times" }, "="), cell("amount", "no amount")),
    ),
    selectedField ? whyPanel(selectedField, key) : null,
  ];
}

function whyPanel(field, rowKey) {
  const read = [];
  if (field.printed && field.printed !== field.value) read.push(h("div", { class: "read" }, "Printed as ", h("code", {}, field.printed)));
  if (field.state === "edited" && field.original_value !== field.value) {
    read.push(h("div", { class: "read" }, "The model read ", h("code", {}, field.original_value === null ? "nothing" : field.original_value)));
  }
  const signals = field.signals.filter((signal) => signal.outcome !== "not_applicable");
  const marks = { pass: "✓", weak: "~", fail: "✕" };
  const editing = state.editing && field.path === state.selected;
  return h("div", { class: "why", id: "why" },
    editing ? h("div", {},
      h("div", { class: "editor" },
        h("input", {
          class: "edit", value: field.value ?? "", "aria-label": `New value for ${fieldLabel(field)}`, placeholder: "Leave empty if it isn't on the page", spellcheck: "false",
          onkeydown: (event) => {
            event.stopPropagation();
            if (event.key === "Enter") { event.preventDefault(); saveEdit(field, event.target.value); }
            if (event.key === "Escape") { state.editing = false; state.editError = null; paintReview(); }
          },
        }),
        h("button", { class: "btn small primary", onclick: (event) => saveEdit(field, event.target.closest(".editor").querySelector("input").value) }, "Save"),
        h("button", { class: "btn small", onclick: () => { state.editing = false; state.editError = null; paintReview(); } }, "Cancel"),
      ),
      state.editError ? h("div", { class: "edit-error", role: "alert" }, state.editError) : null,
    ) : [
      read,
      signals.length ? h("ul", { class: "signals" }, signals.map((signal) => h("li", { class: signal.outcome },
        h("span", { class: "mark", "aria-label": signal.outcome }, marks[signal.outcome]),
        h("span", {}, signal.detail),
        h("span", { class: "effect" }, signal.effect > 0 ? `+${signal.effect.toFixed(1)}` : signal.effect.toFixed(1)),
      ))) : h("div", { class: "read" }, "Added by a person, so there are no checks to show."),
      h("div", { class: "actions" },
        field.state !== "edited" && field.state !== "confirmed" ? h("button", { class: "btn small primary", id: "confirm", onclick: () => confirmField(field) }, "Looks right ", h("kbd", {}, "↵")) : null,
        h("button", { class: "btn small", id: "edit", onclick: () => startEdit() }, "Edit ", h("kbd", {}, "E")),
        rowKey ? h("button", { class: "btn small danger", id: "delete-row", onclick: () => deleteRow(rowKey) }, "Delete line") : null,
      ),
    ],
  );
}

function paintStatusPanel() {
  const { doc } = state;
  if (doc.status === "queued" || doc.status === "reading") {
    return h("aside", { class: "panel" }, h("div", { class: "state-card", id: "reading" },
      h("h2", {}, h("span", { class: "spinner" }), doc.status === "queued" ? "Waiting its turn" : "Reading this document"),
      h("p", {}, "Documents are read one at a time. A local model on a shared server can take a few minutes. You can leave this screen: it keeps going."),
    ));
  }
  if (doc.status === "rejected") {
    return h("aside", { class: "panel" }, h("div", { class: "state-card bad", id: "failed" },
      h("h2", {}, "This file can't be processed"),
      h("p", {}, doc.failure || "The file was refused."),
      h("div", { class: "actions" }, h("a", { class: "btn", href: "#/" }, "Back to queue")),
    ));
  }
  return h("aside", { class: "panel" }, h("div", { class: "state-card bad", id: "failed" },
    h("h2", {}, "Extraction failed"),
    h("p", {}, doc.failure || "The reason wasn't recorded."),
    h("p", {}, "Nothing was guessed and nothing can be exported. Try again, or type the values in yourself."),
    h("div", { class: "actions" },
      h("button", { class: "btn primary", id: "retry", onclick: retryDocument }, "Try again"),
      h("button", { class: "btn", id: "manual", onclick: manualEntry }, "Enter by hand"),
    ),
  ));
}

// --- Review actions ------------------------------------------------------------------------------

async function send(method, path, body) {
  if (!(await needReviewer())) return null;
  try {
    return await api(method, path, { reviewer: state.reviewer, revision: state.doc.revision, ...body });
  } catch (error) {
    if (error.code === "stale_revision") {
      toast("Someone else changed this document. Showing the latest values.", "error");
      await refreshDocument(state.doc.id);
      return null;
    }
    throw error;
  }
}

function handleFailure(error) {
  toast(error.message, "error");
}

function startEdit() {
  state.editing = true;
  state.editError = null;
  paintReview();
}

async function saveEdit(field, value) {
  try {
    const updated = await send("PATCH", `/api/documents/${state.doc.id}/fields/${field.path}`, { value });
    if (!updated) return;
    afterChange(updated, field.path);
  } catch (error) {
    if (error.code === "invalid_value") {
      state.editError = error.message;
      paintReview();
      return;
    }
    handleFailure(error);
  }
}

async function confirmField(field) {
  try {
    const updated = await send("POST", `/api/documents/${state.doc.id}/fields/${field.path}/confirm`, {});
    if (updated) afterChange(updated, field.path);
  } catch (error) {
    handleFailure(error);
  }
}

function afterChange(updated, path) {
  state.doc = updated;
  state.editing = false;
  state.editError = null;
  const next = nextFlaggedFrom(path);
  if (next) {
    state.selected = next;
  } else {
    state.selected = path;
    if (updated.status === "needs_review" && !updated.unacknowledged_checks.length) toast("All flagged values are done. Press A to approve.");
    else if (updated.unacknowledged_checks.length) toast("One check still needs a decision, at the top of this panel.");
  }
  paintReview();
}

async function acknowledge(name) {
  try {
    const updated = await send("POST", `/api/documents/${state.doc.id}/checks/${name}/acknowledge`, {});
    if (updated) { state.doc = updated; paintReview(); }
  } catch (error) { handleFailure(error); }
}

async function addRow() {
  try {
    const updated = await send("POST", `/api/documents/${state.doc.id}/rows`, {});
    if (!updated) return;
    state.doc = updated;
    const rows = rowsOfDocument();
    state.selected = rows[rows.length - 1][1].description.path;
    state.editing = true;
    paintReview();
  } catch (error) { handleFailure(error); }
}

async function deleteRow(rowKey) {
  try {
    const updated = await send("DELETE", `/api/documents/${state.doc.id}/rows/${rowKey}`, {});
    if (!updated) return;
    state.doc = updated;
    state.selected = nextFlaggedFrom(null) || fieldOrder()[0];
    paintReview();
  } catch (error) { handleFailure(error); }
}

async function approveDocument() {
  const { doc } = state;
  if (doc.remaining.length) { toast(`${doc.remaining.length} flagged value(s) still need a look. Press N to go to the next one.`); return; }
  if (doc.unacknowledged_checks.length) { toast("A failed check needs a decision first. See the top of the panel."); return; }
  if (!(await needReviewer())) return;
  const edited = doc.fields.filter((f) => f.state === "edited").length;
  const confirmed = doc.fields.filter((f) => f.state === "confirmed").length;
  const auto = doc.fields.filter((f) => f.state === "auto").length;
  const yes = h("button", { class: "btn primary", id: "approve-confirm", type: "submit" }, "Approve");
  const dialog = h("dialog", { id: "approve-dialog" }, h("form", {
    class: "dialog-body",
    onsubmit: async (event) => {
      event.preventDefault();
      dialog.close();
      dialog.remove();
      try {
        const updated = await send("POST", `/api/documents/${doc.id}/approve`, {});
        if (!updated) return;
        state.doc = updated;
        await findNextDocument();
        paintReview();
        toast("Approved. Export is unlocked.");
      } catch (error) { handleFailure(error); }
    },
  },
    h("h2", {}, "Approve this document?"),
    h("p", {}, `As ${state.reviewer}: ${edited} edited, ${confirmed} checked, ${auto} accepted on the strength of their checks. Changing a value later withdraws the approval.`),
    h("div", { class: "dialog-actions" }, h("button", { class: "btn", type: "button", onclick: () => { dialog.close(); dialog.remove(); } }, "Not yet"), yes),
  ));
  dialog.addEventListener("cancel", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
  yes.focus();
}

async function findNextDocument() {
  try {
    const { documents } = await api("GET", "/api/documents?status=needs_review");
    state.nextDocument = documents.find((d) => d.id !== state.doc.id)?.id || null;
  } catch {
    state.nextDocument = null;
  }
}

function goToNextDocument() {
  if (state.nextDocument) location.hash = `#/doc/${state.nextDocument}`;
  else location.hash = "#/";
}

async function exportDocument(format) {
  try {
    const response = await fetch(`/api/documents/${state.doc.id}/export.${format}`, { headers: state.reviewer ? { "X-Reviewer": state.reviewer } : {} });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      toast(payload.message || "The server refused this export.", "error");
      await refreshDocument(state.doc.id);
      return;
    }
    const blob = await response.blob();
    const name = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "")?.[1] || `review-desk.${format}`;
    const link = h("a", { href: URL.createObjectURL(blob), download: name });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 10000);
    toast(`Saved ${name}.`);
    await refreshDocument(state.doc.id);
  } catch (error) { handleFailure(error); }
}

async function retryDocument() {
  if (!(await needReviewer())) return;
  try {
    state.doc = await api("POST", `/api/documents/${state.doc.id}/retry`, { reviewer: state.reviewer });
    refreshDocument(state.doc.id, true);
  } catch (error) { handleFailure(error); }
}

async function manualEntry() {
  if (!(await needReviewer())) return;
  try {
    state.doc = await api("POST", `/api/documents/${state.doc.id}/manual`, { reviewer: state.reviewer });
    state.selected = nextFlaggedFrom(null);
    state.editing = true;
    paintReview();
  } catch (error) { handleFailure(error); }
}

// --- Audit log -----------------------------------------------------------------------------------

async function renderAudit(documentId) {
  state.view = "audit";
  let payload;
  try {
    payload = await api("GET", `/api/audit${documentId ? `?document=${documentId}` : ""}`);
  } catch (error) {
    app.replaceChildren(appBar("audit"), h("div", { class: "empty" }, h("h2", {}, "Couldn't load the log"), h("p", {}, error.message)));
    return;
  }
  const result = h("div", { id: "verify-result" });
  const verify = async () => {
    const status = await api("GET", "/api/audit/verify");
    result.replaceChildren(status.ok
      ? h("div", { class: "verify good" }, `Chain intact: ${status.events} events. Newest hash `, h("code", {}, status.head))
      : h("div", { class: "verify broken" }, `Chain broken: ${status.problem}`));
  };
  app.replaceChildren(
    appBar("audit"),
    h("main", { class: "queue" }, h("div", { class: "queue-inner" },
      h("div", { class: "audit-head" },
        h("h1", {}, documentId ? "Document history" : "Audit log"),
        documentId ? h("a", { class: "btn small", href: `#/doc/${documentId}` }, "← Back to document") : null,
        documentId ? h("a", { class: "btn small", href: "#/audit" }, "Whole log") : null,
        h("div", { class: "spacer" }),
        h("button", { class: "btn", id: "verify", onclick: verify }, "Verify chain"),
      ),
      result,
      h("div", { class: "table-card" }, payload.events.length ? h("table", { class: "audit" },
        h("thead", {}, h("tr", {},
          h("th", { width: "52" }, "#"), h("th", { width: "130" }, "When"), h("th", { width: "150" }, "Who"), h("th", { width: "170" }, "What"), h("th", {}, "Details"), h("th", { width: "90" }, "Hash"),
        )),
        h("tbody", {}, payload.events.map((event) => h("tr", {},
          h("td", { class: "faint" }, event.seq),
          h("td", { class: "muted" }, formatTime(event.at)),
          h("td", {}, event.actor),
          h("td", { class: "action-name" }, ACTIONS[event.action] || event.action),
          h("td", { class: "detail" }, detailText(event)),
          h("td", { class: "hash", title: event.hash }, event.hash.slice(0, 8)),
        ))),
      ) : h("div", { class: "empty" }, h("h2", {}, "Nothing logged yet"), h("p", {}, "Everything that happens to a document is recorded here."))),
    )),
  );
}

function detailText(event) {
  const detail = event.detail || {};
  if (event.action === "field_edited") return [h("b", {}, pathLabel(detail.field)), `: ${detail.old ?? "empty"} → ${detail.new ?? "empty"}`];
  if (event.action === "field_confirmed") return [h("b", {}, pathLabel(detail.field)), `: ${detail.value ?? "empty"}`];
  if (event.action === "approved") return `Revision ${detail.revision}, values ${String(detail.content_hash).slice(0, 12)}`;
  if (event.action === "exported") return `${String(detail.format).toUpperCase()}, approved by ${detail.approved_by}`;
  if (event.action === "export_refused") return `${String(detail.format).toUpperCase()}: ${detail.reason === "not_approved" ? "not approved" : "values changed after approval"}`;
  if (event.action === "approval_voided") return detail.reason;
  if (event.action === "document_received") return `${detail.filename}, ${Math.max(1, Math.round(detail.bytes / 1024))} KB, sha256 ${String(detail.sha256).slice(0, 12)}`;
  if (event.action === "upload_refused") return `${detail.filename}: ${detail.code}`;
  if (event.action === "extraction_stored") return `${detail.values} values, ${detail.flagged} flagged, model ${detail.model}`;
  if (event.action === "reading_failed") return detail.message;
  if (event.action === "check_acknowledged") return detail.finding;
  if (event.action === "row_added" || event.action === "row_deleted") return `Line ${detail.row}`;
  return "";
}

// --- Keyboard ------------------------------------------------------------------------------------

function keyHelp() {
  const pairs = [
    ["J / ↓", "Next value"], ["K / ↑", "Previous value"], ["N", "Next value to check"], ["Enter", "Looks right, go to the next one"],
    ["E", "Edit this value"], ["Esc", "Cancel an edit, or back to the queue"], ["A", "Approve"], ["]", "Next document, once approved"], ["+ / −", "Zoom the page"], ["0", "Fit the page"], ["?", "This list"],
  ];
  const dialog = h("dialog", { id: "keys-dialog" }, h("div", { class: "dialog-body" },
    h("h2", {}, "Keyboard"),
    h("dl", { class: "keys" }, pairs.map(([key, what]) => [h("dt", {}, h("kbd", {}, key)), h("dd", {}, what)])),
    h("div", { class: "dialog-actions" }, h("button", { class: "btn", onclick: () => { dialog.close(); dialog.remove(); } }, "Close")),
  ));
  dialog.addEventListener("cancel", () => dialog.remove());
  document.body.append(dialog);
  dialog.showModal();
}

window.addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const target = event.target;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || document.querySelector("dialog[open]")) return;
  if (event.key === "?") { event.preventDefault(); keyHelp(); return; }

  if (state.view === "queue") {
    const rows = visibleDocuments();
    if (event.key === "j" || event.key === "ArrowDown") { event.preventDefault(); state.queueSelected = Math.min(rows.length - 1, state.queueSelected + 1); paintQueue(); }
    else if (event.key === "k" || event.key === "ArrowUp") { event.preventDefault(); state.queueSelected = Math.max(0, state.queueSelected - 1); paintQueue(); }
    else if (event.key === "Enter" && rows[state.queueSelected] && target.tagName !== "BUTTON") { location.hash = `#/doc/${rows[state.queueSelected].id}`; }
    else if (event.key === "u") { document.getElementById("file-input")?.click(); }
    return;
  }

  if (state.view !== "review") return;
  if (event.key === "Escape") { location.hash = "#/"; return; }
  if (event.key === "]" && state.doc?.status === "approved") { goToNextDocument(); return; }
  if (event.key === "+" || event.key === "=") { setZoom(state.zoom * 1.2); return; }
  if (event.key === "-") { setZoom(state.zoom / 1.2); return; }
  if (event.key === "0") { setZoom(1); return; }
  if (!reviewable()) return;
  const field = fieldMap().get(state.selected);
  if (event.key === "j" || event.key === "ArrowDown") { event.preventDefault(); move(1); }
  else if (event.key === "k" || event.key === "ArrowUp") { event.preventDefault(); move(-1); }
  else if (event.key === "n") { const next = nextFlaggedFrom(state.selected); if (next) select(next); else toast("Nothing left to check."); }
  else if (event.key === "e" && field) { event.preventDefault(); startEdit(); }
  else if (event.key === "a" && state.doc.status === "needs_review") { event.preventDefault(); approveDocument(); }
  else if (event.key === "Enter" && field && target.tagName !== "BUTTON" && target.tagName !== "A") {
    event.preventDefault();
    if (field.state === "edited" || field.state === "confirmed") { const next = nextFlaggedFrom(field.path); if (next) select(next); else move(1); }
    else confirmField(field);
  }
});

// --- Start ---------------------------------------------------------------------------------------

route();
