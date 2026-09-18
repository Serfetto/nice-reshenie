/* Консоль аналитика ВКД. Обычный JS без сборки. API: /api/a/* (assessment), /api/i/* (ingest). */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { mode: "replay", run: null, res: null, sel: null, alerts: [], unread: 0, prevDuration: null, tab: "map" };

const CLASS_COLOR = { critical: "#d64545", undesirable: "#e0a030", acceptable: "#3a9d5d", no_data: "#8a929c" };
const CLASS_TEXT = { critical: "критично", undesirable: "нежелательно", acceptable: "приемлемо", no_data: "нет данных" };
const STATUS_TEXT = { preferred: "предпочтительно", equivalent: "равнозначно лучшему", worse: "хуже",
  not_recommended: "не рекомендуется", requires_review: "требует проверки" };
const REC_TEXT = { preferred: "Предпочтительное окно", equivalent: "Лучшие окна равнозначны",
  insufficient_basis: "Недостаточно оснований для рекомендации", no_window: "Нет окна без критических факторов" };
const KIND_TEXT = { observation: "НАБЛЮДЕНИЕ", message: "СООБЩЕНИЕ SWPC", forecast_external: "ВНЕШНИЙ ПРОГНОЗ",
  forecast_team: "РАСЧЁТ КОМАНДЫ", probability: "ВЕРОЯТНОСТЬ", forecast_baseline: "БАЗОВАЯ МОДЕЛЬ",
  geometry: "РАСЧЁТ КОМАНДЫ", catalog: "КАТАЛОГ", elements: "ОРБИТА", "observation+forecast": "НАБЛЮДЕНИЕ + ПРОГНОЗ", none: "—" };
const LAYER_TEXT = { measurement: "измерение", condition: "условие на траектории", forecast: "прогноз" };
const CONF_TEXT = { high: "высокая", medium: "средняя", low: "низкая", none: "нет" };
const MECH_TEXT = { radiation: "Радиация", mmod: "Сближения" };
const MODE_TEXT = { now: "Текущая обстановка", replay: "Прогноз из прошлого", review: "Разбор (весь архив)" };
const MODE_HINT = {
  now: "Живые данные. Период поиска — от ближайших 15 минут на сутки вперёд.",
  replay: "Считаем так, будто сейчас момент T: видны только данные, опубликованные до него.",
  review: "Разбор прошлого по всему архиву, включая поздние уточнения (реконструкция).",
};
const STAGES = { queued: "в очереди", start: "запуск", slice: "срез данных", orbit: "орбита МКС",
  radiation: "радиационная обстановка", mmod: "сближения с объектами", windows: "сравнение окон", done: "готово" };
const OVERRIDE_SOURCES = { goes_protons: "Протоны GOES", swpc_alerts: "Сообщения SWPC", kp_observed: "Kp (наблюдение)",
  swpc_3day: "3-дневный прогноз", swpc_rsga: "RSGA", catalog_spacetrack: "Каталог объектов", iss_spacetrack: "Орбита МКС" };
const PRESETS = {
  "0608": { mode: "replay", asOf: "2024-06-08T03:00", planned: "2024-06-08T06:00", dur: [6, 30] },
  "0510": { mode: "replay", asOf: "2024-05-10T21:00", planned: "", dur: [6, 30] },
  "0511": { mode: "review", asOf: "2024-05-11T00:00", planned: "", dur: [6, 30] },
  "0526": { mode: "replay", asOf: "2024-05-26T00:00", planned: "2024-05-26T06:00", dur: [6, 30] },
};

// ---------- утилиты ----------
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) e.append(k.nodeType ? k : String(k));
  return e;
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hm = (iso) => (iso ? iso.slice(11, 16) : "—");
const dm = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "—");
const toIso = (local) => (local ? `${local}:00Z` : null);
const toLocal = (iso) => (iso ? iso.slice(0, 16) : "");
const num = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const ageText = (s) => (s === null || s === undefined ? "—" : s < 120 ? `${s} с` : s < 7200 ? `${Math.round(s / 60)} мин` :
  s < 172800 ? `${(s / 3600).toFixed(1)} ч` : `${Math.round(s / 86400)} сут`);

async function api(path, opts = {}) {
  const r = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let data = null;
  try { data = await r.json(); } catch { data = null; }
  if (!r.ok) {
    let msg = data?.detail ?? `HTTP ${r.status}`;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("\n");
    throw new Error(msg);
  }
  return data;
}

function toast(text, severity = "info", ms = 7000) {
  const t = el("div", { class: `toast ${severity}` }, text);
  $("toasts").append(t);
  setTimeout(() => t.remove(), ms);
}

// ---------- форма ----------
function setMode(mode) {
  S.mode = mode;
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === mode));
  $("asOfWrap").classList.toggle("hidden", mode === "now");
  $("modeHint").textContent = MODE_HINT[mode];
}

function buildOverrides() {
  const box = $("overrides");
  box.innerHTML = "";
  for (const [key, title] of Object.entries(OVERRIDE_SOURCES)) {
    const sel = el("select", { "data-src": key },
      el("option", { value: "" }, "обычно"), el("option", { value: "disabled" }, "отключить"),
      el("option", { value: "frozen" }, "заморозить на…"));
    const at = el("input", { type: "datetime-local", class: "hidden", "data-at": key, step: "60" });
    sel.addEventListener("change", () => at.classList.toggle("hidden", sel.value !== "frozen"));
    box.append(el("div", { class: "ov-row" }, el("span", {}, title), sel, at));
  }
}

