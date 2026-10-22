import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const APP_PATH = "app/frontend/app.js";
const CSS_PATH = "app/frontend/app.css";
const VALID_CODE = "0000000-00.2026.8.21.0001";
const TOKEN = "test-token";
const FIXED_NOW = Date.parse("2026-01-01T00:30:00.000Z");
const DAY_SECONDS = 24 * 60 * 60;
const timestampAtElapsedSeconds = (seconds, now = FIXED_NOW) =>
  new Date(now - seconds * 1000).toISOString();

const ids = [
  "search-form","process-code","process-code-error","bearer-token","token-error",
  "toggle-token","access-trigger","access-label","auth-panel","status","status-title",
  "status-detail","retry-search","request-process","result","search-submit",
  "copy-summary","new-search","action-feedback","result-code","result-class",
  "result-court","result-updated","result-context","parties-list","subjects-list",
  "header-data","movements-list","summary-body","summary-provenance","summary-eyebrow",
  "summary-title","case-data-title","movements-title",
  "history-trigger","history-panel","history-title","refresh-history","close-history",
  "history-status","history-list","open-existing-history","request-refresh",
  "confirm-modal","confirm-modal-title","confirm-modal-desc","confirm-refresh-submit",
  "confirm-refresh-cancel","refresh-from-result",
  "verify-token","auth-status","source-judit","source-datajud","datajud-status-hint",
  "judit-info","judit-info-wrap","judit-tooltip","datajud-info","datajud-info-wrap","datajud-tooltip",
  "api-connectivity","api-connectivity-label",
];

class ClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    for (const name of names) this.values.add(name);
  }

  remove(...names) {
    for (const name of names) this.values.delete(name);
  }

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }

  contains(name) {
    return this.values.has(name);
  }

  has(name) {
    return this.values.has(name);
  }
}

class Element {
  constructor(id) {
    this.id = id;
    this.hidden = ["history-panel", "confirm-modal", "judit-tooltip", "datajud-tooltip"].includes(id);
    this.disabled = id === "source-datajud";
    this.value = "";
    this.textContent = "";
    this.type = id === "bearer-token" ? "password" : id.startsWith("source-") ? "radio" : "";
    this.checked = id === "source-judit";
    this.name = id.startsWith("source-") ? "query-source" : "";
    this.children = [];
    this.firstChild = null;
    this.listeners = {};
    this.attributes = new Map();
    this.classList = new ClassList();
    this.focused = false;
    this.scrolled = false;
  }

  append(...nodes) {
    this.children.push(...nodes);
    this.firstChild = this.children[0] ?? null;
  }

  appendChild(node) {
    this.append(node);
    return node;
  }

