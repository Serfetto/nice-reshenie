/* Консоль выбора времени ВКД. Обычный JS без сборки. API: /api/a/* (assessment), /api/i/* (ingest). */
"use strict";

const $ = (id) => document.getElementById(id);
const S = { mode: "history", run: null, res: null, sel: null, alerts: [], unread: 0, prevDuration: null,
  tab: "map", detailsOpen: false, drawerMode: null };

// ---------- словари: простые слова ----------
const CLASS_COLOR = { critical: "#d64545", undesirable: "#e0a030", acceptable: "#3a9d5d", no_data: "#8a929c" };
const CLASS_TEXT = { critical: "стоп-фактор", undesirable: "нежелательно", acceptable: "без замечаний", no_data: "нет данных" };
const MECH_TEXT = { radiation: "Частицы от Солнца", mmod: "Мусор и метеороиды" };
const REASON_PLAIN = {
  nominal: "без замечаний",
  saa: "пролёт над Южной Атлантикой (ЮАА) — зона повышенной радиации, обычный фон",
  sep_s3: "сильная солнечная радиационная буря (S3 и выше) — при ней ВКД не проводят",
  sep_open_zone: "частицы от Солнца долетают до станции",
  sep_partial_access: "часть частиц от Солнца долетает до станции",
  sep_shielded: "идёт выброс частиц от Солнца, но станцию прикрывает магнитное поле Земли",
  sep_probable: "по суточному прогнозу выброс частиц вероятен",
  sep_unlikely: "по суточному прогнозу выброс частиц маловероятен",
  sep_ongoing_beyond_horizon: "выброс частиц продолжается, а дальше 6 часов прогноз не строим",
  no_observation: "нет измерений", obs_stale: "измерения устарели", source_disabled: "источник отключён",
  no_forecast: "нет прогноза на это время",
  conjunction: "опасное сближение с отслеживаемым объектом (проход через зону контроля станции)",
  meteor_shower: "метеорный поток: мелких частиц заметно больше обычного, и направление, откуда они летят, не закрыто Землёй",
  no_catalog: "нет данных о мусоре", catalog_stale: "данные о мусоре устарели",
};
const STATUS_TEXT = { preferred: "лучший вариант", equivalent: "почти как лучший", worse: "хуже лучшего",
  not_recommended: "не рекомендуется", requires_review: "нет данных — нужно проверить" };
const STATUS_STRIP = { preferred: "s-best", equivalent: "s-good", worse: "s-worse", not_recommended: "s-bad", requires_review: "s-nodata" };
const KIND_TEXT = { observation: "ИЗМЕРЕНИЕ", message: "СООБЩЕНИЕ SWPC", forecast_external: "ВНЕШНИЙ ПРОГНОЗ",
  forecast_team: "НАШ РАСЧЁТ", probability: "ВЕРОЯТНОСТЬ", forecast_baseline: "ПРОСТАЯ МОДЕЛЬ",
  geometry: "НАШ РАСЧЁТ", catalog: "КАТАЛОГ", elements: "ОРБИТА", "observation+forecast": "ИЗМЕРЕНИЕ + ПРОГНОЗ", none: "—" };
const LAYER_TEXT = { measurement: "измерение", condition: "условие на орбите", forecast: "прогноз" };
const CONF_TEXT = { high: "высокая", medium: "средняя", low: "низкая", none: "нет" };
const MODE_TEXT = { now: "Сейчас", replay: "Проверка на прошлом", review: "Разбор по полному архиву" };
const STATE_TEXT = { ok: "в порядке", stale: "данные устарели", no_data: "нет данных", disabled: "отключён",
  frozen: "заморожен", frozen_stale: "заморожен, данные устарели", error: "ошибка загрузки", paused: "заморожен", never: "ещё не загружался" };
const STAGES = { queued: "в очереди", start: "запуск", slice: "собираю данные", orbit: "считаю орбиту",
  radiation: "частицы от Солнца", mmod: "космический мусор", windows: "сравниваю варианты", done: "готово" };
const OVERRIDE_SOURCES = { goes_protons: "Частицы от Солнца (GOES)", swpc_alerts: "Сообщения SWPC",
  kp_observed: "Магнитная буря (Kp)", swpc_rsga: "Суточный прогноз RSGA", catalog_spacetrack: "Каталог мусора",
  meteor_forecast: "Метеорные потоки (NASA)", iss_spacetrack: "Орбита МКС" };
// фоновые причины: известны заранее (геометрия орбиты, годовой прогноз) — не делают вариант «плохим» сами по себе
const BACKGROUND = ["saa", "meteor_shower"];
// подписи полей в панели доказательств
const FIELD_TEXT = {
  source: "источник", product: "продукт", quantity: "величина", unit: "единицы", from: "данные с", to: "данные по",
  time: "время измерения", values_pfu: "значения, pfu", age_at_cutoff_h: "давность на момент расчёта, ч", fresh: "свежие",
  reconstructed: "восстановлено из архива", note: "примечание", code: "код сообщения", serial: "номер",
  title: "заголовок", issued_at: "опубликовано", valid_from: "действует с", valid_to: "действует до",
  begin_time: "начало события", end_time: "окончание события", scale: "уровень шкалы NOAA", model: "модель",
  ln_rate_per_h: "принятый темп, ln/ч", fitted_rate_per_h: "подогнанный темп, ln/ч", growth_cap: "ограничение роста, раз",
  fit_window_h: "окно подгонки, ч", horizon_h: "горизонт, ч", base_time: "последнее измерение",
  observed_until: "Kp измерен до", observed_reconstructed: "Kp — окончательные значения GFZ (реконструкция)",
  forecast_issues: "выпуски прогноза Kp", max_kp_in_interval: "максимум Kp на интервале", assumed_points: "точек с Kp по допущению",
  role: "роль", kp_shift_deg_per_kp: "сдвиг границы, °/Kp", saa_b_nt: "порог |B| для ЮАА, нТл",
  norad_id: "номер NORAD", object_name: "объект", object_type: "тип", tca: "момент наибольшего сближения",
  min_range_km: "минимальное расстояние, км", radial_km: "по радиусу, км", intrack_km: "вдоль орбиты, км",
  crosstrack_km: "поперёк орбиты, км", rel_speed_km_s: "относительная скорость, км/с", pass_type: "тип пролёта",
  crossing_angle_deg: "угол между скоростями, °", energy_per_gram_kj: "энергия 1 г вещества, кДж",
  tnt_equiv_per_gram_g: "то же в тротиле, г", in_control_box: "проход через зону контроля", element_epoch: "эпоха элементов",
  element_created: "элементы опубликованы", element_age_days: "возраст элементов, сут", raw_id: "исходный файл",
  objects_screened: "проверено объектов", excluded_coorbiting: "исключены (летят вместе с МКС)",
  latest_published: "последняя публикация каталога", cutoff: "учтено опубликованное до", control_box_km: "зона контроля, км",
  method: "метод", limitations: "ограничения", epoch: "эпоха элементов", created: "опубликованы", creation_policy: "время публикации",
  line1: "TLE, строка 1", line2: "TLE, строка 2", max_factor_in_interval: "максимум отношения к фону", threshold: "порог",
  showers: "потоки", visible_share: "доля времени, когда радиант виден",
};
const TIME_FIELDS = ["from", "to", "time", "issued_at", "valid_from", "valid_to", "begin_time", "end_time", "base_time",
  "observed_until", "tca", "element_epoch", "element_created", "latest_published", "cutoff", "epoch", "created"];
const PRESETS = {
  "0608": { review: false, asOf: "2024-06-08T03:00", planned: "2024-06-08T06:00" },
  "0510": { review: false, asOf: "2024-05-10T21:00", planned: "" },
  "0511": { review: true, asOf: "2024-05-11T00:00", planned: "" },
  "0526": { review: false, asOf: "2024-05-26T00:00", planned: "2024-05-26T00:45" },
  "1009": { review: false, asOf: "2024-10-09T06:00", planned: "" },
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
function put(parent, ...kids) { for (const k of kids.flat()) if (k !== null && k !== undefined && k !== false) parent.append(k); }
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const hm = (iso) => (iso ? iso.slice(11, 16) : "—");
const dm = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)} ${iso.slice(11, 16)}` : "—");
const dmy = (iso) => (iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)}` : "—");
const toIso = (local) => (local ? `${local}:00Z` : null);
const num = (v, d = 0) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const ageText = (s) => (s === null || s === undefined ? "—" : s < 120 ? `${s} с` : s < 7200 ? `${Math.round(s / 60)} мин` :
  s < 172800 ? `${(s / 3600).toFixed(1)} ч` : `${Math.round(s / 86400)} сут`);
const reasonText = (code, fallback) => REASON_PLAIN[code] || fallback || code;

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
  $("historyBox").classList.toggle("hidden", mode === "now");
}