function buildRequest() {
  const dur = Number($("durH").value) * 60 + Number($("durM").value);
  const req = {
    mode: S.mode, duration_min: dur, radiation_model: $("model").value, step_min: Number($("stepMin").value),
    eva: { return_to_airlock_min: Number($("retMin").value), overrun_margin_min: Number($("marginMin").value) },
    source_overrides: {},
  };
  if (S.mode !== "now") {
    if (!$("asOf").value) throw new Error("Укажите момент T");
    req.as_of = toIso($("asOf").value);
  }
  for (const [id, key] of [["earliest", "earliest_start"], ["latest", "latest_start"], ["planned", "planned_start"]]) {
    if ($(id).value) req[key] = toIso($(id).value);
  }
  document.querySelectorAll("#overrides select").forEach((s) => {
    if (!s.value) return;
    const o = { state: s.value };
    if (s.value === "frozen") {
      const at = document.querySelector(`[data-at="${s.dataset.src}"]`).value;
      if (!at) throw new Error(`Укажите момент заморозки для «${OVERRIDE_SOURCES[s.dataset.src]}»`);
      o.at = toIso(at);
    }
    req.source_overrides[s.dataset.src] = o;
  });
  return req;
}

async function submitRun(req) {
  $("formErr").textContent = "";
  $("runBtn").disabled = true;
  showProgress("queued", 0);
  try {
    const { run_id } = await api("/api/a/runs", { method: "POST", body: JSON.stringify(req) });
    await pollRun(run_id);
  } catch (e) {
    $("formErr").textContent = e.message;
    $("progress").classList.add("hidden");
  } finally {
    $("runBtn").disabled = false;
  }
}

function showProgress(stage, pct) {
  $("progress").classList.remove("hidden");
  $("progress").querySelector("i").style.width = `${pct}%`;
  $("progress").querySelector("span").textContent = `${STAGES[stage] || stage} · ${pct}%`;
}

async function pollRun(id) {
  for (;;) {
    const r = await api(`/api/a/runs/${id}?include=summary`);
    const p = r.progress || {};
    showProgress(p.stage || r.status, p.pct || 0);
    if (r.status === "done") break;
    if (r.status === "failed") throw new Error(`Расчёт не выполнен: ${r.error}`);
    await new Promise((ok) => setTimeout(ok, 600));
  }
  await loadRun(id);
  $("progress").classList.add("hidden");
}

async function loadRun(id) {
  const run = await api(`/api/a/runs/${id}`);
  if (!run.result) throw new Error(run.error || "нет результата");
  if (S.res && run.request && S.res.search && run.request.duration_min !== S.res.search.duration_min &&
      S.pendingPlanChange) {
    S.prevDuration = S.res.search.duration_min;
  } else {
    S.prevDuration = null;
  }
  S.pendingPlanChange = false;
  S.run = run;
  S.res = run.result;
  history.replaceState(null, "", `?run=${id}`);
  render();
  refreshRuns();
}

// ---------- отрисовка результата ----------
function render() {
  const r = S.res;
  $("empty").classList.add("hidden");
  $("result").classList.remove("hidden");
  const badge = $("modeBadge");
  badge.classList.remove("hidden");
  badge.textContent = `${MODE_TEXT[r.mode]} · T = ${dm(r.as_of)} UTC`;
  const byId = Object.fromEntries(r.windows.map((w) => [w.id, w]));
  S.byId = byId;
  const def = byId[r.recommendation.window] || byId[r.planned] || r.windows[0];
  S.sel = def?.id;
  renderPlanBanner();
  renderRec();
  renderTimeline();
  renderWindows();
  selectWindow(S.sel, false);
  renderSources();
  renderVerifyPane();
  renderExport();
  showTab(S.tab);
}

function renderPlanBanner() {
  const b = $("planBanner");
  if (S.prevDuration) {
    b.classList.remove("hidden");
    b.textContent = `Изменение плана: длительность ${S.prevDuration} → ${S.res.search.duration_min} мин. ` +
      "Окна сравниваются только с окнами той же длительности; сокращение выхода — это новый план, а не улучшение условий.";
  } else b.classList.add("hidden");
}

function renderRec() {
  const r = S.res, rec = r.recommendation, best = S.byId[rec.window];
  const card = $("recCard");
  card.innerHTML = "";
  const head = el("div", { class: "head" },
    el("span", { class: `pill ${rec.status}` }, REC_TEXT[rec.status] || rec.status),
    best ? el("span", { class: "title" }, `${best.id}: ${dm(best.start)} – ${hm(best.end)} UTC`) : null,
    el("span", { class: `pill ${rec.confidence === "low" ? "low" : "worse"}` }, `уверенность: ${CONF_TEXT[rec.confidence] || rec.confidence}`),
    rec.tentative ? el("span", { class: "pill low" }, "предварительно") : null);
  const flags = el("div", { class: "flags" },
    el("span", { class: "flag" }, `поиск ${dm(r.search.earliest_start)} – ${dm(r.search.latest_start)}, длительность ${r.search.duration_min} мин`),
    r.reconstruction ? el("span", { class: "flag warn" }, "есть реконструированные данные") : null,
    ...r.sources.filter((s) => !["ok", "frozen"].includes(s.state) &&
        !(s.state === "no_data" && ["kp_forecast", "iss_celestrak"].includes(s.source)))  // только «живые» источники
      .map((s) => el("span", { class: "flag warn", title: s.last_error || "" }, `${s.source}: ${s.state}`)));
  const lines = el("ul", {}, ...r.explanation.text.map((t) => el("li", {}, t)));
  const rech = r.recheck_after?.length
    ? el("div", { class: "muted" }, "Пересчитать: " + r.recheck_after.slice(0, 3).map((x) => `${dm(x.time)} — ${x.reason}`).join("; "))
    : null;
  const watchBtn = el("button", { type: "button", class: "watch", onclick: createWatch },
    r.mode === "now" ? "Отслеживать выбранное окно" : "Проиграть оповещения для выбранного окна");
  const tg = el("label", { class: "inline muted" }, el("input", { type: "checkbox", id: "tgChk" }), "дублировать в Telegram");
  card.append(head, flags, lines, rech,
    el("div", { class: "actions" }, watchBtn, tg,
      el("a", { href: `/runs/${S.run.run_id}/brief`, target: "_blank" }, "Краткая сводка"),
      el("a", { href: `/runs/${S.run.run_id}/report`, target: "_blank" }, "Полный отчёт"),
      el("a", { href: `/runs/${S.run.run_id}/export.zip` }, "Скачать ZIP")));
}