  removeChild(node) {
    this.children = this.children.filter(candidate => candidate !== node);
    this.firstChild = this.children[0] ?? null;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener(type, handler) {
    this.listeners[type] = handler;
  }

  click() {
    const listener = this.listeners.click;
    if (typeof listener !== "function") throw new Error(`${this.id} has no click listener`);
    return listener({ target: this });
  }

  dispatch(type, event = {}) {
    switch (type) {
      case "keydown": return this.listeners.keydown?.(event);
      case "pointerenter": return this.listeners.pointerenter?.(event);
      case "pointerleave": return this.listeners.pointerleave?.(event);
      default: throw new Error(`unsupported synthetic event: ${type}`);
    }
  }

  focus() {
    this.focused = true;
    this.listeners.focus?.();
  }

  blur() {
    this.focused = false;
    this.listeners.blur?.();
  }

  scrollIntoView() {
    this.scrolled = true;
  }

  get renderedText() {
    return this.textContent + this.children
      .map(node => node.renderedText ?? node.textContent ?? "")
      .join("");
  }
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function readyPayload(overrides = {}) {
  return {
    code: VALID_CODE,
    class_name: "Classe",
    court: "TJRS",
    updated_at: "2026-09-18T12:00:00Z",
    summary_status: "available",
    summary: { markdown: "Resumo pronto", created_at: "2026-09-18T12:00:00Z" },
    parties: [],
    subjects: [],
    header: {},
    recent_steps: [{ title: "Sentença", text: "Movimento público", occurred_at: "2026-09-18T10:00:00Z" }],
    ...overrides,
  };
}

function createHarness(fetchResponses, { apiBase = "same-origin", hasExplicitCapabilities = false, now = FIXED_NOW } = {}) {
  const elements = Object.fromEntries(ids.map(id => [id, new Element(id)]));
  const requests = [];
  const capabilitiesRequests = [];
  const timers = new Map();
  const windowListeners = {};
  let fetchIndex = 0;
  let timerId = 0;
  let clipboardText = "";

  const navLinks = [
    { href: "#summary-title", listeners: {} },
    { href: "#case-data-title", listeners: {} },
    { href: "#movements-title", listeners: {} },
  ].map(link => ({
    getAttribute(name) { return name === "href" ? link.href : null; },
    addEventListener(type, handler) { link.listeners[type] = handler; },
    click() {
      const listener = link.listeners.click;
      if (typeof listener !== "function") throw new Error("navigation link has no click listener");
      return listener();
    },
    listeners: link.listeners,
  }));

  const document = {
    querySelector(selector) {
      assert.match(selector, /^#[a-z0-9-]+$/i, `unexpected selector: ${selector}`);
      return elements[selector.slice(1)];
    },
    querySelectorAll(selector) {
      if (selector === ".document-nav a") return navLinks;
      return [];
    },
    createElement(tag) {
      return new Element(tag);
    },
  };

  const context = {
    document,
    console,
    Date: class extends Date {
      static now() { return now; }
    },
    TypeError,
    URL,
    requestAnimationFrame: callback => callback(),
    navigator: {
      clipboard: {
        writeText: async value => {
          clipboardText = value;
        },
      },
    },
    window: {
      RPY_PUBLIC_CONFIG: { apiBase },
      crypto: { randomUUID: () => "7ddcc476-3c58-4c90-864a-0eacac9fd8b3" },
      location: { origin: "http://localhost" },
      matchMedia: () => ({ matches: false }),
      addEventListener: (type, handler) => {
        windowListeners[type] = handler;
      },
    },
    setTimeout: callback => {
      const id = ++timerId;
      timers.set(id, callback);
      return id;
    },
    clearTimeout: id => {
      timers.delete(id);
    },
    fetch: async (url, options = {}) => {
      if (url.endsWith("/capabilities") && !hasExplicitCapabilities) {
        capabilitiesRequests.push({ url, options });
        return jsonResponse(200, {
          authenticated: false,
          default_source: "judit",
          sources: {
            judit: { name: "Judit", configured: true, enabled: true, experimental: false },
            datajud: { name: "DataJud", configured: false, enabled: false, experimental: true, reason_unavailable: "DataJud desabilitado por padrão no servidor (DATAJUD_ENABLED=false)." },
          },
        });
      }
      requests.push({ url, options });
      const response = fetchResponses[Math.min(fetchIndex, fetchResponses.length - 1)];
      fetchIndex += 1;
      if (response instanceof Error) throw response;
      return response;
    },
  };

  vm.runInNewContext(fs.readFileSync(APP_PATH, "utf8"), context);

  return {
    elements,
    requests,
    capabilitiesRequests,
    clipboard: () => clipboardText,
    hasTimer: () => timers.size > 0,
    async tick() {
      assert.ok(timers.size > 0, "expected a scheduled timer");
      const [id, callback] = timers.entries().next().value;
      timers.delete(id);
      await callback();
    },
    async search({ code = VALID_CODE, token = TOKEN } = {}) {
      elements["process-code"].value = code;
      elements["bearer-token"].value = token;
      await elements["search-form"].listeners.submit({ preventDefault() {} });
    },
    async click(id) {
      if (elements[id].disabled) return;
      await elements[id].click();
    },
    async dispatch(id, type, event = {}) {
      await elements[id].dispatch(type, event);
    },
    async changeRadio(id) {
      assert.equal(elements[id].type, "radio");
      elements[id].checked = true;
      for (const other of Object.values(elements)) {
        if (other !== elements[id] && other.name === elements[id].name) {
          other.checked = false;
        }
      }
      elements[id].listeners.change?.({ target: elements[id] });
    },
    async clickNavLink(index) {
      await navLinks[index].click();
    },
    pagehide() {
      windowListeners.pagehide?.();
    },
  };
}

async function scenario(name, body) {
  try {
    await body();
    console.log(`ok - ${name}`);
  } catch (error) {
    error.message = `${name}: ${error.message}`;
    throw error;
  }
}

async function renderHistoryItem(updatedAt, now = FIXED_NOW) {
  const ui = createHarness([
    jsonResponse(200, {
      items: [{
        code: VALID_CODE,
        court: "TJRS",
        class_name: "Procedimento Comum",
        updated_at: updatedAt,
        acquisition_status: "completed",
      }],
      total: 1,
    }),
    jsonResponse(200, { items: [] }),
  ], { now });
  ui.elements["bearer-token"].value = TOKEN;
  await ui.click("history-trigger");
  return ui;
}

await scenario("configured Pages API base uses the backend origin", async () => {
  const ui = createHarness(
    [jsonResponse(200, { classification: "read_existing", process_exists: true })],
    { apiBase: "https://api-staging.rpy.test" },
  );
  await ui.search();
  assert.equal(
    ui.requests[0].url,
    `https://api-staging.rpy.test/processes/${encodeURIComponent(VALID_CODE)}/status`,
  );
});

await scenario("unconfigured Pages API fails closed without network I/O", async () => {
  const ui = createHarness([jsonResponse(500, {})], { apiBase: null });
  await ui.search();
  assert.equal(ui.requests.length, 0);
  assert.ok(ui.elements["status-title"].textContent.includes("API de staging"));
});

await scenario("successful lookup renders public data, focuses result and uses bearer header", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload()),
  ]);