function buildOverrides() {
  const box = $("overrides");
  box.innerHTML = "";
  for (const [key, title] of Object.entries(OVERRIDE_SOURCES)) {
    const sel = el("select", { "data-src": key },
      el("option", { value: "" }, "работает"), el("option", { value: "disabled" }, "отключить"),
      el("option", { value: "frozen" }, "заморозить на…"));
    const at = el("input", { type: "datetime-local", class: "hidden", "data-at": key, step: "60" });
    sel.addEventListener("change", () => at.classList.toggle("hidden", sel.value !== "frozen"));
    box.append(el("div", { class: "ov-row" }, el("span", {}, title), sel, at));
  }
}

function buildRequest() {
  const dur = Number($("durH").value) * 60 + Number($("durM").value);
  const mode = S.mode === "now" ? "now" : ($("reviewChk").checked ? "review" : "replay");
  const req = {
    mode, duration_min: dur, radiation_model: $("model").value, step_min: Number($("stepMin").value),
    eva: { return_to_airlock_min: Number($("retMin").value), overrun_margin_min: Number($("marginMin").value) },
    source_overrides: {},
  };
  if (mode !== "now") {
    if (!$("asOf").value) throw new Error("Укажите дату и время");
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
  $("progress").querySelector("span").textContent = `${STAGES[stage] || stage}…`;
}

// ждать, пока расчёт продвигается; если состояние не меняется STALL_MS — сообщить, а не ждать вечно
const STALL_MS = 180e3;
async function waitRun(id, onProgress, path = `/api/a/runs/${id}?include=summary`) {
  let last = "", since = Date.now();
  for (;;) {
    const r = await api(path);
    const p = r.progress || {};
    onProgress?.(p, r);
    if (r.status === "done") return r;
    if (r.status === "failed") throw new Error(`Не получилось посчитать: ${r.error}`);
    const key = `${r.status}/${p.stage}/${p.pct}`;
    if (key !== last) { last = key; since = Date.now(); }
    if (Date.now() - since > STALL_MS) {
      throw new Error(`Расчёт ${id} не продвигается ${STALL_MS / 60e3} мин (состояние: ${r.status}). ` +
        "Возможно, сервис перезапускался — запустите расчёт заново.");
    }
    await new Promise((ok) => setTimeout(ok, 700));
  }
}

async function pollRun(id) {
  await waitRun(id, (p, r) => showProgress(p.stage || r.status, p.pct || 0));
  await loadRun(id);
  $("progress").classList.add("hidden");
}

async function loadRun(id) {
  const run = await api(`/api/a/runs/${id}`);
  if (!run.result) throw new Error(run.error || "нет результата");
  S.prevDuration = S.pendingPlanChange && S.res ? S.res.search.duration_min : null;
  S.pendingPlanChange = false;
  S.run = run;
  S.res = run.result;
  history.replaceState(null, "", `?run=${id}`);
  render();
  refreshRuns();
}

// ---------- главный экран ----------
function render() {
  const r = S.res;
  $("empty").classList.add("hidden");
  $("result").classList.remove("hidden");
  const badge = $("modeBadge");
  badge.classList.remove("hidden");
  badge.textContent = r.mode === "now" ? "Сейчас" : `${MODE_TEXT[r.mode]} · ${dmy(r.as_of)} ${hm(r.as_of)} UTC`;
  S.byId = Object.fromEntries(r.windows.map((w) => [w.id, w]));
  const def = S.byId[r.recommendation.window] || S.byId[r.planned] || r.windows[0];
  S.sel = def?.id;
  renderPlanBanner();
  renderAnswer();
  renderStrip();
  renderDetails();
  selectWindow(S.sel);
}

function renderPlanBanner() {
  const b = $("planBanner");
  if (S.prevDuration) {
    b.classList.remove("hidden");
    b.textContent = `Изменён план: длительность ${S.prevDuration} → ${S.res.search.duration_min} мин. ` +
      "Варианты сравниваются только с вариантами той же длительности: короче — это другой план, а не «стало лучше».";
  } else b.classList.add("hidden");
}

// короткая фраза по механизму для окна
function mechSentence(m, st, dur) {
  const top = Object.keys(st.reasons || {});
  const nonSaa = top.filter((c) => !BACKGROUND.includes(c));
  if (st.worst === "acceptable") {
    return m === "mmod" ? "опасных сближений нет, метеорный фон обычный" : "без замечаний";
  }
  if (st.worst === "undesirable" && nonSaa.length === 0) {
    return st.reasons.saa ? `только обычные пролёты ЮАА (${num(st.reasons.saa)} мин) — зона повышенной радиации над Южной Атлантикой`
      : `опасных сближений нет; метеорный поток ${num(st.reasons.meteor_shower)} мин — мелких частиц больше обычного (статистика NASA)`;
  }
  const code = st.worst === "critical" ? top.find((c) => ["sep_s3", "sep_open_zone", "conjunction"].includes(c)) || top[0]
    : st.worst === "no_data" ? top.find((c) => ["no_observation", "obs_stale", "source_disabled", "no_forecast",
      "no_catalog", "catalog_stale"].includes(c)) || top[0]
      : nonSaa[0];
  const mins = st.worst === "critical" ? st.minutes.critical : st.worst === "no_data" ? st.minutes.no_data : st.minutes.undesirable;
  return `${CLASS_TEXT[st.worst]} — ${reasonText(code)} (${num(mins)} мин из ${dur})`;
}

function renderAnswer() {
  const r = S.res, rec = r.recommendation, best = S.byId[rec.window], dur = r.search.duration_min;
  const box = $("answer");
  box.innerHTML = "";
  const reasons = el("ul", { class: "reasons" });
  let big, sub, cls, adverse = false;
  if (best) {
    cls = "preferred";
    // если и лучший вариант с неблагоприятными условиями (не только фоновыми) — не выдавать его за хороший
    adverse = Object.values(best.mechanisms).some((st) => st.worst === "undesirable" &&
      Object.keys(st.reasons || {}).some((c) => !BACKGROUND.includes(c)));
    big = adverse ? `Хороших вариантов нет. Наименее неблагоприятный — начало в ${hm(best.start)} UTC`
      : `Лучше всего начать в ${hm(best.start)} UTC`;
    sub = `${dmy(best.start)} · выход до ${hm(best.end)} UTC · ${Math.floor(dur / 60)} ч ${dur % 60} мин`;
    for (const [m, st] of Object.entries(best.mechanisms)) {
      reasons.append(el("li", {}, el("i", { style: `background:${CLASS_COLOR[st.worst]}` }),
        `${MECH_TEXT[m]}: ${mechSentence(m, st, dur)}`));
    }
    const ov = best.overrun;
    reasons.append(el("li", {}, el("i", { style: `background:${ov.robust ? CLASS_COLOR.acceptable : CLASS_COLOR.undesirable}` }),
      ov.robust ? `Если работа затянется — стоп-факторов нет ещё минимум ${ov.margin_min} мин после конца`
        : `Если работа затянется на ${num(ov.first_critical_after_end_min)} мин — начнётся стоп-фактор`));
    const n = rec.n_equivalent || 0;
    if (n) reasons.append(el("li", {}, el("i", { style: "background:#3a9d5d" }),
      `Ещё ${n} ${n === 1 ? "вариант почти такой же" : "вариантов почти такие же"} — отмечены зелёным на полосе ниже`));
    const plan = S.byId[r.planned];
    if (plan && plan.id !== best.id) {
      const worstM = Object.entries(plan.mechanisms).sort((a, b) =>
        ["acceptable", "undesirable", "no_data", "critical"].indexOf(b[1].worst) - ["acceptable", "undesirable", "no_data", "critical"].indexOf(a[1].worst))[0];
      reasons.append(el("li", {}, el("i", { style: `background:${CLASS_COLOR[worstM[1].worst]}` }),
        `Ваш план (${hm(plan.start)}) — ${STATUS_TEXT[plan.status]}. ${MECH_TEXT[worstM[0]]}: ${mechSentence(worstM[0], worstM[1], dur)}`));
    }
  } else if (rec.status === "insufficient_basis") {
    cls = "insufficient_basis";
    big = "Рекомендацию дать нельзя";
    const bad = r.sources.filter((s) => !["ok", "frozen"].includes(s.state) && !["kp_forecast", "iss_celestrak", "swpc_3day"].includes(s.source));
    sub = "Не хватает данных — это не значит, что всё хорошо";
    reasons.append(el("li", {}, el("i", { style: "background:#8a929c" }),
      bad.length ? `Проблемы с источниками: ${bad.map((s) => `${OVERRIDE_SOURCES[s.source] || s.source} — ${STATE_TEXT[s.state] || s.state}`).join("; ")}`
        : "Во всех вариантах есть интервалы без данных"));
  } else {
    cls = "no_window";
    big = "Подходящего времени нет";
    sub = "Во всех вариантах в заданном промежутке есть стоп-фактор";
    const counts = {};
    r.windows.forEach((w) => Object.values(w.mechanisms).forEach((st) => Object.keys(st.reasons).forEach((c) => {
      if (st.worst === "critical") counts[c] = (counts[c] || 0) + 1;
    })));
    const topc = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([c]) => reasonText(c));
    if (topc.length) reasons.append(el("li", {}, el("i", { style: "background:#d64545" }), `Причина: ${topc.join("; ")}`));
  }
  const o = r.orbit || {};
  reasons.append(el("li", { class: "muted" }, el("i", { style: "background:#4a9eff" }),
    `Орбита МКС: ${o.source === "iss_spacetrack" ? "Space-Track" : o.source === "iss_celestrak" ? "CelesTrak" : o.source} · ` +
    `элементы на ${dm(o.epoch)} UTC, опубликованы ${dm(o.created)} UTC · ` +
    (o.age_at_cutoff_h >= 0 ? `давность ${num(o.age_at_cutoff_h, 1)} ч` : `эпоха позже выбранного момента на ${num(-o.age_at_cutoff_h, 1)} ч (разбор по архиву)`)));
  const conf = rec.confidence;
  const next = (r.recheck_after || [])[0];
  const note = conf === "low"
    ? el("div", { class: "note" }, "Прогноз на это время ненадёжен: дальше 6 часов по частицам известна только суточная " +
      "вероятность, а дальше 12 часов с новыми данными об орбитах могут появиться сближения. " +
      (next ? `Пересчитайте после ${dm(next.time)} UTC.` : "Пересчитайте ближе к началу."))
    : null;
  const watchText = r.mode === "now" ? "Следить и предупредить, если станет хуже" : "Показать предупреждения, как в тот день";
  put(box,
    el("div", { class: "verdict" }, el("span", { class: "big" }, big),
      best ? el("span", { class: `pill ${conf === "low" ? "low" : "worse"}` }, `надёжность прогноза: ${CONF_TEXT[conf] || conf}`) : null,
      best ? el("span", { class: `pill ${adverse ? "worse" : cls}` }, adverse ? "наименее неблагоприятный"
        : rec.status === "equivalent" ? "есть равноценные" : "лучший вариант") : null),
    el("div", { class: "sub" }, sub),
    reasons, note,
    el("div", { class: "actions" },
      best ? el("button", { type: "button", class: "main", onclick: () => whyWindow(best.id) }, "Почему так? Откуда данные") : null,
      best ? el("button", { type: "button", onclick: () => createWatch(best.id) }, watchText) : null,
      el("a", { href: `/runs/${S.run.run_id}/brief`, target: "_blank" }, "Сводка для руководителя"),
      el("a", { href: `/runs/${S.run.run_id}/export.zip` }, "Сохранить (ZIP)")));
}