function seriesArr(key) {
  return (S.res.series[key] || []).map((v) => (v === null ? null : v));
}

function renderTimeline() {
  const r = S.res, s = r.series, t = s.times;
  const bandRows = Object.keys(r.timeline);
  const bandAxes = { [bandRows[0]]: "y", [bandRows[1]]: "y2" };
  const shapes = [], traces = [];
  bandRows.forEach((m) => {
    const yref = bandAxes[m];
    const xs = [], ys = [], texts = [], cd = [];
    r.timeline[m].forEach((iv, i) => {
      shapes.push({ type: "rect", xref: "x", yref, x0: iv.from, x1: iv.to, y0: 0, y1: 1, line: { width: 0 },
        fillcolor: CLASS_COLOR[iv.class], opacity: iv.kind === "observation" || iv.kind === "none" ? 0.95 : 0.7 });
      const mid = new Date((Date.parse(iv.from) + Date.parse(iv.to)) / 2).toISOString();
      xs.push(mid); ys.push(0.5); cd.push([m, i]);
      texts.push(`<b>${MECH_TEXT[m]}: ${CLASS_TEXT[iv.class]}</b><br>${esc(iv.reason_text)}<br>` +
        `${KIND_TEXT[iv.kind] || iv.kind} · уверенность ${CONF_TEXT[iv.confidence] || iv.confidence}<br>${dm(iv.from)}–${hm(iv.to)}`);
    });
    traces.push({ x: xs, y: ys, yaxis: yref, mode: "markers", marker: { size: 14, opacity: 0 }, hoverinfo: "text",
      text: texts, customdata: cd, showlegend: false, name: m });
  });
  const line = (key, name, color, dash, yaxis = "y3", shape = "linear") => ({
    x: t, y: seriesArr(key), name, yaxis, mode: "lines", connectgaps: false,
    line: { color, width: 1.5, dash, shape }, hovertemplate: `${name}: %{y:.3g}<extra></extra>` });
  if (s["radiation.p_ge10"]) {
    traces.push(line("radiation.p_ge10", "≥10 МэВ, геостационар", "#4a9eff"));
    traces.push(line("radiation.p_ge100", "≥100 МэВ, геостационар", "#a67cf0"));
    traces.push(line("radiation.j_iss", "J_iss: выше порога обрезания в точке МКС", "#f07c4a", "dot"));
    traces.push(line("radiation.kp", "Kp", "#cfd5dc", "solid", "y4", "hv"));
    traces.push({ x: t, y: seriesArr("radiation.saa").map((v) => (v ? 8.5 : null)), yaxis: "y4", mode: "lines",
      name: "ЮАА", line: { color: "#e0a030", width: 6 }, hoverinfo: "skip" });
  }
  // T, выбранное, рекомендованное и плановое окна
  shapes.push({ type: "line", xref: "x", yref: "paper", x0: r.as_of, x1: r.as_of, y0: 0, y1: 1,
    line: { color: "#fff", width: 1, dash: "dash" } });
  const rec = S.byId[r.recommendation.window], plan = S.byId[r.planned];
  if (rec) shapes.push({ type: "rect", xref: "x", yref: "paper", x0: rec.start, x1: rec.end, y0: 0.77, y1: 1,
    line: { color: "#3a9d5d", width: 2 }, fillcolor: "rgba(0,0,0,0)" });
  if (plan && plan !== rec) shapes.push({ type: "rect", xref: "x", yref: "paper", x0: plan.start, x1: plan.end,
    y0: 0.77, y1: 1, line: { color: "#cfd5dc", width: 1, dash: "dot" }, fillcolor: "rgba(0,0,0,0)" });
  S.baseShapes = shapes;
  const layout = {
    paper_bgcolor: "#161d25", plot_bgcolor: "#161d25", font: { color: "#dfe6ee", size: 11 },
    margin: { l: 80, r: 10, t: 10, b: 30 }, hovermode: "closest", showlegend: true,
    legend: { orientation: "h", y: -0.08, font: { size: 11 } },
    xaxis: { type: "date", gridcolor: "#2a3542", anchor: "y4" },
    yaxis: { domain: [0.9, 0.98], range: [0, 1], visible: false, fixedrange: true },
    yaxis2: { domain: [0.8, 0.88], range: [0, 1], visible: false, fixedrange: true },
    yaxis3: { domain: [0.34, 0.74], type: "log", title: { text: "pfu" }, gridcolor: "#2a3542", exponentformat: "power" },
    yaxis4: { domain: [0, 0.26], range: [0, 9.5], title: { text: "Kp" }, gridcolor: "#2a3542" },
    shapes: shapes.concat(selShape()),
    annotations: [
      ...bandRows.map((m, i) => ({ xref: "paper", yref: "paper", x: 0, y: i === 0 ? 0.94 : 0.84, xanchor: "right",
        text: MECH_TEXT[m], showarrow: false, xshift: -6 })),
      { xref: "x", yref: "paper", x: r.as_of, y: 0.77, text: "T", showarrow: false, xanchor: "left", xshift: 3,
        font: { color: "#fff" } },
    ],
  };
  Plotly.react("timeline", traces, layout, { displaylogo: false, responsive: true,
    modeBarButtonsToRemove: ["select2d", "lasso2d"] });
  const tl = $("timeline");
  tl.removeAllListeners?.("plotly_click");
  tl.on("plotly_click", (ev) => {
    const p = ev.points?.[0];
    if (p?.customdata && Array.isArray(p.customdata)) {
      const [m, i] = p.customdata;
      const iv = S.res.timeline[m][i];
      openEvidence(iv.evidence, `${MECH_TEXT[m]} · ${dm(iv.from)}–${hm(iv.to)} · ${CLASS_TEXT[iv.class]}`, iv);
    }
  });
}