  await ui.search();

  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, `/processes/${encodeURIComponent(VALID_CODE)}/status`);
  assert.equal(ui.requests[0].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(ui.requests[0].options.headers.Accept, "application/json");

  await ui.click("open-existing-history");

  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}`);
  assert.equal(ui.requests[1].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(ui.requests[1].options.headers.Accept, "application/json");
  assert.doesNotMatch(ui.requests[1].url, /test-token/);
  assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto"));
  assert.equal(ui.elements["summary-eyebrow"].textContent, "SÍNTESE GERADA POR IA");
  assert.ok(ui.elements["summary-provenance"].textContent.includes("gerado por IA"));
  assert.ok(ui.elements["movements-list"].renderedText.includes("Movimento público"));
  assert.equal(ui.elements["result"].hidden, false);
  assert.equal(ui.elements["result-code"].focused, true);
  assert.equal(ui.elements["result"].scrolled, true);
});

await scenario("published iaSummary string is rendered and copied as readable text", async () => {
  const markdown = [
    "# Resumo do processo",
    "## Síntese",
    "A audiência foi designada para 03/03/2027.",
    "## Movimentações",
    "- Em 01/09/2026, audiência designada para 03/03/2027.",
  ].join("\n");
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload({ summary: null, iaSummary: markdown })),
  ]);

  await ui.search();
  await ui.click("open-existing-history");

  assert.ok(ui.elements["summary-body"].renderedText.includes("A audiência foi designada"));
  assert.equal(ui.elements["copy-summary"].hidden, false);
  await ui.click("copy-summary");
  assert.ok(ui.clipboard().includes("Resumo do processo"));
  assert.ok(ui.clipboard().includes("- Em 01/09/2026, audiência designada para 03/03/2027."));
  assert.doesNotMatch(ui.clipboard(), /<ProcessHeader|<Party/);
});

await scenario("structured iaSummary JSON is not interpreted as Markdown", async () => {
  const structured = {
    schema_version: 2,
    process: { cnj: VALID_CODE, representatives: [] },
    summary: { synthesis: "JSON deve permanecer estruturado." },
  };
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload({ summary: null, iaSummary: structured })),
  ]);

  await ui.search();
  await ui.click("open-existing-history");

  assert.equal(ui.elements["copy-summary"].hidden, true);
  assert.equal(ui.elements["summary-eyebrow"].hidden, true);
  assert.ok(ui.elements["summary-body"].renderedText.includes("sem resumo publicado"));
  assert.doesNotMatch(ui.elements["summary-body"].renderedText, /JSON deve permanecer estruturado|schema_version/);
});

await scenario("provider-free demo summary is labeled as synthetic instead of AI-generated", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload({
      summary: {
        markdown: "Resumo pronto",
        created_at: "2026-09-18T12:00:00Z",
        model: "local-demo-no-provider",
      },
    })),
  ]);

  await ui.search();
  await ui.click("open-existing-history");

  assert.equal(ui.elements["summary-eyebrow"].textContent, "SÍNTESE DE DEMONSTRAÇÃO");
  assert.ok(ui.elements["summary-provenance"].textContent.includes("Resumo sintético local"));
  assert.ok(ui.elements["summary-provenance"].textContent.includes("Nenhum provedor externo"));
  assert.doesNotMatch(ui.elements["summary-provenance"].textContent, /gerado por IA/i);
});

await scenario("processing summary remains readable and transitions to published summary", async () => {
  const processing = readyPayload({ summary_status: "processing", summary: null });
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, processing),
    jsonResponse(200, readyPayload()),
  ]);

  await ui.search();
  await ui.click("open-existing-history");

  assert.ok(ui.elements["summary-body"].renderedText.includes("sendo preparado"));
  assert.equal(ui.elements["summary-eyebrow"].hidden, true);
  assert.equal(ui.elements["result"].hidden, false);
  assert.equal(ui.hasTimer(), true);

  await ui.tick();

  assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto"));
  assert.equal(ui.hasTimer(), false);
  assert.ok(ui.elements["status-title"].textContent.includes("Resumo publicado"));
});

await scenario("a new lookup clears stale result before a 404 outcome", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload()),
    jsonResponse(404, { detail: "not found" }),
  ]);

  await ui.search();
  await ui.click("open-existing-history");
  assert.equal(ui.elements["result"].hidden, false);

  await ui.search({ code: "0000000-00.2026.8.21.0002" });

  assert.equal(ui.elements["result"].hidden, true);
  assert.ok(ui.elements["status-title"].textContent.includes("ainda não disponível"));
  assert.equal(ui.elements["request-process"].hidden, false);
});

await scenario("missing process acquisition starts only after explicit user action", async () => {
  const ui = createHarness([
    jsonResponse(404, { detail: "not found" }),
    jsonResponse(202, { status: "pending", created: true }),
  ]);

  await ui.search();

  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].options.method, undefined);

  await ui.click("request-process");

  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}/request`);
  assert.equal(ui.requests[1].options.method, "POST");
  assert.equal(ui.requests[1].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(ui.hasTimer(), true);
});

await scenario("availability polling is bounded and stops after the documented attempt limit", async () => {
  const responses = [
    jsonResponse(404, { detail: "not found" }),
    jsonResponse(202, { status: "pending", created: true }),
    ...Array.from({ length: 20 }, () => jsonResponse(404, { detail: "not found" })),
  ];
  const ui = createHarness(responses);

  await ui.search();
  await ui.click("request-process");
  for (let attempt = 0; attempt < 20; attempt += 1) {
    assert.equal(ui.hasTimer(), true);
    await ui.tick();
  }

  assert.equal(ui.hasTimer(), false);
  assert.ok(ui.elements["status-detail"].textContent.includes("não é necessário manter esta página aberta"));
  assert.equal(ui.requests.length, 22);
});

await scenario("invalid CNJ and missing credential fail locally before network I/O", async () => {
  const invalid = createHarness([jsonResponse(500, {})]);
  await invalid.search({ code: "123", token: TOKEN });
  assert.equal(invalid.requests.length, 0);
  assert.equal(invalid.elements["process-code"].getAttribute("aria-invalid"), "true");
  assert.equal(invalid.elements["process-code"].focused, true);

  const missingCredential = createHarness([jsonResponse(500, {})]);
  await missingCredential.search({ token: "" });
  assert.equal(missingCredential.requests.length, 0);
  assert.equal(missingCredential.elements["bearer-token"].getAttribute("aria-invalid"), "true");
  assert.equal(missingCredential.elements["bearer-token"].focused, true);
  assert.equal(missingCredential.elements["auth-panel"].hidden, false);
});