function renderStrip() {
  const r = S.res, strip = $("strip"), axis = $("stripAxis");
  strip.innerHTML = "";
  axis.innerHTML = "";
  const n = r.windows.length;
  r.windows.forEach((w) => {
    const isBest = w.id === r.recommendation.window;
    const c = el("div", { class: `cell ${STATUS_STRIP[w.status] || "s-worse"} ${isBest ? "s-best best" : ""} ${w.is_planned ? "plan" : ""}`,
      "data-id": w.id, title: `${hm(w.start)}–${hm(w.end)} · ${STATUS_TEXT[w.status]}`, onclick: () => selectWindow(w.id) });
    strip.append(c);
  });
  // подписи времени: примерно 6 меток
  const step = Math.max(1, Math.round(n / 6));
  for (let i = 0; i < n; i += step) {
    const w = r.windows[i];
    axis.append(el("span", { style: `left:${((i + 0.5) / n) * 100}%` }, i === 0 ? dm(w.start) : hm(w.start)));
  }
}

function selectWindow(id) {
  const w = S.byId?.[id];
  if (!w) return;
  S.sel = id;
  document.querySelectorAll("#strip .cell").forEach((c) => c.classList.toggle("sel", c.dataset.id === id));
  renderVariant(w);
  if (S.detailsOpen) {
    document.querySelectorAll("#winTable tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.id === id));
    document.querySelector(`#winTable tr[data-id="${id}"]`)?.scrollIntoView({ block: "nearest" });
    if ($("timeline").layout) Plotly.relayout("timeline", { shapes: S.baseShapes.concat(selShape()) });
    if (S.tab === "map") renderMap();
  }
}

function windowIntervals(w) {
  // соседние интервалы с той же причиной (различаются только уверенностью или доказательствами) — одной строкой
  const out = [];
  for (const [m, list] of Object.entries(S.res.timeline)) {
    list.forEach((iv) => {
      if (iv.class === "acceptable" || iv.to <= w.start || iv.from >= w.end) return;
      const prev = out[out.length - 1];
      if (prev && prev[0] === m && prev[1].class === iv.class && prev[1].reason === iv.reason && prev[1].to === iv.from) {
        prev[1] = { ...prev[1], to: iv.to, evidence: [...new Set([...prev[1].evidence, ...iv.evidence])] };
      } else out.push([m, { ...iv }]);
    });
  }
  return out;
}

function renderVariant(w) {
  const r = S.res, dur = r.search.duration_min, box = $("variant");
  box.innerHTML = "";
  const isBest = w.id === r.recommendation.window;
  const ul = el("ul");
  for (const [m, st] of Object.entries(w.mechanisms)) {
    ul.append(el("li", {}, el("i", { style: `background:${CLASS_COLOR[st.worst]}` }), `${MECH_TEXT[m]}: ${mechSentence(m, st, dur)}`));
  }
  const ov = w.overrun;
  ul.append(el("li", {}, el("i", { style: `background:${ov.robust ? CLASS_COLOR.acceptable : CLASS_COLOR.undesirable}` }),
    ov.robust ? `Если затянется: стоп-факторов нет ещё ${ov.margin_min}+ мин` : `Если затянется на ${num(ov.first_critical_after_end_min)} мин — стоп-фактор`));
  ul.append(el("li", {}, el("i", { style: "background:#4a9eff" }), `Надёжность прогноза: ${CONF_TEXT[w.confidence] || w.confidence}`));
  const chips = el("div", { class: "ivs" });
  windowIntervals(w).slice(0, 12).forEach(([m, iv]) => {
    const a = iv.from > w.start ? iv.from : w.start, z = iv.to < w.end ? iv.to : w.end;  // в пределах окна
    chips.append(el("span", { class: `iv ${iv.class}`, title: `Откуда эти данные. Надёжность: ${CONF_TEXT[iv.confidence] || iv.confidence}` +
      (iv.confidence_reason ? ` — ${iv.confidence_reason}` : ""),
      onclick: () => openEvidence(iv.evidence, `${MECH_TEXT[m]} · ${dm(iv.from)}–${hm(iv.to)}`, iv) },
    `${hm(a)}–${hm(z)}: ${reasonText(iv.reason, iv.reason_text)}`));
  });
  (r.events.mmod || []).filter((e) => e.tca >= w.start && e.tca <= w.end && isNotableConj(e)).forEach((e) =>
    chips.append(el("span", { class: `iv ${e.in_control_box ? "critical" : ""}`,
      onclick: () => openEvidence([`conj:${e.norad_id}:${e.tca}`, "catalog"], `Сближение: ${e.object_name}`) },
    `${hm(e.tca)}: ${e.object_name} в ${num(e.min_range_km, 1)} км, ${e.pass_type || ""} пролёт ${num(e.rel_speed_km_s, 1)} км/с` +
    `${e.in_control_box ? ` — опасно близко (1 г на такой скорости ≈ ${num(e.tnt_equiv_per_gram_g)} г тротила)` : ""}`)));
  const watchText = r.mode === "now" ? "Следить за этим вариантом" : "Показать предупреждения для этого варианта";
  put(box,
    el("div", { class: "vh" }, el("b", {}, `Начало ${hm(w.start)} → конец ${hm(w.end)} UTC`),
      el("span", { class: `pill ${w.status}` }, isBest ? "лучший вариант" : STATUS_TEXT[w.status]),
      w.is_planned ? el("span", { class: "pill worse" }, "ваш план") : null),
    ul,
    chips.childNodes.length ? el("div", { class: "muted" }, "Что влияет (нажмите, чтобы увидеть данные):") : null,
    chips.childNodes.length ? chips : null,
    el("div", { class: "actions" },
      el("button", { type: "button", class: "btn", onclick: () => whyWindow(w.id) }, "Откуда данные"),
      el("button", { type: "button", class: "btn", onclick: () => createWatch(w.id) }, watchText)));
}

function whyWindow(id) {
  const w = S.byId[id];
  const keys = new Set(["orbit"]);
  windowIntervals(w).forEach(([, iv]) => iv.evidence.forEach((k) => keys.add(k)));
  // если всё спокойно — показать, на чём основана оценка
  for (const list of Object.values(S.res.timeline)) {
    list.forEach((iv) => { if (iv.to > w.start && iv.from < w.end) iv.evidence.forEach((k) => keys.add(k)); });
  }
  openEvidence([...keys], `Откуда данные: ${hm(w.start)}–${hm(w.end)} UTC`);
}

// ---------- подробности ----------
function toggleDetails(force) {
  S.detailsOpen = force ?? !S.detailsOpen;
  $("details").classList.toggle("hidden", !S.detailsOpen);
  $("detailsBtn").textContent = S.detailsOpen ? "Скрыть подробности ▴" : "Подробности: графики, таблица, карта, источники ▾";
  if (S.detailsOpen && S.res) { renderDetails(); selectWindow(S.sel); }
}

function renderDetails() {
  if (!S.detailsOpen) return;
  renderRec();
  renderTimeline();
  renderWindows();
  renderSources();
  renderVerifyPane();
  renderExport();
  showTab(S.tab);
}

function renderRec() {
  const r = S.res, box = $("recCard");
  box.innerHTML = "";
  put(box, el("h2", {}, "Полное объяснение"),
    el("div", { class: "flags" },
      el("span", { class: "flag" }, `варианты: начало с ${dm(r.search.earliest_start)} по ${dm(r.search.latest_start)} UTC, шаг ${r.search.step_min} мин`),
      r.reconstruction ? el("span", { class: "flag warn" }, "использованы восстановленные архивные данные") : null,
      ...r.sources.filter((s) => !["ok", "frozen"].includes(s.state) && !(s.state === "no_data" && ["kp_forecast", "iss_celestrak"].includes(s.source)))
        .map((s) => el("span", { class: "flag warn", title: s.last_error || "" }, `${s.source}: ${s.state}`))),
    el("ul", {}, ...r.explanation.text.map((t) => el("li", {}, t))),
    r.recheck_after?.length ? el("div", { class: "muted" }, "Когда пересчитать: " +
      r.recheck_after.slice(0, 3).map((x) => `${dm(x.time)} UTC — ${x.reason}`).join("; ")) : null,
    el("h3", {}, "Ограничения оценки"),
    el("ul", { class: "muted" }, ...(r.limitations || []).map((t) => el("li", {}, t))),
    el("div", { class: "muted" }, `Версия алгоритма ${r.algorithm_version}; настройки ${Object.entries(r.config?.sha256 || {}).map(([k, v]) => `${k} ${v}`).join(", ")}`));
}

function seriesArr(key) { return (S.res.series[key] || []).map((v) => (v === null ? null : v)); }

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
      xs.push(new Date((Date.parse(iv.from) + Date.parse(iv.to)) / 2).toISOString()); ys.push(0.5); cd.push([m, i]);
      texts.push(`<b>${MECH_TEXT[m]}: ${CLASS_TEXT[iv.class]}</b><br>${esc(reasonText(iv.reason, iv.reason_text))}<br>` +
        `${KIND_TEXT[iv.kind] || iv.kind} · надёжность ${CONF_TEXT[iv.confidence] || iv.confidence}` +
        (iv.confidence_reason ? `<br><i>${esc(iv.confidence_reason)}</i>` : "") + `<br>${dm(iv.from)}–${hm(iv.to)} UTC`);
    });
    traces.push({ x: xs, y: ys, yaxis: yref, mode: "markers", marker: { size: 14, opacity: 0 }, hoverinfo: "text",
      text: texts, customdata: cd, showlegend: false });
  });
  const line = (key, name, color, dash, yaxis = "y3", shape = "linear") => ({
    x: t, y: seriesArr(key), name, yaxis, mode: "lines", connectgaps: false,
    line: { color, width: 1.5, dash, shape }, hovertemplate: `${name}: %{y:.3g}<extra></extra>` });
  // измерено (до T) — сплошной линией, прогноз (после T) — пунктиром
  const split = (key, name, color) => {
    const y = seriesArr(key);
    return [
      { x: t, y: y.map((v, i) => (t[i] <= r.as_of ? v : null)), name, legendgroup: key, yaxis: "y3", mode: "lines",
        line: { color, width: 1.5 }, hovertemplate: `${name}: %{y:.3g}<extra>измерено</extra>` },
      { x: t, y: y.map((v, i) => (t[i] >= r.as_of ? v : null)), name: `${name} — прогноз`, legendgroup: key, showlegend: false,
        yaxis: "y3", mode: "lines", line: { color, width: 1.5, dash: "dash" }, hovertemplate: `${name}: %{y:.3g}<extra>прогноз</extra>` },
    ];
  };
  if (s["radiation.p_ge10"]) {
    traces.push(...split("radiation.p_ge10", "частицы ≥10 МэВ у спутника GOES", "#4a9eff"));
    traces.push(...split("radiation.p_ge100", "частицы ≥100 МэВ у спутника GOES", "#a67cf0"));
    traces.push(line("radiation.j_iss", "долетает до станции (оценка)", "#f07c4a", "dot"));
    traces.push(line("radiation.kp", "магнитная буря, Kp", "#cfd5dc", "solid", "y4", "hv"));
    traces.push({ x: t, y: seriesArr("radiation.saa").map((v) => (v ? 8.5 : null)), yaxis: "y4", mode: "lines",
      name: "пролёт ЮАА", line: { color: "#e0a030", width: 6 }, hoverinfo: "skip" });
  }
  shapes.push({ type: "line", xref: "x", yref: "paper", x0: r.as_of, x1: r.as_of, y0: 0, y1: 1,
    line: { color: "#fff", width: 1, dash: "dash" } });
  // где заканчивается количественный прогноз потока — дальше только суточная вероятность
  const extraNotes = [];
  const p10 = seriesArr("radiation.p_ge10");
  let lastIdx = -1;
  p10.forEach((v, i) => { if (v !== null) lastIdx = i; });
  if (s["radiation.p_ge10"] && lastIdx < t.length - 1) {
    const from = lastIdx >= 0 ? t[lastIdx] : t[0];
    const probs = seriesArr("radiation.prob_sep").slice(lastIdx + 1).filter((v) => v !== null);
    const pmax = probs.length ? Math.max(...probs) : null;
    const text = lastIdx < 0 ? "нет измерений потока частиц — оценить нельзя"
      : from < r.as_of ? "нет свежих измерений — прогноз не строится"
        : pmax !== null ? `дальше 6 ч по минутам не предсказать — есть только суточная вероятность события: до ${num(pmax)}%`
          : "дальше прогноза нет";
    shapes.push({ type: "rect", xref: "x", yref: "paper", x0: from, x1: t[t.length - 1], y0: 0.34, y1: 0.74,
      line: { width: 0 }, fillcolor: "rgba(138,146,156,0.13)" });
    extraNotes.push({ xref: "x", yref: "paper", x: from, y: 0.72, xanchor: "left", yanchor: "top", xshift: 6, showarrow: false,
      text: text.replace(" — ", "<br>"), align: "left", font: { color: "#cfd5dc", size: 11 },
      bgcolor: "rgba(22,29,37,0.8)" });
  }
  // где Kp неизвестен и принят консервативно
  const kpA = seriesArr("radiation.kp_assumed");
  const aIdx = kpA.findIndex((v) => v === 1);
  if (aIdx >= 0) {
    const bIdx = kpA.length - 1 - [...kpA].reverse().findIndex((v) => v === 1);
    shapes.push({ type: "rect", xref: "x", yref: "paper", x0: t[aIdx], x1: t[bIdx], y0: 0, y1: 0.26,
      line: { width: 0 }, fillcolor: "rgba(138,146,156,0.13)" });
    extraNotes.push({ xref: "x", yref: "paper", x: t[aIdx], y: 0.25, xanchor: "left", yanchor: "top", xshift: 6, showarrow: false,
      text: "Kp неизвестен (нет прогноза) — принят 5 с запасом", font: { color: "#cfd5dc", size: 11 }, bgcolor: "rgba(22,29,37,0.8)" });
  }
  const rec = S.byId[r.recommendation.window], plan = S.byId[r.planned];
  if (rec) shapes.push({ type: "rect", xref: "x", yref: "paper", x0: rec.start, x1: rec.end, y0: 0.77, y1: 1,
    line: { color: "#58d68d", width: 2 }, fillcolor: "rgba(0,0,0,0)" });
  if (plan && plan !== rec) shapes.push({ type: "rect", xref: "x", yref: "paper", x0: plan.start, x1: plan.end,
    y0: 0.77, y1: 1, line: { color: "#cfd5dc", width: 1, dash: "dot" }, fillcolor: "rgba(0,0,0,0)" });
  S.baseShapes = shapes;
  const layout = {
    paper_bgcolor: "#161d25", plot_bgcolor: "#161d25", font: { color: "#dfe6ee", size: 11 },
    margin: { l: 110, r: 10, t: 10, b: 30 }, hovermode: "closest", showlegend: true,
    legend: { orientation: "h", y: -0.08, font: { size: 11 } },
    xaxis: { type: "date", gridcolor: "#2a3542", anchor: "y4" },
    yaxis: { domain: [0.9, 0.98], range: [0, 1], visible: false, fixedrange: true },
    yaxis2: { domain: [0.8, 0.88], range: [0, 1], visible: false, fixedrange: true },
    yaxis3: { domain: [0.34, 0.74], type: "log", title: { text: "частиц, pfu" }, gridcolor: "#2a3542", exponentformat: "power" },
    yaxis4: { domain: [0, 0.26], range: [0, 9.5], title: { text: "Kp" }, gridcolor: "#2a3542" },
    shapes: shapes.concat(selShape()),
    annotations: [
      ...bandRows.map((m, i) => ({ xref: "paper", yref: "paper", x: 0, y: i === 0 ? 0.94 : 0.84, xanchor: "right",
        text: MECH_TEXT[m], showarrow: false, xshift: -6 })),
      { xref: "x", yref: "paper", x: r.as_of, y: 0.77, text: "T", showarrow: false, xanchor: "left", xshift: 3, font: { color: "#fff" } },
      ...extraNotes,
    ],
  };
  Plotly.react("timeline", traces, layout, { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d"] });
  const tl = $("timeline");
  tl.removeAllListeners?.("plotly_click");
  tl.on("plotly_click", (ev) => {
    const p = ev.points?.[0];
    if (p?.customdata && Array.isArray(p.customdata)) {
      const [m, i] = p.customdata;
      const iv = S.res.timeline[m][i];
      openEvidence(iv.evidence, `${MECH_TEXT[m]} · ${dm(iv.from)}–${hm(iv.to)}`, iv);
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
  const r = S.res, mechs = Object.keys(r.timeline), tbl = $("winTable"), hide = $("hideBad").checked;
  const head = el("tr", {}, el("th", {}, "Начало – конец, UTC"), el("th", {}, "Оценка"),
    ...mechs.map((m) => el("th", { class: "num" }, `${MECH_TEXT[m]}: стоп / нежел. / нет данных, мин`)),
    mechs.includes("radiation") ? el("th", { class: "num", title: "Сумма оценки потока, долетающего до станции, за окно" }, "Частиц за окно, pfu·мин") : null,
    el("th", { class: "num" }, "Если затянется"), el("th", {}, "Надёжность"), el("th", {}, "№"));
  const rows = r.windows.filter((w) => !hide || w.status !== "not_recommended").map((w) => {
    const cells = mechs.map((m) => {
      const mn = w.mechanisms[m].minutes;
      const c = (v, k) => el("span", { class: `cl ${v > 0 ? k : "z"}` }, num(v));
      return el("td", { class: "num" }, c(mn.critical, "critical"), " / ", c(mn.undesirable, "undesirable"), " / ", c(mn.no_data, "no_data"));
    });
    const ov = w.overrun;
    return el("tr", { "data-id": w.id, class: [w.id === r.recommendation.window ? "rec" : "", w.id === S.sel ? "sel" : ""].join(" "),
      onclick: () => selectWindow(w.id) },
      el("td", {}, `${dm(w.start)} – ${hm(w.end)}${w.id === r.recommendation.window ? " ★" : ""}${w.is_planned ? " (план)" : ""}`),
      el("td", {}, el("span", { class: `pill ${w.status}`, title: w.rule_text ? `${w.rule}: ${w.rule_text}` : "" }, STATUS_TEXT[w.status] || w.status)),
      ...cells,
      mechs.includes("radiation") ? el("td", { class: "num" }, num(w.mechanisms.radiation.exposure_pfu_min, 1)) : null,
      el("td", { class: "num" }, ov.robust ? `≥ ${ov.margin_min} мин ок` : `стоп через ${num(ov.first_critical_after_end_min)} мин`),
      el("td", {}, CONF_TEXT[w.confidence] || w.confidence), el("td", { class: "muted" }, w.id));
  });
  tbl.innerHTML = "";
  tbl.append(el("thead", {}, head), el("tbody", {}, ...rows));
  $("durEdit").value = r.search.duration_min;
}

// сближения, которые показываем в деталях и на карте (остальные — в полном отчёте)
const NOTABLE_CONJ_KM = 10;
function isNotableConj(e) { return e.in_control_box || e.min_range_km <= NOTABLE_CONJ_KM; }

function renderMap() {
  const r = S.res, s = r.series, w = S.byId[S.sel];
  if (!w) return;
  const t = s.times, cls = s["radiation.class"] || [];
  const clsName = { "-1": "no_data", 0: "acceptable", 1: "undesirable", 2: "critical" };
  const lat = [], lon = [], col = [], txt = [];
  t.forEach((x, i) => {
    if (x < w.start || x >= w.end) return;
    lat.push(s.lat[i]); lon.push(s.lon[i]);
    const c = clsName[String(cls[i])] || "acceptable";
    col.push(CLASS_COLOR[c]);
    txt.push(`${dm(x)} · ${CLASS_TEXT[c]}<br>высота ${num(s.alt_km[i])} км, геомагнитная широта ${num(s.mlat[i], 1)}°` +
      (s["radiation.e_cut_mev"] ? `<br>магнитное поле пропускает частицы от ${num(s["radiation.e_cut_mev"][i])} МэВ` : ""));
  });
  const traces = [
    { type: "scattergeo", lat: s.lat, lon: s.lon, mode: "lines", line: { color: "#3b4654", width: 1 }, hoverinfo: "skip", name: "весь путь" },
    { type: "scattergeo", lat, lon, mode: "markers", marker: { size: 5, color: col }, text: txt, hoverinfo: "text",
      name: `выход ${hm(w.start)}–${hm(w.end)}` },
  ];
  const conj = (r.events.mmod || []).filter((e) => e.tca >= w.start && e.tca <= w.end && isNotableConj(e));
  if (conj.length) {
    const idx = conj.map((e) => t.findIndex((x) => x >= e.tca));
    traces.push({ type: "scattergeo", lat: idx.map((i) => s.lat[i]), lon: idx.map((i) => s.lon[i]), mode: "markers",
      marker: { size: 12, symbol: "x", color: conj.map((e) => (e.in_control_box ? "#ff5a5a" : "#cfd5dc")) },
      text: conj.map((e) => `${e.object_name} · ${hm(e.tca)} · ${num(e.min_range_km, 1)} км · ${e.pass_type || ""} ${num(e.rel_speed_km_s, 1)} км/с`),
      hoverinfo: "text", name: "сближения" });
  }
  Plotly.react("map", traces, {
    paper_bgcolor: "#161d25", font: { color: "#dfe6ee", size: 11 }, margin: { l: 0, r: 0, t: 0, b: 0 },
    showlegend: true, legend: { orientation: "h", y: 0 },
    geo: { projection: { type: "natural earth" }, showland: true, landcolor: "#223041", showocean: true,
      oceancolor: "#121a23", coastlinecolor: "#51606f", bgcolor: "#161d25", lataxis: { range: [-65, 65] } },
  }, { displaylogo: false, responsive: true, topojsonURL: "/static/vendor/" });
  $("mapHint").textContent = "Цвет точки — обстановка в этот момент. Ближе к полюсам магнитное поле Земли хуже защищает от " +
    "частиц; над Южной Атлантикой — зона повышенной радиации (ЮАА).";
}

// ---------- доказательства ----------
function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "object") return esc(JSON.stringify(v));
  return esc(v);
}

function openEvidence(keys, title, iv) {
  S.drawerMode = "evidence";
  const body = $("drawerBody");
  body.innerHTML = "";
  $("drawerTitle").textContent = title || "Откуда данные";
  body.append(el("p", { class: "hint" }, "Каждая оценка опирается на данные ниже. Метка показывает тип: ",
    el("b", {}, "измерение"), " (было на самом деле), ", el("b", {}, "внешний прогноз"), " (NOAA SWPC) или ",
    el("b", {}, "наш расчёт"), ". По ссылке «исходный файл» — то, что было скачано из источника."));
  if (iv) {
    body.append(el("div", { class: "ev" },
      el("div", { class: "h" }, el("span", { class: `kind ${iv.kind}` }, KIND_TEXT[iv.kind] || iv.kind), el("span", { class: "layer" }, "итог для выхода")),
      el("div", { class: "kv" }, el("b", {}, "Оценка"), CLASS_TEXT[iv.class], el("b", {}, "Почему"), reasonText(iv.reason, iv.reason_text),
        el("b", {}, "Когда"), `${dm(iv.from)} – ${dm(iv.to)} UTC`, el("b", {}, "Надёжность"), CONF_TEXT[iv.confidence] || iv.confidence,
        iv.confidence_reason ? el("b", {}, "От чего зависит") : null, iv.confidence_reason || null)));
  }
  const ev = S.res.evidence;
  for (const k of keys || []) {
    const e = ev[k];
    if (!e) continue;
    const kv = el("div", { class: "kv" });
    for (const [f, v] of Object.entries(e)) {
      if (["layer", "kind", "raw_ref"].includes(f) || v === null) continue;
      const val = TIME_FIELDS.includes(f) && typeof v === "string" && v.endsWith("Z") ? `${dm(v)} UTC` : v;
      kv.append(el("b", { title: f }, FIELD_TEXT[f] || f), el("span", { html: fmtVal(val) }));
    }
    const links = el("div", { class: "flags" });
    const rr = e.raw_ref || {};
    const ids = rr.raw_ids || (rr.raw_id ? [rr.raw_id] : []);
    const loc = rr.locator ? `?locator=${encodeURIComponent(rr.locator)}` : "";
    ids.slice(0, 6).forEach((id) => links.append(el("a", { class: "btn", href: `/api/i/raw/${id}${loc}`, target: "_blank" }, `исходный файл #${id}`),
      el("a", { class: "btn", href: `/api/i/raw/${id}/meta`, target: "_blank" }, "откуда и когда скачан")));
    if (rr.locator) links.append(el("span", { class: "flag" }, `запись: ${rr.locator}`));
    body.append(el("div", { class: "ev" },
      el("div", { class: "h" }, el("span", { class: `kind ${e.kind}` }, KIND_TEXT[e.kind] || e.kind),
        el("span", { class: "layer" }, LAYER_TEXT[e.layer] || e.layer || ""), el("code", {}, k)),
      kv, ids.length || rr.locator ? links : null));
  }
  openDrawer();
}

function openDrawer() { $("drawer").classList.remove("hidden"); $("drawer").setAttribute("aria-hidden", "false"); }
function closeDrawer() { $("drawer").classList.add("hidden"); $("drawer").setAttribute("aria-hidden", "true"); S.drawerMode = null; }

// ---------- вкладки подробностей ----------
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
  put(box, el("h3", {}, "Данные, на которых построен ответ"),
    el("p", { class: "hint" }, r.mode === "replay"
      ? `Учтены только данные, опубликованные до ${dm(r.as_of)} UTC; «отброшено» — вышедшие позже (в расчёт не попали).`
      : "Давность — сколько прошло с последних данных."),
    el("div", { class: "tablewrap" }, el("table", { class: "tbl" },
      el("thead", {}, el("tr", {}, ...["Источник", "Состояние", "Последние данные", "Давность", "Использовано",
        "Отброшено (вышли позже)", "Восстановлено из архива", "Отключён/заморожен"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ...rows))),
    el("h3", {}, "Орбита МКС"),
    el("div", { class: "kv" }, el("b", {}, "Источник"), r.orbit.source, el("b", {}, "Эпоха элементов"), `${dm(r.orbit.epoch)} UTC`,
      el("b", {}, "Опубликовано"), `${dm(r.orbit.created)} UTC`, el("b", {}, "Давность"),
      r.orbit.age_at_cutoff_h >= 0 ? `${num(r.orbit.age_at_cutoff_h, 1)} ч` : `эпоха позже выбранного момента на ${num(-r.orbit.age_at_cutoff_h, 1)} ч (разбор по архиву)`,
      el("b", {}, "TLE"), el("code", {}, `${r.orbit.line1}\n${r.orbit.line2}`)),
    el("h3", {}, "Сбор данных прямо сейчас"),
    el("p", { class: "hint" }, "«Заморозить» здесь останавливает обновление источника для всех пользователей сервиса (не дольше 2 ч). " +
      "Чтобы проверить поведение при сбое только в своём расчёте, используйте «Дополнительно» → «Проверка сбоя» в форме."),
    el("div", { id: "liveSources", class: "tablewrap" }, "загрузка…"));
}

async function loadLiveSources() {
  const box = document.getElementById("liveSources");
  let list;
  try { list = await api("/api/i/sources"); } catch (e) { if (box) box.textContent = `сбор данных недоступен: ${e.message}`; return; }
  renderChips(list);
  if (!box) return;
  const act = async (name, what) => {
    try { await api(`/api/i/sources/${name}/${what}`, { method: "POST" }); toast(`${name}: ${what === "refresh" ? "обновляю" : what === "pause" ? "заморожен" : "возобновлён"}`); }
    catch (e) { toast(e.message, "warning"); }
    setTimeout(loadLiveSources, 1500);
  };
  box.innerHTML = "";
  box.append(el("table", { class: "tbl" },
    el("thead", {}, el("tr", {}, ...["Источник", "Состояние", "Последние данные, UTC", "Давность", "Последняя успешная загрузка, UTC",
      "Как часто", "Ошибка", ""].map((h) => el("th", {}, h)))),
    el("tbody", {}, ...list.map((s) => el("tr", {},
      el("td", { title: s.title || "" }, s.title || s.source), el("td", {}, el("span", { class: `chip ${s.status}` }, s.status)),
      el("td", {}, dm(s.latest_data)), el("td", { class: "num" }, ageText(s.age_s)), el("td", {}, dm(s.last_success)),
      el("td", { class: "num" }, ageText(s.interval_s)), el("td", { title: s.last_error || "" }, (s.last_error || "—").slice(0, 40)),
      el("td", {}, el("button", { class: "btn", type: "button", onclick: () => act(s.source, "refresh") }, "обновить"), " ",
        s.paused ? el("button", { class: "btn", type: "button", title: s.paused_until ? `снимется сама в ${dm(s.paused_until)} UTC` : "",
          onclick: () => act(s.source, "resume") }, "возобновить")
          : el("button", { class: "btn", type: "button", onclick: () => act(s.source, "pause") }, "заморозить")))))));
}

function renderChips(list) {
  const bad = list.filter((s) => !["ok"].includes(s.status));
  const box = $("srcChips");
  box.innerHTML = "";
  box.append(el("span", { class: `chip ${bad.length ? "stale" : "ok"}`, title: bad.map((s) => `${s.title || s.source}: ${s.status}`).join("\n") },
    bad.length ? `данные: ${bad.length} из ${list.length} с замечаниями` : `данные: все ${list.length} источников в порядке`));
  const paused = list.filter((s) => s.paused).map((s) => `${OVERRIDE_SOURCES[s.source] || s.title || s.source}` +
    (s.paused_until ? ` до ${hm(s.paused_until)} UTC` : ""));
  if (paused.length) box.append(el("span", { class: "chip paused", title: "Обновление заморожено для всех пользователей. " +
    "Возобновить — «Подробности» → «Данные и источники». Проверить сбой только для своего расчёта — «Дополнительно» в форме." },
  `заморожено: ${paused.join(", ")}`));
  const b = S.boot;
  if (b && (b.state === "running" || b.state === "errors")) {
    const errs = Object.entries(b.sources || {}).filter(([, x]) => x.errors?.length).map(([n, x]) => `${n}: ${x.errors[0]}`);
    box.append(el("span", { class: `chip ${b.state === "running" ? "stale" : "error"}`,
      title: b.state === "running" ? `Догружается архив (${(b.ranges || []).map((r) => `${r.from} – ${r.to}`).join(", ")}): ${b.current || ""}. ` +
        "Расчёты на ещё не загруженные дни покажут «нет данных»." : `Ошибки загрузки архива (повтор при следующем запуске):\n${errs.join("\n")}` },
    b.state === "running" ? `архив: загружено ${b.done_days ?? 0} из ${b.total_days ?? "…"} дней` : "архив: есть ошибки загрузки"));
  }
}

async function refreshChips() {
  try { S.boot = await api("/api/i/bootstrap"); } catch { S.boot = null; }
  try { renderChips(await api("/api/i/sources")); } catch { /* сервис сбора может ещё стартовать */ }
}

function renderVerifyPane() {
  const box = $("tab-verify");
  box.innerHTML = "";
  if (S.res.mode !== "replay") {
    box.append(el("p", { class: "hint" }, "Доступно для «Прошлой даты» без галочки «Разбор»: сравниваем, что система ответила бы тогда, с тем, что было на самом деле."));
    return;
  }
  put(box, el("p", {}, "Система ответила, зная только данные до выбранного момента. Теперь посчитаем то же самое по всему архиву и сравним."),
    el("button", { class: "btn primary", type: "button", onclick: runVerify }, "Что было на самом деле"),
    el("div", { id: "verifyOut" }));
}

async function runVerify() {
  const out = $("verifyOut");
  out.textContent = "сравниваю…";
  try {
    const { run_id } = await api(`/api/a/runs/${S.run.run_id}/verify`, { method: "POST" });
    const r = (await waitRun(run_id, null, `/api/a/runs/${run_id}`)).result;
    out.innerHTML = "";
    const tb = el("tbody");
    for (const [m, x] of Object.entries(r.mechanisms)) {
      for (const lvl of ["adverse", "critical"]) {
        const sc = x[lvl];
        tb.append(el("tr", {}, el("td", {}, MECH_TEXT[m]), el("td", {}, lvl === "adverse" ? "неблагоприятно" : "стоп-фактор"),
          el("td", { class: "num" }, sc.hits_min), el("td", { class: "num" }, sc.misses_min), el("td", { class: "num" }, sc.false_alarm_min),
          el("td", { class: "num" }, sc.pod ?? "—"), el("td", { class: "num" }, sc.far ?? "—")));
      }
    }
    const rc = r.recommended_window_check;
    const rw = rc ? S.byId[rc.window] : null;
    put(out,
      rc ? el("p", {}, el("b", {}, `Рекомендованный вариант ${rw ? hm(rw.start) : rc.window}: `),
        rc.actual_critical_min > 0 ? `на деле в нём был стоп-фактор (${rc.actual_critical_min} мин).`
          : "на деле стоп-факторов в нём не было.") : el("p", { class: "muted" }, "Рекомендации не было."),
      el("h3", {}, "Прогноз против факта (минуты после выбранного момента)"),
      el("div", { class: "tablewrap" }, el("table", { class: "tbl" },
        el("thead", {}, el("tr", {}, ...["Что", "Уровень", "Угадали, мин", "Пропустили, мин", "Ложная тревога, мин",
          "Доля угаданных", "Доля ложных"].map((h) => el("th", {}, h)))), tb)),
      el("h3", {}, "Как было на самом деле"), el("ul", {}, ...(r.actual_explanation || []).map((t) => el("li", {}, t))),
      el("p", { class: "hint" }, "ЮАА не учитывается — это геометрия орбиты, она известна заранее."));
  } catch (e) {
    out.textContent = `Не получилось: ${e.message}`;
  }
}

async function renderExport() {
  const id = S.run.run_id, box = $("tab-export");
  box.innerHTML = "";
  put(box, el("p", {}, "Сохранённый расчёт содержит запрос, момент отсечки, оценки, рекомендацию, ссылки на исходные файлы и версию алгоритма — его можно воспроизвести."),
    el("div", { class: "flags" },
      el("a", { class: "btn", href: `/runs/${id}/brief`, target: "_blank" }, "Сводка для руководителя"),
      el("a", { class: "btn", href: `/runs/${id}/report`, target: "_blank" }, "Полный отчёт"),
      el("a", { class: "btn primary", href: `/runs/${id}/export.zip` }, "ZIP: отчёт + данные"),
      el("a", { class: "btn", href: `/api/a/runs/${id}`, target: "_blank" }, "JSON")),
    el("h3", {}, "Строка статуса (для экипажа или табло)"), el("pre", { class: "mono", id: "statusLine" }, "…"),
    el("div", { class: "kv" }, el("b", {}, "Номер расчёта"), el("code", {}, id), el("b", {}, "Версия алгоритма"), S.run.algorithm_version || "—"));
  try { $("statusLine").textContent = await fetch(`/runs/${id}/status-line`).then((r) => r.text()); } catch { /* необязательно */ }
}

// ---------- предупреждения ----------
async function createWatch(id) {
  const r = S.res, w = S.byId[id || S.sel];
  if (!w) return;
  const body = { mode: r.mode === "now" ? "now" : "replay", window_start: w.start, duration_min: r.search.duration_min,
    eva: { return_to_airlock_min: r.search.return_to_airlock_min, overrun_margin_min: r.search.overrun_margin_min },
    label: `выход ${dm(w.start)}`, channels: ["web", "telegram"] };
  if (body.mode === "replay") {
    const start = Date.parse(w.start) - 6 * 3600e3;
    body.as_of = new Date(Math.min(start, Date.parse(r.as_of))).toISOString().slice(0, 19) + "Z";
    body.sim_step_min = 30;
  }
  try {
    await api("/api/a/watches", { method: "POST", body: JSON.stringify(body) });
    toast(body.mode === "now" ? `Слежу за выходом ${dm(w.start)}. Если станет хуже — придёт предупреждение.`
      : `Прокручиваю тот день с ${dm(body.as_of)} в ускоренном времени (30 мин за ~5 с). Предупреждения появятся справа.`);
    ensureNotifyPermission();
    openAlerts();
  } catch (e) { toast(e.message, "warning"); }
}

function ensureNotifyPermission() {
  if ("Notification" in window && Notification.permission === "default") Notification.requestPermission();
}

function onAlert(a) {
  S.alerts.push(a);
  if (a.severity !== "info") S.unread += 1;
  updateBell();
  toast(`${a.severity === "critical" ? "СТОП-ФАКТОР · " : ""}${a.message}`, a.severity, a.severity === "critical" ? 15000 : 8000);
  if ("Notification" in window && Notification.permission === "granted" && a.severity !== "info") {
    try { new Notification("ВКД · предупреждение", { body: a.message.slice(0, 200) }); } catch { /* нет поддержки */ }
  }
  if (S.drawerMode === "alerts") renderAlerts();
}

function updateBell() {
  $("bellCount").textContent = String(S.unread);
  $("bell").classList.toggle("has", S.unread > 0);
}

async function openAlerts() {
  S.drawerMode = "alerts";
  S.unread = 0;
  updateBell();
  $("drawerTitle").textContent = "Предупреждения";
  openDrawer();
  await renderAlerts();
}

async function renderAlerts() {
  const body = $("drawerBody");
  let watches = [];
  try { watches = await api("/api/a/watches?limit=10"); } catch { /* покажем пусто */ }
  const labels = Object.fromEntries(watches.map((w) => [w.watch_id, w.label || w.watch_id]));
  body.innerHTML = "";
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  body.append(el("p", { class: "hint" }, "Система следит за выбранным временем выхода и присылает сообщение, когда обстановка меняется: " +
    "появился стоп-фактор, стало хуже, пропали данные, стало лучше. Сообщения приходят сюда, в уведомления браузера" +
    (perm === "granted" ? " (включены)" : "") + " и в Telegram, если он настроен."));
  if (perm === "default") body.append(el("button", { class: "btn", type: "button",
    onclick: () => Notification.requestPermission().then(renderAlerts) }, "Включить уведомления браузера"));
  body.append(el("h3", {}, "За чем слежу"));
  if (!watches.length) body.append(el("p", { class: "muted" }, "Пока ни за чем. Нажмите «Следить…» или «Показать предупреждения…» под ответом."));
  watches.forEach((w) => body.append(el("div", { class: "ev" },
    el("div", { class: "h" }, el("b", {}, w.label || w.watch_id),
      el("span", { class: "flag" }, w.mode === "replay" ? "прокрутка прошлого дня" : "в реальном времени"),
      el("span", { class: `flag ${w.status === "active" ? "" : "warn"}` }, { active: "идёт", finished: "закончено", expired: "окно прошло", stopped: "остановлено" }[w.status] || w.status),
      w.status === "active" ? el("button", { class: "btn", type: "button", onclick: async () => { await api(`/api/a/watches/${w.watch_id}/stop`, { method: "POST" }); renderAlerts(); } }, "остановить") : null),
    el("div", { class: "muted" }, `Выход ${dm(w.window_start)}–${hm(w.window_end)} UTC` +
      (w.mode === "replay" ? ` · время в прокрутке: ${dm(w.sim_time)}` : ` · последняя проверка ${dm(w.last_check_at)}`) + ` · сообщений ${w.n_alerts || 0}`),
    w.error ? el("div", { class: "err" }, w.error) : null)));
  body.append(el("h3", {}, "Сообщения"));
  const list = [...S.alerts].reverse().slice(0, 60);
  if (!list.length) body.append(el("p", { class: "muted" }, "Сообщений пока нет."));
  list.forEach((a) => body.append(el("div", { class: `alert ${a.severity} ${a.ack_at ? "acked" : ""}` },
    el("div", { class: "meta" }, `${labels[a.watch_id] || a.watch_id} · на момент ${dm(a.as_of)} UTC`),
    el("div", {}, a.message),
    !a.ack_at && a.severity !== "info" ? el("button", { class: "btn", type: "button", onclick: async () => {
      await api(`/api/a/alerts/${a.id}/ack`, { method: "POST" }); a.ack_at = new Date().toISOString(); renderAlerts(); } }, "принято") : null)));
}

function connectAlerts() {
  const es = new EventSource("/api/alerts/stream");
  es.addEventListener("alert", (e) => onAlert(JSON.parse(e.data)));
}

async function loadRecentAlerts() {
  try { S.alerts = (await api("/api/a/alerts?limit=200")).slice(-100); } catch { /* сервис может ещё стартовать */ }
}

// ---------- помощь ----------
function openHelp() {
  S.drawerMode = "help";
  $("drawerTitle").textContent = "Как пользоваться";
  const b = $("drawerBody");
  b.innerHTML = `<div class="help">
  <p>Консоль отвечает на вопрос: <b>когда лучше начать выход в открытый космос и почему</b>.</p>
  <h4>Три шага</h4>
  <ol>
    <li><b>Момент.</b> «Сейчас» — живые данные. «Прошлая дата» — реальный день 2024 года: система считает так, будто
      сейчас этот момент, и видит только то, что тогда было известно. Так можно проверить, не ошибается ли она.</li>
    <li><b>Длительность выхода.</b></li>
    <li><b>«Найти лучшее время».</b> Система перебирает варианты начала каждые 15 минут в ближайшие сутки.</li>
  </ol>
  <h4>Как читать ответ</h4>
  <ul>
    <li>Крупно — лучшее время начала и почему, по двум видам опасности: <b>частицы от Солнца</b> и <b>мусор и метеороиды</b>.
      Если хороших вариантов нет, так и написано — и показан наименее неблагоприятный.</li>
    <li>Полоса «Все варианты начала»: каждый квадратик — вариант начала. Зелёный — хороший (★ — лучший),
      оранжевый — хуже, красный — не рекомендуется (есть стоп-фактор), серый — нет данных. Нажмите на квадратик — узнаете почему.</li>
    <li>«Надёжность прогноза: низкая» — дальше 6 часов известна только суточная вероятность, пересчитайте ближе к делу.</li>
    <li>«Почему так? Откуда данные» — какие измерения и прогнозы использованы, когда опубликованы, ссылка на исходный файл.</li>
  </ul>
  <h4>Как выбирается лучший вариант</h4>
  <ol>
    <li>В варианте есть стоп-фактор (солнечная буря S3 и выше, частицы долетают до станции, опасное сближение) — он не рекомендуется.</li>
    <li>На части варианта нет данных — «нужно проверить»: нехватка данных не считается хорошей новостью.</li>
    <li>Из остальных лучше тот, где меньше минут нежелательных условий: сначала по частицам от Солнца, затем по мусору и метеороидам.
      Если один вариант лучше по одному, а другой по другому, решает приоритет: радиация важнее.</li>
    <li>При равенстве — меньше минут, где поток частиц не оценён числом, меньше частиц за выход, меньше минут метеорных
      потоков (это статистика, поэтому она идёт последней), больше запас, если работа затянется.</li>
    <li>Разница меньше 5 минут (и меньше 20 % потока) — варианты равноценны.</li>
  </ol>
  <h4>От чего зависит надёжность</h4>
  <ul>
    <li><b>высокая</b> — свежее измерение, не старше 15 минут;</li>
    <li><b>средняя</b> — наш прогноз на ближайшие 3 часа, действующее предупреждение SWPC, сближения в ближайшие 12 часов
      (по проверке на 2024 г. такие подтверждаются в 9 случаях из 10);</li>
    <li><b>низкая</b> — прогноз на 3–6 часов, только суточная вероятность, сближения дальше 12 часов (с новыми данными
      появляется примерно каждое четвёртое), статистика метеорных потоков, магнитная буря неизвестна и принята с запасом.</li>
  </ul>
  <p>Точная причина — в подсказке на графике и в «Откуда данные».</p>
  <h4>Цвета</h4>
  <div class="legend big"><span><i class="c-acceptable"></i>без замечаний</span><span><i class="c-undesirable"></i>нежелательно</span>
    <span><i class="c-critical"></i>стоп-фактор — выход не рекомендуется</span><span><i class="c-no_data"></i>нет данных — оценить нельзя</span></div>
  <h4>Предупреждения</h4>
  <p>«Следить…» — система будет пересчитывать выбранное время и пришлёт сообщение, если станет хуже.
    Для прошлой даты — «Показать предупреждения, как в тот день»: день прокручивается ускоренно, и видно, когда пришло бы предупреждение.</p>
  <h4>Подробности</h4>
  <p>Кнопка под полосой: графики обстановки, таблица всех вариантов, карта пути станции, источники данных,
    «Что было на самом деле» (для прошлой даты) и сохранение отчёта.</p>
  <h4>Словарь</h4>
  <ul>
    <li><b>Частицы от Солнца</b> — после вспышек Солнце выбрасывает протоны. Их меряет спутник GOES далеко от Земли;
      до станции долетает только часть — магнитное поле Земли защищает, лучше у экватора, хуже у полюсов.</li>
    <li><b>Магнитная буря (Kp)</b> — ослабляет защиту, частицы проникают ближе к экватору.</li>
    <li><b>ЮАА</b> — зона над Южной Атлантикой, где магнитное поле слабее и радиация выше. Станция пролетает её несколько раз в сутки.</li>
    <li><b>Мусор и метеороиды</b> — отслеживаемые объекты (спутники, обломки крупнее ~10 см) проверяются на проход через зону
      контроля станции; мелкие частицы, которых не видно поштучно, учитываются статистически по прогнозу метеорных потоков NASA.</li>
    <li><b>Почему сближение — стоп-фактор</b> — на орбите относительная скорость доходит до 15 км/с: 1 г вещества несёт около
      100 кДж, как 25 г тротила. Поэтому размер объекта и вероятность попадания не взвешиваются.</li>
    <li><b>UTC</b> — всемирное время (Москва = UTC + 3).</li>
  </ul>
  <p class="muted">Это оценка внешних условий, а не доза облучения. Решение о выходе принимают специалисты.</p>
  </div>`;
  openDrawer();
}

// ---------- история ----------
async function refreshRuns() {
  let list = [];
  try { list = await api("/api/a/runs?limit=10"); } catch { return; }
  const ul = $("runsList");
  ul.innerHTML = "";
  list.filter((r) => r.kind === "assessment").forEach((r) => {
    const q = r.request || {};
    ul.append(el("li", { onclick: () => loadRun(r.run_id).catch((e) => toast(e.message, "warning")) },
      `${q.mode === "now" ? "Сейчас" : `${dmy(q.as_of)} ${hm(q.as_of)}`} · ${Math.floor(q.duration_min / 60)} ч ${q.duration_min % 60} мин`,
      el("span", { class: "s" }, r.status === "done" ? "" : r.status)));
  });
}

// ---------- запуск ----------
function init() {
  setMode("history");
  buildOverrides();
  $("asOf").value = "2024-06-08T03:00";
  document.querySelectorAll("#modeSeg button").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  document.querySelectorAll("#examples button").forEach((b) => b.addEventListener("click", () => {
    const p = PRESETS[b.dataset.preset];
    setMode("history");
    $("asOf").value = p.asOf; $("reviewChk").checked = p.review; $("planned").value = p.planned;
    $("earliest").value = ""; $("latest").value = ""; $("durH").value = 6; $("durM").value = 30;
    submitRun(buildRequest());
  }));
  $("form").addEventListener("submit", (e) => {
    e.preventDefault();
    try { submitRun(buildRequest()); } catch (err) { $("formErr").textContent = err.message; }
  });
  $("detailsBtn").addEventListener("click", () => toggleDetails());
  $("hideBad").addEventListener("change", () => { renderWindows(); selectWindow(S.sel); });
  $("durBtn").addEventListener("click", () => {
    const d = Number($("durEdit").value);
    if (!d || !S.run) return;
    S.pendingPlanChange = true;
    $("durH").value = Math.floor(d / 60); $("durM").value = d % 60;
    submitRun({ ...S.run.request, duration_min: d });
  });
  document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("drawerClose").addEventListener("click", closeDrawer);
  $("bell").addEventListener("click", openAlerts);
  $("helpBtn").addEventListener("click", openHelp);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  const tick = () => { $("clock").textContent = new Date().toISOString().slice(11, 16) + " UTC"; };
  tick(); setInterval(tick, 10000);
  refreshChips();
  setInterval(refreshChips, 30000);
  refreshRuns();
  loadRecentAlerts().then(connectAlerts);
  const run = new URLSearchParams(location.search).get("run");
  if (run) loadRun(run).catch((e) => toast(e.message, "warning"));
}

document.addEventListener("DOMContentLoaded", init);