function selShape() {
  const w = S.byId?.[S.sel];
  if (!w) return [];
  return [{ type: "rect", xref: "x", yref: "paper", x0: w.start, x1: w.end, y0: 0, y1: 1, line: { width: 0 },
    fillcolor: "rgba(74,158,255,0.10)" }];
}

function renderWindows() {
  const r = S.res, mechs = Object.keys(r.timeline);
  const tbl = $("winTable");
  const hide = $("hideBad").checked;
  const head = el("tr", {}, el("th", {}, "Окно"), el("th", {}, "Начало – конец, UTC"), el("th", {}, "Статус"),
    ...mechs.map((m) => el("th", { class: "num" }, `${MECH_TEXT[m]}: крит / нежел / нет данных, мин`)),
    mechs.includes("radiation") ? el("th", { class: "num", title: "Сумма J_iss за окно — оценка потока выше порога обрезания" }, "Поток, pfu·мин") : null,
    el("th", { class: "num" }, "Запас на задержку"), el("th", {}, "Уверенность"));
  const rows = r.windows.filter((w) => !hide || !["not_recommended"].includes(w.status)).map((w) => {
    const cells = mechs.map((m) => {
      const mn = w.mechanisms[m].minutes;
      const c = (v, k) => el("span", { class: `cl ${v > 0 ? k : "z"}` }, num(v));
      return el("td", { class: "num" }, c(mn.critical, "critical"), " / ", c(mn.undesirable, "undesirable"), " / ", c(mn.no_data, "no_data"));
    });
    const ov = w.overrun;
    const tr = el("tr", { "data-id": w.id, class: [w.id === r.recommendation.window ? "rec" : "", w.id === S.sel ? "sel" : ""].join(" "),
      onclick: () => selectWindow(w.id, true) },
      el("td", {}, `${w.id}${w.id === r.recommendation.window ? " ★" : ""}${w.is_planned ? " (план)" : ""}`),
      el("td", {}, `${dm(w.start)} – ${hm(w.end)}`),
      el("td", {}, el("span", { class: `pill ${w.status}` }, STATUS_TEXT[w.status] || w.status)),
      ...cells,
      mechs.includes("radiation") ? el("td", { class: "num" }, num(w.mechanisms.radiation.exposure_pfu_min, 1)) : null,
      el("td", { class: "num" }, ov.robust ? `≥ ${ov.margin_min} мин` : `${num(ov.first_critical_after_end_min)} мин`),
      el("td", {}, CONF_TEXT[w.confidence] || w.confidence));
    return tr;
  });
  tbl.innerHTML = "";
  tbl.append(el("thead", {}, head), el("tbody", {}, ...rows));
  const sh = $("shift");
  sh.max = String(r.windows.length - 1);
  $("durEdit").value = r.search.duration_min;
}