await scenario("unauthorized response does not reveal process existence and returns focus to credential", async () => {
  const ui = createHarness([jsonResponse(401, { detail: "unauthorized" })]);

  await ui.search();

  assert.equal(ui.elements["result"].hidden, true);
  assert.ok(ui.elements["status-title"].textContent.includes("autorizar"));
  assert.equal(ui.elements["bearer-token"].focused, true);
  assert.equal(ui.elements["bearer-token"].getAttribute("aria-invalid"), "true");
  assert.doesNotMatch(ui.elements["status-detail"].textContent, /existe|não existe/i);
});

await scenario("network failure is distinguishable from an application error", async () => {
  const network = createHarness([new TypeError("network unavailable")]);
  await network.search();
  assert.ok(network.elements["status-title"].textContent.includes("Sem conexão"));
  assert.equal(network.elements["retry-search"].hidden, false);

  const server = createHarness([jsonResponse(500, {})]);
  await server.search();
  assert.ok(server.elements["status-title"].textContent.includes("não pôde ser concluída"));
  assert.equal(server.elements["retry-search"].hidden, false);
});

await scenario("allowlisted JSX renders as inert DOM and unsupported markup stays literal", async () => {
  const markdown = [
    "# Resumo do processo",
    '<ProcessHeader className="process-header">',
    "- Processo: 0000000-00.2026.8.21.0010",
    "</ProcessHeader>",
    "## Partes",
    '<Party name="Maria da Silva" />',
    '<img src=x onerror=alert(1)>',
  ].join("\n");
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload({
      code: "0000000-00.2026.8.21.0010",
      summary: { markdown },
      parties: [{ name: "Maria da Silva" }],
      recent_steps: [],
    })),
  ]);

  await ui.search({ code: "0000000-00.2026.8.21.0010" });
  await ui.click("open-existing-history");

  const text = ui.elements["summary-body"].renderedText;
  assert.ok(text.includes("Maria da Silva"));
  assert.ok(text.includes("Processo: 0000000-00.2026.8.21.0010"));
  assert.ok(text.includes("<img src=x onerror=alert(1)>"));
  assert.ok(ui.elements["summary-body"].children.some(node => node.className === "summary-process-header"));

  await ui.click("copy-summary");
  assert.ok(ui.clipboard().includes("Resumo do processo"));
  assert.ok(ui.clipboard().includes("Maria da Silva"));
  assert.doesNotMatch(ui.clipboard(), /<ProcessHeader|<Party/);
});

await scenario("new search and pagehide cancel polling without erasing the in-memory credential", async () => {
  const processing = readyPayload({ summary_status: "processing", summary: null });
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, processing),
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, processing),
  ]);

  await ui.search();
  await ui.click("open-existing-history");
  assert.equal(ui.hasTimer(), true);

  await ui.click("new-search");
  assert.equal(ui.hasTimer(), false);
  assert.equal(ui.elements["result"].hidden, true);
  assert.equal(ui.elements["process-code"].value, "");
  assert.equal(ui.elements["bearer-token"].value, TOKEN);

  await ui.search();
  await ui.click("open-existing-history");
  assert.equal(ui.hasTimer(), true);
  ui.pagehide();
  assert.equal(ui.hasTimer(), false);
});

await scenario("CNJ heading uses sans-serif tabular numerals for legibility", async () => {
  const css = fs.readFileSync(CSS_PATH, "utf8");
  assert.match(css, /\.process-header h2\{[^}]*ui-sans-serif[^}]*font-variant-numeric:tabular-nums/);
});

await scenario("credential toggle updates accessible label and markup includes field error alert roles", async () => {
  const html = fs.readFileSync("app/frontend/index.html", "utf8");
  assert.match(html, /id="process-code-error"[^>]*role="alert"/);
  assert.match(html, /id="token-error"[^>]*role="alert"/);
  assert.match(html, /id="toggle-token"[^>]*aria-controls="bearer-token"/);

  const ui = createHarness([jsonResponse(200, readyPayload())]);
  await ui.click("toggle-token");
  assert.equal(ui.elements["toggle-token"].getAttribute("aria-label"), "Ocultar token de acesso");
  await ui.click("toggle-token");
  assert.equal(ui.elements["toggle-token"].getAttribute("aria-label"), "Mostrar token de acesso");
});

await scenario("bearer token field submits search on Enter", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
  ]);
  ui.elements["process-code"].value = VALID_CODE;
  ui.elements["bearer-token"].value = TOKEN;

  const handler = ui.elements["bearer-token"].listeners.keydown;
  assert.equal(typeof handler, "function");
  let prevented = false;
  await handler({ key: "Enter", preventDefault() { prevented = true; } });

  assert.equal(prevented, true);
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, `/processes/${encodeURIComponent(VALID_CODE)}/status`);
});

await scenario("section navigation links shift focus to target headings", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload()),
  ]);
  await ui.search();
  await ui.click("open-existing-history");
  await ui.clickNavLink(1);
  assert.equal(ui.elements["case-data-title"].focused, true);
});

await scenario("copy button resets label cleanly on rapid repeated clicks", async () => {
  const ui = createHarness([
    jsonResponse(200, { classification: "read_existing", process_exists: true }),
    jsonResponse(200, readyPayload()),
  ]);
  await ui.search();
  await ui.click("open-existing-history");

  await ui.click("copy-summary");
  assert.equal(ui.elements["copy-summary"].textContent, "Resumo copiado");

  await ui.click("copy-summary");
  assert.equal(ui.elements["copy-summary"].textContent, "Resumo copiado");

  await ui.tick();
  assert.equal(ui.elements["copy-summary"].textContent, "Copiar resumo");
});

