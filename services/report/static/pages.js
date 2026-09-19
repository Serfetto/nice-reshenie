/* Разделы «Источники», «Оповещения», «Сохранения». Утилиты (el, api, dm, …) и состояние S — из app.js. */
"use strict";

function plural(n, forms) {
  const a = Math.abs(n) % 100, b = a % 10;
  return forms[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2];
}
const intText = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("ru-RU"));
const sizeText = (b) => (b >= 1e9 ? `${(b / 1e9).toFixed(1)} ГБ` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} МБ`
  : b >= 1e3 ? `${Math.round(b / 1e3)} КБ` : `${b} Б`);
const periodText = (s) => (!s ? "—" : s % 86400 === 0 ? `${s / 86400} сут` : s % 3600 === 0 ? `${s / 3600} ч`
  : s >= 3600 ? `${(s / 3600).toFixed(1)} ч` : `${Math.round(s / 60)} мин`);
// дата без года, если этот год; иначе с годом (годовой прогноз метеоров, старые архивы)
const whenText = (iso) => (iso && iso.slice(0, 4) !== new Date().toISOString().slice(0, 4) ? `${dmy(iso)} ${hm(iso)}` : dm(iso));
function relTime(iso) {
  if (!iso) return "";
  const s = Math.round((Date.parse(iso) - Date.now()) / 1000);
  return s >= 0 ? `через ${ageText(s)}` : `${ageText(-s)} назад`;
}

function stopPagePolling() {
  clearTimeout(S.srcTimer);
  clearTimeout(S.watchTimer);
  S.srcTimer = S.watchTimer = null;
}

// ======================= Источники =======================
const GROUPS = [
  ["radiation", "Частицы от Солнца и магнитная обстановка"],
  ["orbit", "Орбита МКС"],
  ["mmod", "Мусор и метеороиды"],
];
const SRC_STATE = { ok: "в порядке", stale: "данные устарели", no_data: "нет данных", error: "ошибка загрузки",
  paused: "заморожен", running: "обновляется…" };
const REPLAY_TEXT = {
  exact: "время выпуска известно — расчёт на прошлую дату видит только то, что вышло к тому моменту",
  reconstructed: "архив восстановлен после событий — в расчёте на прошлую дату помечается «восстановлено»",
};
const ITEM_FORMS = { swpc_alerts: ["сообщение", "сообщения", "сообщений"],
  iss_spacetrack: ["набор элементов", "набора элементов", "наборов элементов"],
  iss_celestrak: ["набор элементов", "набора элементов", "наборов элементов"],
  catalog_spacetrack: ["набор элементов", "набора элементов", "наборов элементов"] };
// выгрузка CSV: по умолчанию records; каталог Space-Track наружу не выгружается (условия использования)
const CSV_PATH = { swpc_alerts: "messages.csv?source=swpc_alerts", iss_spacetrack: "elements.csv?source=spacetrack",
  iss_celestrak: "elements.csv?source=celestrak", catalog_spacetrack: null };

S.srcPending = new Map();   // источник -> часть «обновить все»? (для итогового сообщения)
S.srcUrlsOpen = new Set();  // раскрытые списки адресов переживают перерисовку
let srcSeq = 0;

function openSourcesPage() { loadSourcesPage(); }

async function loadSourcesPage() {
  clearTimeout(S.srcTimer);
  const seq = ++srcSeq;
  let list, boot = null;
  try {
    [list, boot] = await Promise.all([api("/api/i/sources?detail=1"), api("/api/i/bootstrap").catch(() => null)]);
  } catch (e) {
    if (S.page !== "sources" || seq !== srcSeq) return;
    const box = $("srcGroups");
    box.innerHTML = "";
    box.append(el("p", { class: "err" }, `Сервис сбора данных недоступен: ${e.message}`));
    S.srcTimer = setTimeout(loadSourcesPage, 15000);
    return;
  }
  if (S.page !== "sources" || seq !== srcSeq) return;  // ушли со страницы или уже идёт более свежий запрос
  S.boot = boot;
  collectRefreshResults(list);
  renderChips(list);
  renderSourcesPage(list, boot);
  const busy = list.some((s) => s.refresh?.state === "running") || boot?.state === "running";
  S.srcTimer = setTimeout(loadSourcesPage, busy ? 2000 : 20000);
}

// итог ручного обновления: отдельное сообщение по источнику или одно общее для «Обновить все»
function collectRefreshResults(list) {
  for (const s of list) {
    if (!S.srcPending.has(s.source)) continue;
    const r = s.refresh || { state: "error", errors: ["сервис сбора перезапускался — состояние неизвестно"] };
    if (r.state === "running") continue;
    const bulk = S.srcPending.get(s.source);
    S.srcPending.delete(s.source);
    if (bulk && S.bulk) S.bulk.results[s.source] = { ...r, title: s.title || s.source };
    else if (r.state === "error") toast(`${s.title || s.source}: обновить не удалось — ${(r.errors || [])[0] || "ошибка"}`, "warning", 12000);
    else toast(`${s.title || s.source}: обновлено, ${r.new_items ? `новых записей ${intText(r.new_items)}` : "новых данных нет"}`);
  }
  if (S.bulk && S.bulk.names.every((n) => S.bulk.results[n])) {
    const res = Object.values(S.bulk.results);
    const bad = res.filter((r) => r.state === "error"), fresh = res.filter((r) => r.state !== "error" && r.new_items);
    toast(`Обновлено источников: ${res.length - bad.length} из ${res.length}. ` +
      (fresh.length ? `Новые данные: ${fresh.map((r) => r.title).join(", ")}.` : "Новых данных нет.") +
      (bad.length ? ` Ошибки: ${bad.map((r) => r.title).join(", ")}.` : ""), bad.length ? "warning" : "info", 12000);
    S.bulk = null;
  }
}

function renderSourcesPage(list, boot) {
  const running = list.filter((s) => s.refresh?.state === "running");
  const bad = list.filter((s) => s.status !== "ok");
  const stats = $("srcSummary");
  stats.innerHTML = "";
  put(stats,
    el("div", { class: "stat" }, el("b", {}, list.length), el("span", {}, plural(list.length, ["источник", "источника", "источников"]))),
    el("div", { class: "stat ok" }, el("b", {}, list.length - bad.length), el("span", {}, "в порядке")),
    el("div", { class: `stat ${bad.length ? "warn" : ""}` }, el("b", {}, bad.length), el("span", {}, "с замечаниями")),
    el("div", { class: `stat ${running.length ? "run" : ""}` }, el("b", {}, running.length), el("span", {}, "обновляются сейчас")));

  const btn = $("refreshAllBtn");
  if (S.bulk) {
    const done = Object.keys(S.bulk.results).length;
    btn.textContent = `Обновляю… ${done} из ${S.bulk.names.length}`;
    btn.disabled = true;
  } else {
    btn.textContent = "Обновить все";
    btn.disabled = false;
  }

  renderBootBox(boot);

  const box = $("srcGroups");
  box.innerHTML = "";
  for (const [g, title] of GROUPS) {
    const items = list.filter((s) => (s.group || "radiation") === g);
    if (!items.length) continue;
    box.append(el("h2", { class: "group-h" }, title), el("div", { class: "src-grid" }, ...items.map(sourceCard)));
  }
}

function renderBootBox(boot) {
  const box = $("bootBox");
  box.innerHTML = "";
  if (!boot || boot.state === "idle") return;
  const ranges = (boot.ranges || []).map((r) => `${dmy(`${r.from}T`)}–${dmy(`${r.to}T`)}`).join(", ");
  const errs = Object.entries(boot.sources || {}).filter(([, x]) => x.errors?.length);
  const text = boot.state === "running"
    ? `Догружается архив для расчётов на прошлые даты: ${boot.done_days ?? 0} из ${boot.total_days ?? "…"} дней-источников` +
      (boot.current ? ` (сейчас: ${boot.current})` : "") + ". Расчёты на ещё не загруженные дни покажут «нет данных»."
    : boot.state === "errors" ? `Архив загружен с ошибками (${errs.length} ${plural(errs.length, ["источник", "источника", "источников"])}) — повтор при следующей проверке.`
      : `Архив для расчётов на прошлые даты загружен${ranges ? `: ${ranges}` : ""}.`;
  box.append(el("div", { class: `card boot ${boot.state}` },
    el("div", { class: "src-h" }, el("span", {}, text),
      boot.state !== "running" ? el("button", { type: "button", class: "btn", onclick: rerunBootstrap }, "Проверить архив ещё раз") : null),
    errs.length ? el("details", {}, el("summary", {}, "Ошибки загрузки архива"),
      el("ul", { class: "small" }, ...errs.map(([n, x]) => el("li", {}, `${n}: ${x.errors.slice(0, 3).join("; ")}`)))) : null));
}

async function rerunBootstrap() {
  try { await api("/api/i/bootstrap", { method: "POST" }); toast("Проверяю архив: недостающие дни будут догружены."); }
  catch (e) { toast(e.message, "warning"); }
  loadSourcesPage();
}

function sourceCard(s) {
  const r = s.refresh || {};
  const running = r.state === "running";
  const st = running ? "running" : s.status;
  const stt = s.stats || {};
  const csv = s.source in CSV_PATH ? CSV_PATH[s.source] : `records.csv?source=${s.source}`;
  const items = stt.items !== undefined
    ? `${intText(stt.items)} ${plural(stt.items, ITEM_FORMS[s.source] || ["значение", "значения", "значений"])}` : "ничего";
  const saved = `${items}` + (stt.raw_files ? ` · ${intText(stt.raw_files)} ${plural(stt.raw_files, ["файл", "файла", "файлов"])}, ${sizeText(stt.raw_bytes)}` : "");
  const next = s.paused ? `заморожен${s.paused_until ? ` — снимется сам в ${dm(s.paused_until)} UTC` : ""}`
    : s.next_run ? `${dm(s.next_run)} UTC · ${relTime(s.next_run)}` : "по расписанию не опрашивается";
  const urls = s.urls || [];
  return el("div", { class: `card src st-${st}` },
    el("div", { class: "src-h" },
      el("div", {}, el("h3", {}, s.title || s.source),
        el("div", { class: "muted small" }, s.provider ? `${s.provider} · ` : "", el("code", {}, s.source))),
      el("span", { class: `chip ${st}` }, SRC_STATE[st] || st)),
    s.what ? el("p", { class: "src-what" }, s.what) : null,
    s.used_for ? el("p", { class: "src-use" }, el("b", {}, "Зачем: "), s.used_for) : null,
    el("div", { class: "kv" },
      el("b", {}, "Последние данные"), s.latest_data ? `${whenText(s.latest_data)} UTC · ${relTime(s.latest_data)}` : "нет",
      el("b", {}, s.event_driven ? "Лента проверена" : "Загружено"),
      s.last_success ? `${dm(s.last_success)} UTC · ${relTime(s.last_success)}` : "ещё не загружался",
      el("b", {}, "Следующий опрос"), next,
      el("b", {}, "Расписание"), `раз в ${periodText(s.interval_s)}` +
        (s.issue_times_utc ? ` · выпуски в ${s.issue_times_utc.join(", ")} UTC` : "") +
        (s.max_age_s ? ` · старше ${periodText(s.max_age_s)} — устарели` : ""),
      el("b", {}, "Сохранено"), saved,
      el("b", {}, "Прошлые даты"), REPLAY_TEXT[s.replay] || "—"),
    s.status === "error" && s.last_error ? el("div", { class: "err small src-err" },
      `Ошибка${s.consecutive_failures > 1 ? ` (${s.consecutive_failures} раза подряд)` : ""}: ${s.last_error}`) : null,
    urls.length ? el("details", { class: "src-urls", open: S.srcUrlsOpen.has(s.source) ? "" : null,
      ontoggle: (e) => (e.target.open ? S.srcUrlsOpen.add(s.source) : S.srcUrlsOpen.delete(s.source)) },
    el("summary", {}, `Откуда скачиваем (${urls.length})`),
    el("ul", {}, ...urls.map((u) => el("li", {}, el("span", { class: "muted" }, `${u.title}: `),
      u.url.includes("{") || u.url.includes("space-track.org") || u.url.startsWith("ftp:")
        ? el("code", {}, u.url) : el("a", { href: u.url, target: "_blank", rel: "noopener" }, u.url))))) : null,
    el("div", { class: "src-actions" },
      el("button", { type: "button", class: "btn primary", disabled: running, onclick: () => refreshSource(s) },
        running ? "Обновляется…" : "Обновить"),
      s.paused
        ? el("button", { type: "button", class: "btn", onclick: () => pauseSource(s, "resume") }, "Возобновить")
        : el("button", { type: "button", class: "btn", title: "Остановить обновление для всех (не дольше 2 ч) — проверка поведения при сбое",
          onclick: () => pauseSource(s, "pause") }, "Заморозить"),
      csv ? el("a", { class: "btn", href: `/api/i/export/${csv}`, title: "Все разобранные данные источника" }, "Скачать CSV") : null,
      el("span", { class: "muted small" }, refreshText(r))));
}

function refreshText(r) {
  if (!r.state) return "";
  if (r.state === "running") return `запущено в ${hm(r.started_at)} UTC`;
  if (r.state === "error") return `вручную в ${hm(r.finished_at)}: ошибка — ${(r.errors || [])[0] || ""}`.slice(0, 140);
  return `вручную в ${hm(r.finished_at)} UTC: ${r.new_items ? `+${intText(r.new_items)}` : "новых данных нет"}`;
}

async function refreshSource(s) {
  try {
    const x = await api(`/api/i/sources/${s.source}/refresh`, { method: "POST" });
    if (x.already_running) toast(`${s.title || s.source}: уже обновляется`);
    S.srcPending.set(s.source, false);
  } catch (e) { toast(e.message, "warning"); }
  loadSourcesPage();
}

async function refreshAll() {
  const btn = $("refreshAllBtn");
  btn.disabled = true;
  try {
    const x = await api("/api/i/sources/refresh-all", { method: "POST" });
    const names = [...x.started, ...x.already_running];
    names.forEach((n) => S.srcPending.set(n, true));
    S.bulk = names.length ? { names, results: {} } : null;
    toast(`Обновляю ${names.length} ${plural(names.length, ["источник", "источника", "источников"])}` +
      (x.skipped_paused.length ? `. Заморожены и пропущены: ${x.skipped_paused.join(", ")}` : ""));
  } catch (e) {
    toast(e.message, "warning");
    btn.disabled = false;
  }
  loadSourcesPage();
}

async function pauseSource(s, what) {
  if (what === "pause" && !confirm(`Заморозить «${s.title || s.source}»?\n\nОбновление остановится для всех пользователей сервиса ` +
    "(не дольше 2 ч). Чтобы проверить сбой только в своём расчёте, используйте «Дополнительно» → «Проверка сбоя» в форме расчёта.")) return;
  try { await api(`/api/i/sources/${s.source}/${what}`, { method: "POST" }); }
  catch (e) { toast(e.message, "warning"); }
  loadSourcesPage();
}

// ======================= Оповещения =======================
const SEV_TEXT = { critical: "СТОП-ФАКТОР", warning: "ПРЕДУПРЕЖДЕНИЕ", info: "ИНФО" };
const WATCH_STATUS = { active: "идёт", finished: "закончено", expired: "окно прошло", stopped: "остановлено" };
S.alertFilter = { sev: "", unacked: false, watch: null };
S.watches = [];

async function openAlertsPage() {
  if (S.alertWatch) { S.alertFilter.watch = S.alertWatch; S.alertWatch = null; }
  renderAlertsPage();
  await loadRecentAlerts();
  pollWatches();
}

async function pollWatches() {
  clearTimeout(S.watchTimer);
  try { S.watches = await api("/api/a/watches?limit=50"); } catch { /* сервис оценки может перезапускаться */ }
  if (S.page !== "alerts") return;
  renderAlertsPage();
  S.watchTimer = setTimeout(pollWatches, S.watches.some((w) => w.status === "active") ? 4000 : 30000);
}

function renderAlertsPage() {
  const f = S.alertFilter, watches = S.watches;
  const labels = Object.fromEntries(watches.map((w) => [w.watch_id, w.label || w.watch_id]));
  const open = S.alerts.filter((a) => !a.ack_at && a.severity !== "info");
  const crit = open.filter((a) => a.severity === "critical").length;
  const active = watches.filter((w) => w.status === "active").length;

  const stats = $("alertStats");
  stats.innerHTML = "";
  put(stats,
    el("div", { class: `stat ${active ? "run" : ""}` }, el("b", {}, active), el("span", {}, "идёт отслеживаний")),
    el("div", { class: `stat ${crit ? "crit" : ""}` }, el("b", {}, crit), el("span", {}, "непринятых стоп-факторов")),
    el("div", { class: `stat ${open.length - crit ? "warn" : ""}` }, el("b", {}, open.length - crit), el("span", {}, "непринятых предупреждений")),
    el("div", { class: "stat" }, el("b", {}, S.alerts.length), el("span", {}, "сообщений всего")));

  const perm = "Notification" in window ? Notification.permission : "unsupported";
  $("notifBtn").classList.toggle("hidden", perm !== "default");
  const ack = $("ackAllBtn");
  ack.disabled = !open.length;
  ack.textContent = f.watch ? "Принять все по этому выходу" : "Принять все";

  const wl = $("watchList");
  wl.innerHTML = "";
  if (!watches.length) {
    wl.append(el("div", { class: "card empty-card" },
      el("p", {}, "Пока ни за чем не слежу."),
      el("p", { class: "muted small" }, "Откройте расчёт и нажмите «Следить…» (для «Сейчас») или «Показать предупреждения, " +
        "как в тот день» (для прошлой даты — день прокрутится ускоренно)."),
      el("a", { class: "btn", href: "#calc" }, "К расчёту")));
  }
  watches.forEach((w) => wl.append(watchCard(w)));

  const wf = $("watchFilter");
  wf.innerHTML = "";
  if (f.watch) wf.append(el("div", { class: "flags filter-chip" }, el("span", { class: "flag" }, `Только: ${labels[f.watch] || f.watch}`),
    el("button", { type: "button", class: "linkbtn", onclick: () => { f.watch = null; renderAlertsPage(); } }, "показать все")));

  let list = [...S.alerts].reverse();
  if (f.sev) list = list.filter((a) => a.severity === f.sev);
  if (f.unacked) list = list.filter((a) => !a.ack_at && a.severity !== "info");
  if (f.watch) list = list.filter((a) => a.watch_id === f.watch);
  const feed = $("alertFeed");
  feed.innerHTML = "";
  if (!list.length) {
    feed.append(el("p", { class: "muted" }, S.alerts.length ? "Под выбранный фильтр сообщений нет." : "Сообщений пока нет."));
    return;
  }
  list.slice(0, 200).forEach((a) => feed.append(alertItem(a, labels)));
}

function watchCard(w) {
  const snap = w.last_snapshot;
  const sel = S.alertFilter.watch === w.watch_id;
  return el("div", { class: `card watch ${sel ? "sel" : ""}` },
    el("div", { class: "src-h" },
      el("div", {}, el("h3", {}, w.label || w.watch_id),
        el("div", { class: "muted small" }, `Выход ${dm(w.window_start)}–${hm(w.window_end)} UTC`)),
      el("span", { class: `chip ${w.status === "active" ? "running" : "never"}` }, WATCH_STATUS[w.status] || w.status)),
    el("div", { class: "flags" },
      el("span", { class: "flag" }, w.mode === "replay" ? "прокрутка прошлого дня" : "в реальном времени"),
      w.mode === "replay" ? el("span", { class: "flag" }, `часы прокрутки: ${dm(w.sim_time)} UTC`)
        : el("span", { class: "flag" }, `проверено ${w.last_check_at ? relTime(w.last_check_at) : "—"}`)),
    snap ? el("ul", { class: "mini" }, ...Object.entries(snap.mechanisms).map(([m, st]) =>
      el("li", {}, el("i", { style: `background:${CLASS_COLOR[st.worst]}` }), `${MECH_TEXT[m] || m}: ${CLASS_TEXT[st.worst] || st.worst}`)))
      : el("p", { class: "muted small" }, "ещё не проверялось"),
    w.error ? el("div", { class: "err small" }, w.error) : null,
    el("div", { class: "src-actions" },
      el("button", { type: "button", class: `btn ${sel ? "primary" : ""}`,
        onclick: () => { S.alertFilter.watch = sel ? null : w.watch_id; renderAlertsPage(); } },
      sel ? "показаны только его сообщения" : `Сообщения (${w.n_alerts || 0})`),
      w.status === "active" ? el("button", { type: "button", class: "btn", onclick: async () => {
        try { await api(`/api/a/watches/${w.watch_id}/stop`, { method: "POST" }); } catch (e) { toast(e.message, "warning"); }
        pollWatches();
      } }, "Остановить") : null));
}

function alertItem(a, labels) {
  const tg = a.delivered?.telegram;
  return el("div", { class: `alert ${a.severity} ${a.ack_at ? "acked" : ""}` },
    el("div", { class: "meta" }, el("b", { class: `sev ${a.severity}` }, SEV_TEXT[a.severity] || a.severity),
      ` · ${labels[a.watch_id] || a.watch_id} · обстановка на ${dm(a.as_of)} UTC` +
      (a.data_lag_s !== null && a.data_lag_s !== undefined ? ` · данные давностью ${ageText(a.data_lag_s)}` : "")),
    el("div", {}, a.message),
    el("div", { class: "meta foot" }, `получено ${dm(a.created_at)} UTC` + (tg ? ` · Telegram: ${tg}` : ""),
      !a.ack_at && a.severity !== "info"
        ? el("button", { type: "button", class: "btn", onclick: () => ackAlert(a) }, "Принято")
        : a.ack_at ? el("span", {}, ` · принято ${dm(a.ack_at)} UTC`) : null));
}

async function ackAlert(a) {
  try {
    await api(`/api/a/alerts/${a.id}/ack`, { method: "POST" });
    a.ack_at = new Date().toISOString();
  } catch (e) { toast(e.message, "warning"); }
  updateBell();
  renderAlertsPage();
}

async function ackAll() {
  const w = S.alertFilter.watch;
  try {
    const x = await api(`/api/a/alerts/ack-all${w ? `?watch_id=${encodeURIComponent(w)}` : ""}`, { method: "POST" });
    toast(`Отмечено принятыми: ${x.acknowledged}`);
  } catch (e) { toast(e.message, "warning"); }
  await loadRecentAlerts();
  renderAlertsPage();
}

// ======================= Сохранения =======================
const SAVED_PAGE = 40;
S.saved = { mode: "", q: "", rows: [], more: false, edit: null };

function openSavedPage() { loadSaved(false); }

async function loadSaved(append) {
  const f = S.saved;
  const params = new URLSearchParams({ kind: "assessment", limit: SAVED_PAGE + 1, offset: append ? f.rows.length : 0 });
  if (f.mode) params.set("mode", f.mode);
  let rows;
  try { rows = await api(`/api/a/runs?${params}`); } catch (e) {
    const t = $("savedTable");
    t.innerHTML = "";
    t.append(el("tbody", {}, el("tr", {}, el("td", { class: "err" }, `Сервис оценки недоступен: ${e.message}`))));
    return;
  }
  f.more = rows.length > SAVED_PAGE;
  rows = rows.slice(0, SAVED_PAGE);
  f.rows = append ? f.rows.concat(rows) : rows;
  renderSaved();
}

function savedHaystack(r) {
  const q = r.request || {};
  return [r.label, r.run_id, runMoment(q), dm(q.as_of), dm(r.created_at), MODE_TEXT[q.mode]].join(" ").toLowerCase();
}

function renderSaved() {
  const f = S.saved, q = f.q.trim().toLowerCase();
  const rows = q ? f.rows.filter((r) => savedHaystack(r).includes(q)) : f.rows;
  $("savedCount").textContent = q ? `найдено ${rows.length} из ${f.rows.length}${f.more ? " загруженных" : ""}`
    : `${f.rows.length}${f.more ? "+" : ""} ${plural(f.rows.length, ["расчёт", "расчёта", "расчётов"])}`;
  const t = $("savedTable");
  t.innerHTML = "";
  t.append(el("thead", {}, el("tr", {}, ...["Расчёт", "На какой момент", "Выход", "Ответ", "Надёжность", "Сверка с фактом", ""]
    .map((h) => el("th", {}, h)))));
  const tb = el("tbody");
  if (!rows.length) tb.append(el("tr", {}, el("td", { colspan: 7, class: "muted" },
    f.rows.length ? "Ничего не найдено." : "Расчётов пока нет — сделайте первый в разделе «Расчёт».")));
  rows.forEach((r) => tb.append(savedRow(r)));
  t.append(tb);
  $("savedMore").classList.toggle("hidden", !f.more);
}

function savedRow(r) {
  const q = r.request || {}, s = r.summary, done = r.status === "done";
  const editing = S.saved.edit === r.run_id;
  let name;
  if (editing) {
    const inp = el("input", { type: "text", value: r.label || "", maxlength: 200, placeholder: "например: ВКД-1, план на 08.06" });
    const save = () => saveLabel(r, inp.value);
    inp.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") { S.saved.edit = null; renderSaved(); } });
    setTimeout(() => inp.focus(), 0);
    name = el("div", { class: "edit" }, inp, el("button", { type: "button", class: "btn primary", onclick: save }, "Сохранить"),
      el("button", { type: "button", class: "btn", onclick: () => { S.saved.edit = null; renderSaved(); } }, "Отмена"));
  } else {
    name = [r.label ? el("b", {}, r.label) : el("span", { class: "muted" }, "без названия"), el("br"),
      el("span", { class: "muted small" }, `${dm(r.created_at)} UTC · ${r.run_id}`)];
  }
  let answer;
  if (r.status === "failed") answer = el("span", { class: "err small" }, (r.error || "ошибка расчёта").slice(0, 120));
  else if (!done) answer = el("span", { class: "muted" }, "идёт расчёт…");
  else if (!s) answer = "—";
  else if (s.best) {
    answer = [el("span", { class: `pill ${s.adverse ? "worse" : "preferred"}` }, s.adverse ? "наименее неблагоприятный" : "лучшее время"),
      ` ${dm(s.best.start)}–${hm(s.best.end)} UTC`];
    if (s.planned && s.planned.id !== s.best.id) {
      answer.push(el("br"), el("span", { class: "muted small" }, `план ${hm(s.planned.start)} — ${STATUS_TEXT[s.planned.status] || s.planned.status}`));
    }
  } else if (s.rec_status === "insufficient_basis") answer = el("span", { class: "pill insufficient_basis" }, "нельзя оценить — не хватает данных");
  else answer = el("span", { class: "pill no_window" }, "подходящего времени нет");
  const moment = q.mode === "now" ? `Сейчас · ${dm(s?.as_of || r.created_at)} UTC` : `${dmy(q.as_of)} ${hm(q.as_of)} UTC`;
  return el("tr", { class: S.run?.run_id === r.run_id ? "sel" : "" },
    el("td", { class: "wrap name" }, name),
    el("td", { "data-label": "Момент" }, el("span", { class: "flag" }, MODE_TEXT[q.mode] || q.mode), el("br"), moment),
    el("td", { "data-label": "Выход" }, durText(q.duration_min)),
    el("td", { class: "wrap", "data-label": "Ответ" }, answer),
    el("td", { "data-label": "Надёжность" }, done && s ? (CONF_TEXT[s.confidence] || s.confidence || "—") : "—"),
    el("td", { "data-label": "Сверка" }, r.verified ? "✓ сделана" : el("span", { class: "muted" }, q.mode === "replay" ? "нет" : "—")),
    el("td", {}, el("div", { class: "row-actions" },
      done ? el("button", { type: "button", class: "btn primary", onclick: () => openSaved(r.run_id) }, "Открыть") : null,
      r.status === "failed" ? el("button", { type: "button", class: "btn", title: "Запустить тот же запрос заново",
        onclick: () => { location.hash = "calc"; submitRun(r.request); } }, "Повторить") : null,
      done ? el("a", { class: "btn", href: `/runs/${r.run_id}/brief`, target: "_blank" }, "Сводка") : null,
      done ? el("a", { class: "btn", href: `/runs/${r.run_id}/export.zip` }, "ZIP") : null,
      el("button", { type: "button", class: "btn", onclick: () => { S.saved.edit = r.run_id; renderSaved(); } }, r.label ? "Переименовать" : "Назвать"),
      el("button", { type: "button", class: "btn danger", onclick: () => deleteSaved(r) }, "Удалить"))));
}

function openSaved(id) {
  location.hash = "calc";
  loadRun(id).catch((e) => toast(e.message, "warning"));
}

async function saveLabel(r, value) {
  try {
    const x = await api(`/api/a/runs/${r.run_id}/label`, { method: "POST", body: JSON.stringify({ label: value }) });
    r.label = x.label;
    if (S.run?.run_id === r.run_id) { S.run.label = x.label; renderRunMeta(); }
    refreshRuns();
  } catch (e) { toast(e.message, "warning"); }
  S.saved.edit = null;
  renderSaved();
}

async function deleteSaved(r) {
  const q = r.request || {};
  if (!confirm(`Удалить расчёт «${r.label || `${runMoment(q)}, ${durText(q.duration_min)}`}»?\n\nВместе с ним удаляются его сверки с фактом. Отменить нельзя.`)) return;
  try {
    await api(`/api/a/runs/${r.run_id}`, { method: "DELETE" });
    S.saved.rows = S.saved.rows.filter((x) => x.run_id !== r.run_id);
    renderSaved();
    refreshRuns();
    toast("Расчёт удалён");
  } catch (e) { toast(e.message, "warning"); }
}

// ======================= привязка =======================
function initPages() {
  $("refreshAllBtn").addEventListener("click", refreshAll);
  $("ackAllBtn").addEventListener("click", ackAll);
  $("notifBtn").addEventListener("click", () => Notification.requestPermission().then(renderAlertsPage));
  document.querySelectorAll("#sevSeg button").forEach((b) => b.addEventListener("click", () => {
    S.alertFilter.sev = b.dataset.sev;
    document.querySelectorAll("#sevSeg button").forEach((x) => x.classList.toggle("on", x === b));
    renderAlertsPage();
  }));
  $("unackedChk").addEventListener("change", (e) => { S.alertFilter.unacked = e.target.checked; renderAlertsPage(); });
  document.querySelectorAll("#savedSeg button").forEach((b) => b.addEventListener("click", () => {
    S.saved.mode = b.dataset.mode;
    document.querySelectorAll("#savedSeg button").forEach((x) => x.classList.toggle("on", x === b));
    loadSaved(false);
  }));
  $("savedSearch").addEventListener("input", (e) => { S.saved.q = e.target.value; renderSaved(); });
  $("savedMore").addEventListener("click", () => loadSaved(true));
}