function selectWindow(id, scroll) {
  const w = S.byId?.[id];
  if (!w) return;
  S.sel = id;
  const idx = S.res.windows.findIndex((x) => x.id === id);
  $("shift").value = String(idx);
  $("shiftLabel").textContent = `${w.id} ${dm(w.start)}`;
  document.querySelectorAll("#winTable tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
  if (scroll) document.querySelector(`#winTable tr[data-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
  // подробности окна
  const box = $("winDetail");
  box.innerHTML = "";
  box.append(el("div", {}, S.res.explanation.windows[id] || ""));
  const ivs = el("div", { class: "ivs" });
  for (const [m, list] of Object.entries(S.res.timeline)) {
    list.forEach((iv) => {
      if (iv.class === "acceptable" || iv.to <= w.start || iv.from >= w.end) return;
      ivs.append(el("span", { class: `iv ${iv.class}`, title: "Открыть доказательства",
        onclick: () => openEvidence(iv.evidence, `${MECH_TEXT[m]} · ${dm(iv.from)}–${hm(iv.to)} · ${CLASS_TEXT[iv.class]}`, iv) },
        `${MECH_TEXT[m]} ${hm(iv.from)}–${hm(iv.to)}: ${iv.reason_text}`));
    });
  }
  const conj = (S.res.events.mmod || []).filter((e) => e.tca >= w.start && e.tca <= w.end && isNotableConj(e));
  conj.forEach((e) => ivs.append(el("span", { class: `iv ${e.in_control_box ? "critical" : ""}`,
    onclick: () => openEvidence([`conj:${e.norad_id}:${e.tca}`, "catalog"], `Сближение ${e.object_name}`) },
    `Сближение ${hm(e.tca)}: ${e.object_name}, ${num(e.min_range_km, 1)} км${e.in_control_box ? " — в зоне контроля" : ""}`)));
  if (ivs.childNodes.length) box.append(ivs);
  else box.append(el("div", { class: "muted" }, "В окне нет интервалов с неблагоприятными условиями или пропусками данных."));
  // подсветка на шкале и карте
  if (document.getElementById("timeline").layout) {
    Plotly.relayout("timeline", { shapes: S.baseShapes.concat(selShape()) });
  }
  if (S.tab === "map") renderMap();
}

// сближения, которые стоит показывать в деталях окна и на карте (остальные — в полном отчёте)
const NOTABLE_CONJ_KM = 10;
function isNotableConj(e) { return e.in_control_box || e.min_range_km <= NOTABLE_CONJ_KM; }

// ---------- карта ----------
function renderMap() {
  const r = S.res, s = r.series, w = S.byId[S.sel];
  if (!w) return;
  const t = s.times;
  const inWin = t.map((x) => x >= w.start && x < w.end);
  const cls = s["radiation.class"] || [];
  const clsName = { "-1": "no_data", 0: "acceptable", 1: "undesirable", 2: "critical" };
  const lat = [], lon = [], col = [], txt = [];
  t.forEach((x, i) => {
    if (!inWin[i]) return;
    lat.push(s.lat[i]); lon.push(s.lon[i]);
    const c = clsName[String(cls[i])] || "acceptable";
    col.push(CLASS_COLOR[c]);
    txt.push(`${dm(x)} · ${CLASS_TEXT[c]}<br>ш ${num(s.lat[i], 1)}°, д ${num(s.lon[i], 1)}°, ${num(s.alt_km[i])} км<br>` +
      `геомагн. широта ${num(s.mlat[i], 1)}°, |B| ${num(s.b_nt[i])} нТл` +
      (s["radiation.e_cut_mev"] ? `<br>энергия обрезания ${num(s["radiation.e_cut_mev"][i])} МэВ` : ""));
  });
  const traces = [
    { type: "scattergeo", lat: s.lat, lon: s.lon, mode: "lines", line: { color: "#3b4654", width: 1 },
      hoverinfo: "skip", name: "трасса за период" },
    { type: "scattergeo", lat, lon, mode: "markers", marker: { size: 5, color: col }, text: txt, hoverinfo: "text",
      name: `окно ${w.id}` },
  ];
  const conj = (r.events.mmod || []).filter((e) => e.tca >= w.start && e.tca <= w.end && isNotableConj(e));
  if (conj.length) {
    const idx = conj.map((e) => t.findIndex((x) => x >= e.tca));
    traces.push({ type: "scattergeo", lat: idx.map((i) => s.lat[i]), lon: idx.map((i) => s.lon[i]), mode: "markers",
      marker: { size: 12, symbol: "x", color: conj.map((e) => (e.in_control_box ? "#ff5a5a" : "#cfd5dc")) },
      text: conj.map((e) => `${e.object_name} · ${hm(e.tca)} · ${num(e.min_range_km, 1)} км`), hoverinfo: "text", name: "сближения" });
  }
  Plotly.react("map", traces, {
    paper_bgcolor: "#161d25", font: { color: "#dfe6ee", size: 11 }, margin: { l: 0, r: 0, t: 0, b: 0 },
    showlegend: true, legend: { orientation: "h", y: 0 },
    geo: { projection: { type: "natural earth" }, showland: true, landcolor: "#223041", showocean: true,
      oceancolor: "#121a23", coastlinecolor: "#51606f", bgcolor: "#161d25", showcountries: false,
      lataxis: { range: [-65, 65] } },
  }, { displaylogo: false, responsive: true, topojsonURL: "/static/vendor/" });
  $("mapHint").textContent = `Трасса МКС в окне ${w.id}. Цвет точки — класс радиационной обстановки в этот момент. ` +
    "Открытые для частиц участки — высокие геомагнитные широты; нежелательные участки над Южной Атлантикой — ЮАА.";
}

// ---------- доказательства ----------
function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return esc(JSON.stringify(v));
  return esc(v);
}

function openEvidence(keys, title, iv) {
  const body = $("drawerBody");
  body.innerHTML = "";
  $("drawerTitle").textContent = title || "Доказательства";
  if (iv) {
    body.append(el("div", { class: "ev" },
      el("div", { class: "h" }, el("span", { class: `kind ${iv.kind}` }, KIND_TEXT[iv.kind] || iv.kind),
        el("span", { class: "layer" }, "последствие для ВКД")),
      el("div", { class: "kv" }, el("b", {}, "Класс"), CLASS_TEXT[iv.class], el("b", {}, "Почему"), iv.reason_text,
        el("b", {}, "Интервал"), `${dm(iv.from)} – ${dm(iv.to)} UTC`, el("b", {}, "Уверенность"), CONF_TEXT[iv.confidence] || iv.confidence)));
  }
  const ev = S.res.evidence;
  for (const k of keys || []) {
    const e = ev[k];
    if (!e) continue;
    const kv = el("div", { class: "kv" });
    for (const [f, v] of Object.entries(e)) {
      if (["layer", "kind", "raw_ref"].includes(f) || v === null) continue;
      kv.append(el("b", {}, f), el("span", { html: fmtVal(v) }));
    }
    const links = el("div", { class: "flags" });
    const rr = e.raw_ref || {};
    const ids = rr.raw_ids || (rr.raw_id ? [rr.raw_id] : []);
    ids.slice(0, 8).forEach((id) => links.append(el("a", { class: "btn", href: `/api/i/raw/${id}`, target: "_blank" },
      `исходный файл #${id}`), el("a", { class: "btn", href: `/api/i/raw/${id}/meta`, target: "_blank" }, "откуда и когда")));
    if (rr.locator) links.append(el("span", { class: "flag" }, `запись: ${rr.locator}`));
    body.append(el("div", { class: "ev" },
      el("div", { class: "h" }, el("span", { class: `kind ${e.kind}` }, KIND_TEXT[e.kind] || e.kind),
        el("span", { class: "layer" }, LAYER_TEXT[e.layer] || e.layer || ""), el("code", {}, k)),
      kv, ids.length || rr.locator ? links : null));
  }
  openDrawer();
}

function openDrawer() { $("drawer").classList.remove("hidden"); $("drawer").setAttribute("aria-hidden", "false"); }
function closeDrawer() { $("drawer").classList.add("hidden"); $("drawer").setAttribute("aria-hidden", "true"); }

// ---------- вкладки ----------
function showTab(tab) {
  S.tab = tab;
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
  for (const t of ["map", "sources", "verify", "export"]) $(`tab-${t}`).classList.toggle("hidden", t !== tab);
  if (tab === "map" && S.res) renderMap();
  if (tab === "sources") loadLiveSources();
}

function renderSources() {
  const r = S.res, box = $("tab-sources");
  box.innerHTML = "";
  const rows = r.sources.map((s) => {
    const m = r.manifest[s.source] || {};
    return el("tr", {}, el("td", {}, s.source), el("td", {}, el("span", { class: `chip ${s.state}` }, s.state)),
      el("td", {}, dm(s.latest_data)), el("td", { class: "num" }, ageText(s.age_s)),
      el("td", { class: "num" }, m.items ?? "—"), el("td", { class: "num" }, m.excluded_after_cutoff ?? "—"),
      el("td", {}, m.reconstructed ? "да" : "—"), el("td", {}, m.override || "—"));
  });
  box.append(el("h3", {}, "Данные, использованные в расчёте"),
    el("p", { class: "hint" }, r.mode === "replay"
      ? `Отсечка ${dm(r.as_of)} UTC: учтены только записи, опубликованные до неё; «отброшено» — опубликованные позже.`
      : "Давность — относительно момента расчёта."),
    el("div", { class: "tablewrap" }, el("table", { class: "tbl" },
      el("thead", {}, el("tr", {}, ...["Источник", "Состояние", "Последние данные", "Давность", "Использовано записей",
        "Отброшено после отсечки", "Реконструкция", "Переопределение"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows))),
    el("h3", {}, "Орбита МКС"),
    el("div", { class: "kv" }, el("b", {}, "Источник"), r.orbit.source, el("b", {}, "Эпоха элементов"), dm(r.orbit.epoch),
      el("b", {}, "Опубликованы"), dm(r.orbit.created), el("b", {}, "Давность на момент T"), `${num(r.orbit.age_at_cutoff_h, 1)} ч`,
      el("b", {}, "TLE"), el("code", {}, `${r.orbit.line1}\n${r.orbit.line2}`)),
    el("h3", {}, "Сбор данных сейчас"), el("div", { id: "liveSources", class: "tablewrap" }, "загрузка…"));
}

async function loadLiveSources() {
  const box = document.getElementById("liveSources");
  let list;
  try { list = await api("/api/i/sources"); } catch (e) { if (box) box.textContent = `ingest недоступен: ${e.message}`; return; }
  renderChips(list);
  if (!box) return;
  const act = async (name, what) => {
    try { await api(`/api/i/sources/${name}/${what}`, { method: "POST" }); toast(`${name}: ${what === "refresh" ? "обновление запущено" : what}`); }
    catch (e) { toast(e.message, "warning"); }
    setTimeout(loadLiveSources, 1500);
  };
  box.innerHTML = "";
  box.append(el("table", { class: "tbl" },
    el("thead", {}, el("tr", {}, ...["Источник", "Состояние", "Последние данные", "Давность", "Опрос", "Последняя ошибка", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...list.map((s) => el("tr", {},
      el("td", { title: s.title || "" }, s.source), el("td", {}, el("span", { class: `chip ${s.status}` }, s.status)),
      el("td", {}, dm(s.latest_data)), el("td", { class: "num" }, ageText(s.age_s)),
      el("td", { class: "num" }, ageText(s.interval_s)), el("td", { title: s.last_error || "" }, (s.last_error || "—").slice(0, 40)),
      el("td", {}, el("button", { class: "btn", type: "button", onclick: () => act(s.source, "refresh") }, "обновить"), " ",
        s.paused ? el("button", { class: "btn", type: "button", onclick: () => act(s.source, "resume") }, "возобновить")
          : el("button", { class: "btn", type: "button", onclick: () => act(s.source, "pause") }, "заморозить")))))));
}

function renderChips(list) {
  const cnt = {};
  list.forEach((s) => { cnt[s.status] = (cnt[s.status] || 0) + 1; });
  const box = $("srcChips");
  box.innerHTML = "";
  for (const [st, n] of Object.entries(cnt)) box.append(el("span", { class: `chip ${st}` }, `${st}: ${n}`));
}

// ---------- сверка ----------
function renderVerifyPane() {
  const box = $("tab-verify");
  box.innerHTML = "";
  if (S.res.mode !== "replay") {
    box.append(el("p", { class: "hint" }, "Сверка доступна для режима «Прогноз из прошлого»."));
    return;
  }
  box.append(el("p", {}, "Тот же запрос пересчитывается по всему архиву (факт) и сравнивается с прогнозом, сделанным на момент T. ЮАА исключена — это геометрия."),
    el("button", { class: "btn primary", type: "button", onclick: runVerify }, "Что было на самом деле"),
    el("div", { id: "verifyOut" }));
}

async function runVerify() {
  const out = $("verifyOut");
  out.textContent = "сверка…";
  try {
    const { run_id } = await api(`/api/a/runs/${S.run.run_id}/verify`, { method: "POST" });
    let v;
    for (;;) {
      v = await api(`/api/a/runs/${run_id}`);
      if (v.status === "done" || v.status === "failed") break;
      await new Promise((ok) => setTimeout(ok, 700));
    }
    if (v.status === "failed") throw new Error(v.error);
    const r = v.result;
    out.innerHTML = "";
    const tbl = el("table", { class: "tbl" }, el("thead", {}, el("tr", {}, ...["Механизм", "Уровень", "Попадания, мин", "Пропуски, мин",
      "Ложные тревоги, мин", "Верные «нет», мин", "Доля найденных (POD)", "Доля ложных (FAR)"].map((h) => el("th", {}, h)))));
    const tb = el("tbody");
    for (const [m, x] of Object.entries(r.mechanisms)) {
      for (const lvl of ["adverse", "critical"]) {
        const sc = x[lvl];
        tb.append(el("tr", {}, el("td", {}, MECH_TEXT[m]), el("td", {}, lvl === "adverse" ? "неблагоприятно" : "критично"),
          el("td", { class: "num" }, sc.hits_min), el("td", { class: "num" }, sc.misses_min), el("td", { class: "num" }, sc.false_alarm_min),
          el("td", { class: "num" }, sc.correct_negative_min), el("td", { class: "num" }, sc.pod ?? "—"), el("td", { class: "num" }, sc.far ?? "—")));
      }
    }
    tbl.append(tb);
    const rc = r.recommended_window_check;
    out.append(el("h3", {}, "Прогноз против факта (минуты после T)"), el("div", { class: "tablewrap" }, tbl),
      el("h3", {}, "Рекомендованное окно"),
      rc ? el("p", {}, `${rc.window}: на деле — «${STATUS_TEXT[rc.actual_status] || rc.actual_status}», критических минут ${rc.actual_critical_min}. ` +
        `Лучшее окно по факту: ${rc.actual_best_window || "нет"}.`) : el("p", { class: "muted" }, "Рекомендации не было."),
      el("h3", {}, "Что показал разбор по всему архиву"), el("ul", {}, ...(r.actual_explanation || []).map((t) => el("li", {}, t))),
      el("p", { class: "hint" }, r.note));
  } catch (e) {
    out.textContent = `Ошибка сверки: ${e.message}`;
  }
}

// ---------- сохранение ----------
async function renderExport() {
  const id = S.run.run_id, box = $("tab-export");
  box.innerHTML = "";
  box.append(el("p", {}, "Сохранённый расчёт содержит запрос, момент отсечки, оценки по механизмам, рекомендацию, " +
    "ссылки на исходные файлы и версию алгоритма."),
  el("div", { class: "flags" },
    el("a", { class: "btn", href: `/runs/${id}/report`, target: "_blank" }, "Полный отчёт (HTML)"),
    el("a", { class: "btn", href: `/runs/${id}/brief`, target: "_blank" }, "Краткая сводка для руководителя"),
    el("a", { class: "btn primary", href: `/runs/${id}/export.zip` }, "ZIP: отчёт + JSON + CSV + манифест"),
    el("a", { class: "btn", href: `/api/a/runs/${id}`, target: "_blank" }, "JSON")),
  el("h3", {}, "Строка статуса (для экипажа / внешнего табло)"), el("pre", { class: "mono", id: "statusLine" }, "…"),
  el("div", { class: "kv" }, el("b", {}, "Расчёт"), el("code", {}, id), el("b", {}, "Версия алгоритма"), S.run.algorithm_version || "—",
    el("b", {}, "Создан"), dm(S.run.created_at)));
  try {
    const t = await fetch(`/runs/${id}/status-line`).then((r) => r.text());
    $("statusLine").textContent = t;
  } catch { /* необязательно */ }
}

// ---------- оповещения ----------
async function createWatch() {
  const r = S.res, w = S.byId[S.sel] || S.byId[r.recommendation.window];
  if (!w) { toast("Выберите окно", "warning"); return; }
  const body = { mode: r.mode === "now" ? "now" : "replay", window_start: w.start, duration_min: r.search.duration_min,
    eva: { return_to_airlock_min: r.search.return_to_airlock_min, overrun_margin_min: r.search.overrun_margin_min },
    label: `${w.id} ${dm(w.start)}`, channels: document.getElementById("tgChk")?.checked ? ["web", "telegram"] : ["web"] };
  if (body.mode === "replay") {
    const start = Date.parse(w.start) - 6 * 3600e3, tAs = Date.parse(r.as_of);
    body.as_of = new Date(Math.min(start, tAs)).toISOString().slice(0, 19) + "Z";
    body.sim_step_min = 30;
  }
  try {
    const { watch_id } = await api("/api/a/watches", { method: "POST", body: JSON.stringify(body) });
    toast(body.mode === "now" ? `Окно ${w.id} поставлено на отслеживание (${watch_id})`
      : `Проигрывание запущено: «часы» с ${dm(body.as_of)}, шаг 30 мин каждые несколько секунд`);
    ensureNotifyPermission();
    openAlerts();
  } catch (e) { toast(e.message, "warning"); }
}

function ensureNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

function onAlert(a) {
  S.alerts.push(a);
  if (a.severity !== "info") { S.unread += 1; }
  updateBell();
  toast(`${a.severity === "critical" ? "КРИТИЧНО · " : ""}${a.message}`, a.severity, a.severity === "critical" ? 15000 : 8000);
  if ("Notification" in window && Notification.permission === "granted" && a.severity !== "info") {
    try { new Notification("ВКД · оповещение", { body: a.message.slice(0, 200) }); } catch { /* нет поддержки */ }
  }
  if (!$("drawer").classList.contains("hidden") && S.drawerMode === "alerts") renderAlerts();
}

function updateBell() {
  $("bellCount").textContent = String(S.unread);
  $("bell").classList.toggle("has", S.unread > 0);
}

async function openAlerts() {
  S.drawerMode = "alerts";
  S.unread = 0;
  updateBell();
  $("drawerTitle").textContent = "Оповещения";
  openDrawer();
  await renderAlerts();
}

async function renderAlerts() {
  const body = $("drawerBody");
  let watches = [];
  try { watches = await api("/api/a/watches?limit=10"); } catch { /* ниже покажем пусто */ }
  const labels = Object.fromEntries(watches.map((w) => [w.watch_id, w.label || w.watch_id]));
  body.innerHTML = "";
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  body.append(el("p", { class: "hint" },
    "Оповещения приходят, когда состояние отслеживаемого окна меняется: ухудшение, критический интервал, пропажа данных. ",
    "Доставка: эта консоль в реальном времени, системное уведомление браузера",
    perm === "granted" ? " (включено)" : "", ", Telegram — если задан бот (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)."));
  if (perm === "default") body.append(el("button", { class: "btn", type: "button",
    onclick: () => Notification.requestPermission().then(renderAlerts) }, "Включить уведомления браузера"));
  body.append(el("h3", {}, "Отслеживание"));
  if (!watches.length) body.append(el("p", { class: "muted" }, "Нет отслеживаемых окон. Выберите окно и нажмите «Отслеживать»."));
  watches.forEach((w) => body.append(el("div", { class: "ev" },
    el("div", { class: "h" }, el("b", {}, w.label || w.watch_id), el("span", { class: "flag" }, w.mode === "replay" ? "проигрывание" : "реальное время"),
      el("span", { class: `flag ${w.status === "active" ? "" : "warn"}` }, w.status),
      w.status === "active" ? el("button", { class: "btn", type: "button", onclick: async () => { await api(`/api/a/watches/${w.watch_id}/stop`, { method: "POST" }); renderAlerts(); } }, "остановить") : null),
    el("div", { class: "muted" }, `Окно ${dm(w.window_start)}–${hm(w.window_end)} UTC` +
      (w.mode === "replay" ? ` · «часы»: ${dm(w.sim_time)}` : ` · проверено ${dm(w.last_check_at)}`) + ` · оповещений ${w.n_alerts || 0}`),
    w.error ? el("div", { class: "err" }, w.error) : null)));
  body.append(el("h3", {}, "Лента"));
  const list = [...S.alerts].reverse().slice(0, 60);
  if (!list.length) body.append(el("p", { class: "muted" }, "Оповещений пока нет."));
  list.forEach((a) => body.append(el("div", { class: `alert ${a.severity} ${a.ack_at ? "acked" : ""}` },
    el("div", { class: "meta" }, `${labels[a.watch_id] || a.watch_id} · момент оценки ${dm(a.as_of)} UTC · ` +
      `данные: ${a.data_lag_s !== null ? ageText(a.data_lag_s) : "—"} · доставка: ${Object.entries(a.delivered || {}).map(([k, v]) => `${k} ${v}`).join(", ")}`),
    el("div", {}, a.message),
    !a.ack_at && a.severity !== "info" ? el("button", { class: "btn", type: "button", onclick: async () => {
      await api(`/api/a/alerts/${a.id}/ack`, { method: "POST" }); a.ack_at = new Date().toISOString(); renderAlerts(); } }, "принято") : null)));
}

function connectAlerts() {
  const es = new EventSource("/api/alerts/stream");
  es.addEventListener("alert", (e) => onAlert(JSON.parse(e.data)));
  es.onerror = () => { /* браузер переподключится сам */ };
}

async function loadRecentAlerts() {
  try {
    const list = await api("/api/a/alerts?limit=200");
    S.alerts = list.slice(-100);
  } catch { /* assessment может ещё стартовать */ }
}

// ---------- история расчётов ----------
async function refreshRuns() {
  let list = [];
  try { list = await api("/api/a/runs?limit=12"); } catch { return; }
  const ul = $("runsList");
  ul.innerHTML = "";
  list.filter((r) => r.kind === "assessment").forEach((r) => {
    const q = r.request || {};
    ul.append(el("li", { onclick: () => loadRun(r.run_id).catch((e) => toast(e.message, "warning")) },
      `${MODE_TEXT[q.mode] || q.mode}${q.as_of ? " · T " + dm(q.as_of) : ""} · ${q.duration_min} мин`,
      el("span", { class: "s" }, r.status === "done" ? dm(r.created_at) : r.status)));
  });
}

// ---------- запуск ----------
function init() {
  setMode("replay");
  buildOverrides();
  $("asOf").value = "2024-06-08T03:00";
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll("#presets button").forEach((b) => b.addEventListener("click", () => {
    const p = PRESETS[b.dataset.preset];
    setMode(p.mode);
    $("asOf").value = p.asOf; $("planned").value = p.planned; $("earliest").value = ""; $("latest").value = "";
    $("durH").value = p.dur[0]; $("durM").value = p.dur[1];
    submitRun(buildRequest());
  }));
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    try { submitRun(buildRequest()); } catch (err) { $("formErr").textContent = err.message; }
  });
  $("shift").addEventListener("input", (e) => selectWindow(S.res.windows[Number(e.target.value)].id, true));
  $("hideBad").addEventListener("change", () => { renderWindows(); selectWindow(S.sel, false); });
  $("durBtn").addEventListener("click", () => {
    const d = Number($("durEdit").value);
    if (!d || !S.run) return;
    const req = { ...S.run.request, duration_min: d };
    S.pendingPlanChange = true;
    $("durH").value = Math.floor(d / 60); $("durM").value = d % 60;
    submitRun(req);
  });
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("drawerClose").addEventListener("click", closeDrawer);
  $("bell").addEventListener("click", openAlerts);
  $("simpleToggle").addEventListener("change", (e) => {
    document.body.classList.toggle("simple", e.target.checked);
    if (!e.target.checked && S.res) { renderTimeline(); showTab(S.tab); }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  setInterval(() => { $("clock").textContent = new Date().toISOString().slice(0, 19).replace("T", " ") + " UTC"; }, 1000);
  api("/api/i/sources").then(renderChips).catch(() => {});
  setInterval(() => api("/api/i/sources").then(renderChips).catch(() => {}), 60000);
  refreshRuns();
  loadRecentAlerts().then(connectAlerts);
  const run = new URLSearchParams(location.search).get("run");
  if (run) loadRun(run).catch((e) => toast(e.message, "warning"));
}

document.addEventListener("DOMContentLoaded", init);