// Bloco 153 - Novas verificações comportamentais obrigatórias

await scenario("consulta com histórico existente exibe aviso e opções sem iniciar nova aquisição", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
      acquisition_status: "completed",
      last_acquisition_at: "2026-09-18T10:00:00Z",
      acquired_today: false,
    }),
  ]);
  await ui.search();
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, `/processes/${encodeURIComponent(VALID_CODE)}/status`);
  assert.ok(ui.elements["status-title"].textContent.includes("Processo já consultado"));
  assert.equal(ui.elements["open-existing-history"].hidden, false);
  assert.equal(ui.elements["request-refresh"].hidden, false);
  assert.equal(ui.elements["request-process"].hidden, true);
});

await scenario("abertura do histórico exibe dados armazenados sem chamar a Judit", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
      last_acquisition_at: "2026-09-18T10:00:00Z",
    }),
    jsonResponse(200, readyPayload()),
  ]);
  await ui.search();
  await ui.click("open-existing-history");
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}`);
  assert.equal(ui.requests[1].options.method, undefined);
  assert.equal(ui.elements["result"].hidden, false);
  assert.ok(ui.elements["summary-body"].renderedText.includes("Resumo pronto"));
});

await scenario("CNJ ainda não adquirido permite primeira aquisição pelo fluxo normal", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "acquisition_required",
      process_exists: false,
    }),
    jsonResponse(202, { status: "pending", created: true }),
  ]);
  await ui.search();
  assert.equal(ui.elements["request-process"].hidden, false);
  assert.equal(ui.elements["open-existing-history"].hidden, true);
  assert.equal(ui.elements["request-refresh"].hidden, true);

  await ui.click("request-process");
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}/request`);
  assert.equal(ui.requests[1].options.method, "POST");
  assert.equal(ui.requests[1].options.headers["X-Judit-Authorization-Token"], undefined);
  assert.equal(ui.hasTimer(), true);
});

await scenario("aquisição em andamento identifica estado e inicia acompanhamento", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "acquisition_pending",
      process_exists: false,
      acquisition_status: "processing",
    }),
  ]);
  await ui.search();
  assert.ok(ui.elements["status-title"].textContent.includes("em andamento"));
  assert.equal(ui.hasTimer(), true);
});

await scenario("falha ambígua impede repetição automática e apresenta aviso apropriado", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "ambiguous_failure",
      process_exists: false,
      acquisition_status: "failed_ambiguous",
    }),
  ]);
  await ui.search();
  assert.ok(ui.elements["status-title"].textContent.includes("incerto"));
  assert.equal(ui.elements["request-process"].hidden, true);
  assert.equal(ui.elements["request-refresh"].hidden, true);
  assert.equal(ui.elements["open-existing-history"].hidden, true);
  assert.equal(ui.hasTimer(), false);
});

await scenario("confirmação cancelada fecha o modal sem gerar autorização ou chamada Judit", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
    }),
  ]);
  await ui.search();
  await ui.click("request-refresh");
  assert.equal(ui.elements["confirm-modal"].hidden, false);

  await ui.click("confirm-refresh-cancel");
  assert.equal(ui.elements["confirm-modal"].hidden, true);
  assert.equal(ui.requests.length, 1);
});

await scenario("confirmação aceita gera autorização e executa aquisição com token", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
    }),
    jsonResponse(201, {
      authorization_token: "auth-single-use-token-abc",
      expires_at: "2026-10-15T22:30:00Z",
    }),
    jsonResponse(202, { status: "pending", created: true }),
  ]);
  await ui.search();
  await ui.click("request-refresh");
  assert.equal(ui.elements["confirm-modal"].hidden, false);

  await ui.click("confirm-refresh-submit");
  assert.equal(ui.elements["confirm-modal"].hidden, true);

  assert.equal(ui.requests.length, 3);
  assert.equal(ui.requests[1].url, `/processes/${encodeURIComponent(VALID_CODE)}/authorizations`);
  assert.equal(ui.requests[1].options.method, "POST");

  assert.equal(ui.requests[2].url, `/processes/${encodeURIComponent(VALID_CODE)}/request`);
  assert.equal(ui.requests[2].options.method, "POST");
  assert.equal(
    ui.requests[2].options.headers["X-Judit-Authorization-Token"],
    "auth-single-use-token-abc",
  );
  assert.equal(ui.hasTimer(), true);
});

await scenario("token expirado ou consumido interrompe e informa o usuário", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
    }),
    jsonResponse(201, {
      authorization_token: "expired-token",
    }),
    jsonResponse(409, {
      detail: "token de autorização expirado ou já consumido",
    }),
  ]);
  await ui.search();
  await ui.click("request-refresh");
  await ui.click("confirm-refresh-submit");

  assert.equal(ui.hasTimer(), false);
  assert.ok(ui.elements["status-title"].textContent.includes("não autorizada ou expirada"));
  assert.equal(ui.elements["confirm-refresh-submit"].disabled, false);
});

await scenario("duplo clique bloqueia múltiplos disparos enquanto aguarda resposta", async () => {
  let resolveAuth;
  const slowAuthPromise = new Promise(resolve => {
    resolveAuth = resolve;
  });

  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
    }),
    {
      ok: true,
      status: 201,
      json: async () => {
        await slowAuthPromise;
        return { authorization_token: "auth-123" };
      },
    },
    jsonResponse(202, { status: "pending", created: true }),
  ]);

  await ui.search();
  await ui.click("request-refresh");

  const clickPromise1 = ui.click("confirm-refresh-submit");
  assert.equal(ui.elements["confirm-refresh-submit"].disabled, true);

  const clickPromise2 = ui.click("confirm-refresh-submit");
  resolveAuth();
  await Promise.all([clickPromise1, clickPromise2]);

  assert.equal(ui.requests.length, 3);
});

await scenario("resposta obsoleta digitação rápida ou nova consulta descarta retorno anterior", async () => {
  let resolveStatus1;
  const statusPromise1 = new Promise(resolve => {
    resolveStatus1 = resolve;
  });

  const CODE_1 = "0000000-00.2026.8.21.0001";
  const CODE_2 = "0000000-00.2026.8.21.0002";

  const ui = createHarness([
    {
      ok: true,
      status: 200,
      json: async () => {
        await statusPromise1;
        return { code: CODE_1, classification: "read_existing", process_exists: true };
      },
    },
    jsonResponse(200, { code: CODE_2, classification: "acquisition_required", process_exists: false }),
  ]);

  const search1 = ui.search({ code: CODE_1 });
  const search2 = ui.search({ code: CODE_2 });

  await search2;
  assert.equal(ui.elements["request-process"].hidden, false);

  resolveStatus1();
  await search1;

  assert.equal(ui.elements["request-process"].hidden, false);
  assert.equal(ui.elements["open-existing-history"].hidden, true);
});

await scenario("listagem de histórico exibe processos do tenant e permite abrir um item", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      items: [
        {
          code: VALID_CODE,
          court: "TJRS",
          class_name: "Procedimento Comum",
          updated_at: timestampAtElapsedSeconds(3 * DAY_SECONDS),
          acquisition_status: "completed",
        },
      ],
      total: 1,
    }),
    jsonResponse(200, { items: [] }),
    jsonResponse(200, readyPayload()),
  ]);

  ui.elements["bearer-token"].value = TOKEN;
  await ui.click("history-trigger");

  const historyText = ui.elements["history-list"].children[0].renderedText;
  assert.match(historyText, /Consultado há 3 dias/);
  console.log(`history sample: ${historyText}`);
  assert.equal(ui.elements["history-panel"].hidden, false);
  assert.equal(ui.requests.length, 2);
  assert.equal(ui.requests[0].url, "/processes?limit=50");
  assert.equal(ui.requests[1].url, "/processes/datajud/history");
  assert.equal(ui.elements["history-list"].children.length, 1);

  const historyItem = ui.elements["history-list"].children[0];
  await historyItem.listeners.click();

  assert.equal(ui.elements["history-panel"].hidden, true);
  assert.equal(ui.requests.length, 3);
  assert.equal(ui.requests[2].url, `/processes/${encodeURIComponent(VALID_CODE)}`);
  assert.equal(ui.elements["result"].hidden, false);
});

await scenario("relative history labels use a fixed clock and pt-BR elapsed thresholds", async () => {
  const cases = [
    { name: "future is clamped to now", elapsedSeconds: -1, expected: "agora" },
    { name: "under one minute", elapsedSeconds: 59, expected: "agora" },
    { name: "minute boundary", elapsedSeconds: 60, expected: "há 1 minuto" },
    { name: "before day boundary", elapsedSeconds: DAY_SECONDS - 1, expected: "há 23 horas" },
    { name: "day boundary", elapsedSeconds: DAY_SECONDS, expected: "ontem" },
    { name: "before 30-day month threshold", elapsedSeconds: 30 * DAY_SECONDS - 1, expected: "há 29 dias" },
    { name: "30-day month threshold", elapsedSeconds: 30 * DAY_SECONDS, expected: "mês passado" },
    { name: "before 365-day year threshold", elapsedSeconds: 365 * DAY_SECONDS - 1, expected: "há 12 meses" },
    { name: "365-day year threshold", elapsedSeconds: 365 * DAY_SECONDS, expected: "ano passado" },
  ];

  for (const testCase of cases) {
    const ui = await renderHistoryItem(timestampAtElapsedSeconds(testCase.elapsedSeconds));
    const renderedText = ui.elements["history-list"].children[0].renderedText;
    const match = renderedText.match(/Consultado (.+?)Armazenado/);
    assert.ok(match, `${testCase.name}: expected a relative-time label in ${renderedText}`);
    assert.equal(match[1], testCase.expected, testCase.name);
    console.log(`relative-history ${testCase.name}: Consultado ${match[1]}`);
  }

  const invalidDateUi = await renderHistoryItem("not-a-date");
  const invalidDateText = invalidDateUi.elements["history-list"].children[0].renderedText;
  assert.doesNotMatch(invalidDateText, /Consultado /);
  console.log("relative-history invalid timestamp: no consultation label");
});

await scenario("consulta realizada no mesmo dia destaca aviso de consulta recente", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      code: VALID_CODE,
      classification: "read_existing",
      process_exists: true,
      last_acquisition_at: "2026-10-15T15:00:00Z",
      acquired_today: true,
    }),
  ]);
  await ui.search();
  assert.ok(ui.elements["status-detail"].textContent.includes("America/Sao_Paulo"));
  assert.ok(ui.elements["status-detail"].textContent.includes("consultado hoje"));
});

await scenario("Judit é a fonte padrão selecionada na inicialização", async () => {
  const ui = createHarness([]);
  assert.equal(ui.elements["source-judit"].checked, true);
  assert.equal(ui.elements["source-datajud"].checked, false);
});

await scenario("estado real de conectividade reflete a resposta da API", async () => {
  const ui = createHarness([]);
  await new Promise(r => setTimeout(r, 0));
  assert.equal(ui.elements["api-connectivity-label"].textContent, "API RagV conectada");

  const offline = createHarness([new Error("offline")], { hasExplicitCapabilities: true });
  await new Promise(r => setTimeout(r, 0));
  assert.equal(offline.elements["api-connectivity-label"].textContent, "API RagV indisponível");
  assert.equal(offline.elements["api-connectivity"].classList.has("offline"), true);
});

await scenario("seleção dos provedores fica em Configurar acesso com tooltips exatos", async () => {
  const html = fs.readFileSync("app/frontend/index.html", "utf8");
  assert.match(html, /id="auth-panel"[^>]*hidden/);
  assert.equal((html.match(/class="provider-info"/g) || []).length, 2);
  assert.match(html, /Consulta processual para uso operacional\. Pode gerar cobrança conforme o serviço contratado\./);
  assert.match(html, /API pública do CNJ para consulta de metadados processuais\. Pode auxiliar em testes de conectividade, conforme a cobertura disponível\./);
  assert.equal((html.match(/aria-describedby="(?:judit|datajud)-tooltip"/g) || []).length, 2);
});

await scenario("tooltips de provedor abrem por clique, foco e mouse e fecham por Escape", async () => {
  const ui = createHarness([]);
  const tooltip = ui.elements["judit-tooltip"];
  assert.equal(tooltip.hidden, true);
  await ui.click("judit-info");
  assert.equal(tooltip.hidden, false);
  assert.equal(ui.elements["judit-info"].getAttribute("aria-expanded"), "true");
  await ui.dispatch("judit-info", "keydown", { key: "Escape" });
  assert.equal(tooltip.hidden, true);
  ui.elements["judit-info"].focus();
  assert.equal(tooltip.hidden, false);
  ui.elements["judit-info"].blur();
  assert.equal(tooltip.hidden, true);
  await ui.dispatch("datajud-info", "pointerenter");
  assert.equal(ui.elements["datajud-tooltip"].hidden, false);
  await ui.dispatch("datajud-info-wrap", "pointerleave");
  assert.equal(ui.elements["datajud-tooltip"].hidden, true);
  assert.equal(ui.requests.length, 0);
});

await scenario("autenticação válida atualiza indicador com estado authenticated", async () => {
  const ui = createHarness([
    jsonResponse(200, { ok: true, authenticated: true, tenant_id: "tenant-1" }),
  ]);
  ui.elements["bearer-token"].value = TOKEN;
  await ui.click("verify-token");
  assert.equal(ui.elements["access-trigger"].classList.has("authenticated"), true);
  assert.equal(ui.elements["access-trigger"].classList.has("invalid"), false);
  assert.ok(ui.elements["auth-status"].textContent.toLowerCase().includes("autenticad"));
  assert.equal(ui.requests.length, 1);
  assert.equal(ui.requests[0].url, "/auth/verify");
  assert.equal(ui.requests[0].options.headers.Authorization, `Bearer ${TOKEN}`);
});

await scenario("autenticação inválida atualiza indicador com estado invalid", async () => {
  const ui = createHarness([
    jsonResponse(401, { detail: "Token de acesso inválido ou expirado." }),
  ]);
  ui.elements["bearer-token"].value = "bad-token";
  await ui.click("verify-token");
  assert.equal(ui.elements["access-trigger"].classList.has("invalid"), true);
  assert.equal(ui.elements["access-trigger"].classList.has("authenticated"), false);
  assert.ok(ui.elements["auth-status"].textContent.toLowerCase().includes("recusada"));
});

await scenario("indicador verde somente após validação real pelo backend", async () => {
  const ui = createHarness([
    jsonResponse(200, { ok: true, authenticated: true, tenant_id: "tenant-1" }),
  ]);
  ui.elements["bearer-token"].value = "some-typing-in-progress";
  assert.equal(ui.elements["access-trigger"].classList.has("authenticated"), false);
  assert.equal(ui.elements["access-trigger"].classList.has("invalid"), false);

  ui.elements["bearer-token"].value = TOKEN;
  await ui.click("verify-token");
  assert.equal(ui.elements["access-trigger"].classList.has("authenticated"), true);
});

await scenario("DataJud submits an idempotent job and renders its queued API result", async () => {
  const ui = createHarness([
    jsonResponse(200, { sources: { datajud: { name: "DataJud", configured: true, enabled: true } } }),
    jsonResponse(202, { job_id: "9b765ab6-e6fa-4d9a-9943-22f24c516c08", status: "pending" }),
    jsonResponse(202, { job_id: "9b765ab6-e6fa-4d9a-9943-22f24c516c08", code: VALID_CODE, status: "processing" }),
    jsonResponse(200, { code: VALID_CODE, source: "datajud", court: "TJRS", class_name: "Classe pública", subjects: [], parties: [] }),
  ], { hasExplicitCapabilities: true });
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(ui.elements["source-datajud"].disabled, false);
  assert.equal(ui.elements["datajud-status-hint"].textContent, "");
  await ui.changeRadio("source-datajud");
  await ui.search();
  assert.equal(ui.requests[1].options.method, "POST");
  assert.equal(ui.requests[1].url, "/processes/datajud/jobs");
  assert.equal(ui.requests[1].options.headers.Authorization, `Bearer ${TOKEN}`);
  assert.match(ui.requests[1].options.headers["Idempotency-Key"], /^datajud-/);
  assert.deepEqual(JSON.parse(ui.requests[1].options.body), { code: VALID_CODE });
  assert.equal(ui.requests[2].url, "/processes/datajud/jobs/status");
  assert.deepEqual(JSON.parse(ui.requests[2].options.body), { code: VALID_CODE, job_id: "9b765ab6-e6fa-4d9a-9943-22f24c516c08" });
  assert.equal(ui.hasTimer(), true);
  await ui.tick();
  assert.equal(ui.requests[3].url, "/processes/datajud/jobs/status");
  assert.equal(ui.elements["result-code"].textContent, VALID_CODE);
  assert.equal(ui.elements["result-court"].textContent, "TJRS");
});

await scenario("DataJud independente recuperado do histórico mostra apenas metadados persistidos", async () => {
  const ui = createHarness([
    jsonResponse(200, { items: [] }),
    jsonResponse(200, { items: [{ job_id: "c6c8880b-1196-4bd2-9755-8aeadfb5f013", code: VALID_CODE, status: "completed", created_at: "2026-10-16T12:00:00Z", metadata: { source: "datajud", court: "TJRS", class_name: "Classe persistida", subjects: [], parties: [] } }] }),
  ]);
  ui.elements["bearer-token"].value = TOKEN;
  await ui.click("history-trigger");
  const row = ui.elements["history-list"].children[0];
  assert.match(row.renderedText, /DataJud/);
  await row.listeners.click();
  assert.equal(ui.elements["result-class"].textContent, "Classe persistida");
  assert.equal(ui.elements["result-court"].textContent, "TJRS");
  assert.equal(ui.requests.length, 2);
});

await scenario("DataJud desabilitado não troca silenciosamente a seleção para Judit", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      authenticated: false,
      default_source: "judit",
      sources: {
        judit: { name: "Judit", configured: true, enabled: true, experimental: false },
        datajud: { name: "DataJud", configured: false, enabled: false, experimental: true },
      },
    }),
  ], { hasExplicitCapabilities: true });
  ui.elements["source-datajud"].checked = true;
  ui.elements["source-judit"].checked = false;
  await new Promise(r => setTimeout(r, 10));
  assert.equal(ui.elements["source-datajud"].disabled, true);
  assert.equal(ui.elements["source-datajud"].checked, true);
  assert.equal(ui.elements["source-judit"].checked, false);
});

await scenario("DataJud desabilitado por padrão no servidor", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      authenticated: false,
      default_source: "judit",
      sources: {
        judit: { name: "Judit", configured: true, enabled: true, experimental: false },
        datajud: {
          name: "DataJud",
          configured: false,
          enabled: false,
          experimental: true,
          reason_unavailable: "DataJud desabilitado por padrão no servidor (DATAJUD_ENABLED=false).",
        },
      },
    }),
  ], { hasExplicitCapabilities: true });
  await new Promise(r => setTimeout(r, 10));
  assert.equal(ui.elements["source-datajud"].disabled, true);
  const statusHint = ui.elements["datajud-status-hint"].textContent;
  assert.equal(statusHint, "DataJud desabilitado por padrão no servidor (DATAJUD_ENABLED=false).");
  assert.ok(!statusHint.includes("DATAJUD_API_KEY"));
  assert.ok(!statusHint.includes("JUDIT_API_KEY"));
  assert.ok(!statusHint.toLowerCase().includes("secret"));
});

await scenario("DataJud sem autorização de uso no servidor permanece desabilitado", async () => {
  const ui = createHarness([
    jsonResponse(200, {
      authenticated: false,
      default_source: "judit",
      sources: {
        judit: { name: "Judit", configured: true, enabled: true, experimental: false },
        datajud: {
          name: "DataJud",
          configured: false,
          enabled: false,
          experimental: true,
          reason_unavailable: "Uso de DataJud não autorizado explicitamente (DATAJUD_AUTHORIZED_USE=false).",
        },
      },
    }),
  ], { hasExplicitCapabilities: true });
  await new Promise(r => setTimeout(r, 10));

  assert.equal(ui.elements["source-datajud"].disabled, true);
  const statusHint = ui.elements["datajud-status-hint"].textContent;
  assert.equal(statusHint, "Uso de DataJud não autorizado explicitamente (DATAJUD_AUTHORIZED_USE=false).");
  assert.ok(!statusHint.includes("DATAJUD_API_KEY"));
  assert.ok(!statusHint.includes("JUDIT_API_KEY"));
  assert.ok(!statusHint.toLowerCase().includes("secret"));
});

await scenario("ausência de exposição de segredos em tela ou requisições", async () => {
  const ui = createHarness([]);
  const htmlContent = [
    ui.elements["auth-status"].textContent,
    ui.elements["datajud-status-hint"].textContent,
  ].join(" ");
  assert.ok(!htmlContent.includes("DATAJUD_API_KEY"));
  assert.ok(!htmlContent.includes("JUDIT_API_KEY"));
  assert.ok(!htmlContent.includes("secret"));
});

await scenario("troca de fonte não dispara aquisição paga ou consulta automática", async () => {
  const ui = createHarness([]);
  ui.elements["source-datajud"].disabled = false;
  await ui.changeRadio("source-datajud");
  assert.equal(ui.requests.length, 0);
  await ui.changeRadio("source-judit");
  assert.equal(ui.requests.length, 0);
});

console.log("frontend behavior harness: PASS");

