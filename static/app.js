const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ---------- language ----------
let lang = "en";
try { lang = localStorage.getItem("lang") || "en"; } catch (e) {}
if (!I18N[lang]) lang = "en";

function t(key, vars = {}) {
  let s = (I18N[lang] && I18N[lang][key]) ?? I18N.en[key] ?? key;
  for (const k in vars) s = s.split("{" + k + "}").join(vars[k]);
  return s;
}

let state = { user: null, result: null, cluster: null, history: null };

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-lang]").forEach(b => {
    b.classList.toggle("on", b.dataset.lang === lang);
    b.setAttribute("aria-pressed", b.dataset.lang === lang);
  });
  if (state.user) $("who").textContent = t("loggedAs", { name: state.user });
  if (state.result) renderResult(state.result);
  if (state.cluster) renderCluster(state.cluster);
  if (state.history) renderHistory(state.history);
}

document.querySelectorAll("[data-lang]").forEach(b => b.onclick = () => {
  lang = b.dataset.lang;
  try { localStorage.setItem("lang", lang); } catch (e) {}
  applyLang();
});

// ---------- api ----------
async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong.");
  return data;
}

// ---------- auth ----------
function showMain(username) {
  state.user = username;
  $("auth").classList.add("hidden");
  $("main").classList.remove("hidden");
  $("who").textContent = t("loggedAs", { name: username });
}
function showAuth() {
  state.user = null;
  $("main").classList.add("hidden");
  $("auth").classList.remove("hidden");
  $("password").value = "";
  $("authMsg").textContent = "";
}
const creds = () => ({ username: $("username").value, password: $("password").value });

$("loginBtn").onclick = async () => {
  try { showMain((await api("/api/login", "POST", creds())).username); }
  catch (e) { $("authMsg").textContent = e.message; }
};
$("signupBtn").onclick = async () => {
  try { $("authMsg").textContent = (await api("/api/signup", "POST", creds())).message; }
  catch (e) { $("authMsg").textContent = e.message; }
};
$("logoutBtn").onclick = async () => {
  await api("/api/logout", "POST");
  state.result = state.cluster = state.history = null;
  $("result").innerHTML = ""; $("clusterOut").innerHTML = ""; $("history").innerHTML = "";
  showAuth();
};
["username", "password"].forEach(id => $(id).addEventListener("keydown", e => { if (e.key === "Enter") $("loginBtn").click(); }));
$("ticker").addEventListener("keydown", e => { if (e.key === "Enter") $("predictBtn").click(); });

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach(b => b.onclick = () => {
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === b));
  ["predict", "compare", "history"].forEach(n => $("tab-" + n).classList.toggle("hidden", n !== b.dataset.tab));
  if (b.dataset.tab === "history") loadHistory();
});

// ---------- candlestick chart (SVG) ----------
const REGIME_COLOR = { calm: "#6aa9ff", trending: "#f5b942", volatile: "#ff6b6b" };

function candleSVG(r) {
  const W = 920, H = 380, L = 58, R = 26, T = 12, B = 28;
  const fc = [t("forecast"), r.last_close, r.high, r.low, r.price, null];   // forecast candle opens at last close
  const all = r.candles.concat([fc]);
  const lo = Math.min(...all.map(x => x[3])), hi = Math.max(...all.map(x => x[2]));
  const pad = (hi - lo) * 0.06 || 1, min = lo - pad, span = hi - lo + 2 * pad;
  const y = v => T + (H - T - B) * (1 - (v - min) / span);
  const step = (W - L - R) / all.length, bw = Math.max(2, step * 0.62);
  const x = i => L + step * i + step / 2;
  let g = "", cs = "";
  for (let i = 0; i <= 4; i++) {
    const v = min + span * i / 4;
    g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid"/><text x="${L - 8}" y="${y(v) + 4}" class="axis" text-anchor="end">${v.toFixed(v > 1000 ? 0 : 2)}</text>`;
  }
  all.forEach((c, i) => {
    const isF = i === all.length - 1, up = c[4] >= c[1], col = up ? "#3ddc97" : "#ff6b6b";
    if (!isF && c[5]) cs += `<rect x="${x(i) - step / 2}" y="${T}" width="${step}" height="${H - T - B}" fill="${REGIME_COLOR[c[5]]}" opacity=".10"/>`;
    const top = y(Math.max(c[1], c[4])), h = Math.max(1.5, Math.abs(y(c[1]) - y(c[4])));
    const dash = isF ? ` stroke-dasharray="3 2" fill-opacity=".25"` : "";
    cs += `<line x1="${x(i)}" x2="${x(i)}" y1="${y(c[2])}" y2="${y(c[3])}" stroke="${col}" stroke-width="1.4"${isF ? ` stroke-dasharray="3 2"` : ""}/>` +
          `<rect x="${x(i) - bw / 2}" y="${top}" width="${bw}" height="${h}" fill="${col}" stroke="${col}"${dash}/>`;
    if (!isF && i % 15 === 0) g += `<text x="${x(i)}" y="${H - 8}" class="axis" text-anchor="middle">${c[0].slice(0, 6)}</text>`;
  });
  const fx = x(all.length - 1);
  g += `<text x="${fx}" y="${H - 8}" class="axis acc" text-anchor="end">${esc(t("forecast"))}</text>`;
  return `<div class="chartbox"><svg viewBox="0 0 ${W} ${H}" id="candles" role="img" aria-label="${esc(t("chartTitle"))}">${g}${cs}` +
         `<line id="cross" y1="${T}" y2="${H - B}" class="cross hidden"/></svg><div id="tip" class="tip hidden"></div></div>` +
         `<div class="legend">${["calm", "trending", "volatile"].map(k => `<span><i style="background:${REGIME_COLOR[k]}"></i>${esc(t(k))}</span>`).join("")}` +
         `<span><i class="dash"></i>${esc(t("forecast"))}</span></div>`;
}

function wireTooltip(r) {
  const svg = $("candles"), tip = $("tip"), cross = $("cross");
  if (!svg) return;
  const all = r.candles.concat([[t("forecast"), r.last_close, r.high, r.low, r.price, null]]);
  const W = 920, L = 58, R = 26, step = (W - L - R) / all.length;
  svg.onmousemove = e => {
    const box = svg.getBoundingClientRect(), px = (e.clientX - box.left) * W / box.width;
    const i = Math.min(all.length - 1, Math.max(0, Math.floor((px - L) / step)));
    const c = all[i], cx = L + step * i + step / 2;
    cross.setAttribute("x1", cx); cross.setAttribute("x2", cx); cross.classList.remove("hidden");
    tip.innerHTML = `<b>${esc(c[0])}</b><br>${t("open")} ${c[1]}<br>${t("high")} ${c[2]}<br>${t("low")} ${c[3]}<br>${t("close")} ${c[4]}`;
    tip.classList.remove("hidden");
    const left = cx / W * box.width;
    tip.style.left = (left > box.width / 2 ? left - tip.offsetWidth - 12 : left + 12) + "px";
  };
  svg.onmouseleave = () => { tip.classList.add("hidden"); cross.classList.add("hidden"); };
}

// ---------- scatter plot (SVG) ----------
const GROUP_COLOR = { stable: "#3ddc97", balanced: "#f5b942", aggressive: "#ff6b6b" };

function scatterSVG(points, marks, xTitle, yTitle) {
  const W = 560, H = 340, L = 50, R = points.length ? 16 : 84, T = 12, B = 44;
  const all = points.concat(marks);
  const xs = all.map(p => p.x), ys = all.map(p => p.y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const px = (x1 - x0) * 0.06 || 1, py = (y1 - y0) * 0.08 || 1;
  const X = v => L + (W - L - R) * (v - (x0 - px)) / ((x1 - x0) + 2 * px);
  const Y = v => T + (H - T - B) * (1 - (v - (y0 - py)) / ((y1 - y0) + 2 * py));
  let g = "";
  for (let i = 0; i <= 4; i++) {
    const vx = x0 - px + ((x1 - x0) + 2 * px) * i / 4, vy = y0 - py + ((y1 - y0) + 2 * py) * i / 4;
    g += `<line x1="${X(vx)}" x2="${X(vx)}" y1="${T}" y2="${H - B}" class="grid"/><text x="${X(vx)}" y="${H - B + 15}" class="axis" text-anchor="middle">${vx.toFixed(1)}</text>` +
         `<line x1="${L}" x2="${W - R}" y1="${Y(vy)}" y2="${Y(vy)}" class="grid"/><text x="${L - 6}" y="${Y(vy) + 4}" class="axis" text-anchor="end">${vy.toFixed(1)}</text>`;
  }
  g += `<line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" class="zero"/>`;
  const dots = points.map(p => `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="${p.r || 2.6}" fill="${p.color}" opacity=".5"/>`).join("");
  const big = marks.map(m => `<circle cx="${X(m.x)}" cy="${Y(m.y)}" r="${m.r || 8}" fill="${m.fill || "none"}" stroke="${m.stroke || "#e8eef8"}" stroke-width="2.5"/>` +
    `<text x="${X(m.x) + (m.r || 8) + 5}" y="${Y(m.y) + 4}" class="mark">${esc(m.label)}</text>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" class="scatter" role="img">${g}${dots}${big}` +
         `<text x="${(L + W - R) / 2}" y="${H - 6}" class="axis" text-anchor="middle">${esc(xTitle)}</text>` +
         `<text transform="translate(13 ${(T + H - B) / 2}) rotate(-90)" class="axis" text-anchor="middle">${esc(yTitle)}</text></svg>`;
}

// ---------- result ----------
const sgn = v => (v > 0 ? "+" : "") + v.toFixed(2) + "%";
const REGS = ["calm", "trending", "volatile"];

function renderResult(r) {
  const up = r.pct >= 0, cur = r.currency, cs = r.regimes[r.regime];
  const sum = t("sum1", { ticker: r.ticker, date: r.next_date, cur, price: r.price.toFixed(2), pct: Math.abs(r.pct).toFixed(2), dir: t(up ? "dirGain" : "dirDrop") }) + " " +
              t("sum2", { cur, low: r.low.toFixed(2), high: r.high.toFixed(2), regime: t(r.regime), signal: t("sig_" + r.signal), conf: t("conf_" + r.confidence) }) + " " +
              t("sum3", { regime: t(r.regime), win: cs.win_rate });
  const zone = r.week52.position >= 80 ? "nearHigh" : r.week52.position <= 20 ? "nearLow" : "midRange";

  const pts = r.points.map(p => ({ x: p[0], y: p[1], color: REGIME_COLOR[REGS[p[2]]] }));
  const marks = REGS.map(n => ({ x: r.regimes[n].avg_vol, y: r.regimes[n].avg_ret5, r: 9, fill: REGIME_COLOR[n], label: t(n) }));
  const tp = r.points[r.points.length - 1];
  marks.push({ x: tp[0], y: tp[1], r: 7, stroke: "#ffffff", label: t("today") });
  const map = scatterSVG(pts, marks, t("axVol"), t("axRet"));

  const regRows = REGS.map(n => {
    const s = r.regimes[n];
    return `<div class="reg${r.regime === n ? " now" : ""}"><div class="rtop"><b><i class="dot" style="background:${REGIME_COLOR[n]}"></i>${esc(t(n))}</b><span>${esc(t("shareDays", { p: s.share }))}</span></div>` +
           `<div class="rbar"><i style="width:${s.share}%;background:${REGIME_COLOR[n]}"></i></div>` +
           `<small>${esc(t("avgVol", { v: s.avg_vol }))} · ${esc(t("winRate", { w: s.win_rate }))} · ${esc(t("avgNext", { m: s.avg_next }))}</small></div>`;
  }).join("");
  const qbars = Object.entries(r.quality).map(([k, v]) =>
    `<div class="kb${+k === 3 ? " best" : ""}"><span style="height:${Math.max(4, v * 130)}px"></span><em>${k}<br>${v}</em></div>`).join("");

  $("result").innerHTML = `
  <section class="verdict s-${r.signal}">
    <div class="vleft">
      <span class="vtick">${esc(r.ticker)}</span>
      <div class="vsig">${esc(t("sig_" + r.signal))}</div>
      <span class="vnote c-${r.confidence}">${esc(t("confNote", { conf: t("conf_" + r.confidence), regime: t(r.regime) }))}</span>
      ${r.regime === "volatile" ? `<p class="vhint">${esc(t("volNote"))}</p>` : ""}
    </div>
    <dl class="vstats">
      <div><dt>${esc(t("nextClose"))}</dt><dd class="big">${cur}${r.price.toFixed(2)}</dd><small>${esc(t("forDate", { date: r.next_date }))}</small></div>
      <div><dt>${esc(t("change"))}</dt><dd class="${up ? "g" : "r"}">${up ? "▲" : "▼"} ${sgn(r.pct)}</dd></div>
      <div><dt>${esc(t("expHigh"))}</dt><dd>${cur}${r.high.toFixed(2)}</dd></div>
      <div><dt>${esc(t("expLow"))}</dt><dd>${cur}${r.low.toFixed(2)}</dd></div>
    </dl>
  </section>

  <section class="panel summary"><h3>${esc(t("summaryTitle"))}</h3><p>${esc(sum)}</p>
    <small class="muted">${esc(t("asOf", { date: r.last_date }))} · ${esc(t("dataNote", { n: r.rows, from: r.data_from }))}</small></section>

  <section class="panel"><h3>${esc(t("chartTitle"))}</h3>${candleSVG(r)}</section>

  <section class="panel"><h3>${esc(t("regimeTitle"))}</h3><p class="muted sub1">${esc(t("regimeSub"))}</p>
    <div class="reggrid">
      <div><h4>${esc(t("mapTitle"))}</h4>${map}</div>
      <div>
        <div class="now" style="--c:${REGIME_COLOR[r.regime]}"><small>${esc(t("regimeNow"))}</small><b>${esc(t(r.regime))}</b><small>${esc(t("streak", { n: r.streak }))}</small></div>
        ${regRows}
        <h4 class="q">${esc(t("qualityTitle"))}</h4><div class="kbars">${qbars}</div>
        <p class="mini">${esc(t("qualityNote"))}</p>
      </div>
    </div>
  </section>

  <div class="grid2">
    <section class="panel"><h3>${esc(t("errTitle"))}</h3>
      <div class="kv"><span>${esc(t("mae"))}</span><b>${cur}${r.mae}</b></div>
      <div class="kv"><span>${esc(t("r2"))}</span><b>${r.r2}</b></div>
      <p class="mini">${esc(t("errNote"))}</p>
    </section>
    <section class="panel"><h3>${esc(t("rangeTitle"))}</h3>
      <div class="range"><i style="left:${r.week52.position}%"></i></div>
      <div class="mlab"><span>${cur}${r.week52.low}</span><span>${esc(t(zone))}</span><span>${cur}${r.week52.high}</span></div>
    </section>
  </div>

  <section class="panel"><h3>${esc(t("fitTitle"))}</h3><img class="fit" alt="${esc(t("fitTitle"))}" src="data:image/png;base64,${r.image}"></section>`;
  wireTooltip(r);
}

$("predictBtn").onclick = async () => {
  const btn = $("predictBtn"), st = $("status");
  btn.disabled = true; st.textContent = t("loading"); st.classList.add("busy");
  try {
    state.result = await api("/api/predict", "POST", { ticker: $("ticker").value });
    st.textContent = ""; renderResult(state.result);
  } catch (e) { st.textContent = t("errorPrefix") + e.message; }
  finally { btn.disabled = false; st.classList.remove("busy"); }
};

// ---------- compare (K-Means on tickers) ----------
function renderCluster(c) {
  const rows = c.rows.map(r => `<tr><td><b>${esc(r.ticker)}</b></td><td class="${r.ann_return >= 0 ? "g" : "r"}">${r.ann_return}%</td>` +
    `<td>${r.ann_vol}%</td><td class="r">${r.max_dd}%</td><td><span class="chip grp-${r.group}">${esc(t(r.group))}</span></td></tr>`).join("");
  const marks = c.rows.map(r => ({ x: r.ann_vol, y: r.ann_return, r: 7, fill: GROUP_COLOR[r.group], stroke: "#0e1726", label: r.ticker }));
  $("clusterOut").innerHTML = `<div class="reggrid"><div><h4>${esc(t("groupMap"))}</h4>${scatterSVG([], marks, t("annVol") + " (%)", t("annReturn") + " (%)")}</div>` +
    `<div class="scroll"><table class="tbl"><tr><th>${esc(t("ticker"))}</th><th>${esc(t("annReturn"))}</th><th>${esc(t("annVol"))}</th><th>${esc(t("maxDD"))}</th><th>${esc(t("group"))}</th></tr>${rows}</table></div></div>` +
    (c.skipped.length ? `<p class="muted">${esc(t("skipped", { list: c.skipped.join(", ") }))}</p>` : "");
}
$("clusterBtn").onclick = async () => {
  const btn = $("clusterBtn"), st = $("clusterStatus");
  btn.disabled = true; st.textContent = t("clusterLoading");
  try { state.cluster = await api("/api/cluster", "POST", { tickers: $("clusterInput").value }); st.textContent = ""; renderCluster(state.cluster); }
  catch (e) { st.textContent = t("errorPrefix") + e.message; }
  finally { btn.disabled = false; }
};

// ---------- history ----------
function renderHistory(h) {
  $("history").innerHTML = h.length ? h.map(x => {
    const cur = /\.(NS|BO)$/.test(x.ticker) ? "₹" : "$";
    return `<div class="hrow"><b>${esc(x.ticker)}</b><span>${cur}${x.price.toFixed(2)}</span>` +
      (x.pct == null ? "<span></span>" : `<span class="${x.pct >= 0 ? "g" : "r"}">${sgn(x.pct)}</span>`) +
      (x.signal ? `<span class="chip s-${x.signal}">${esc(t("sig_" + x.signal))}</span>` : "<span></span>") +
      `<small>${esc(x.when)}</small></div>`;
  }).join("") : `<p class="muted">${esc(t("historyEmpty"))}</p>`;
}
async function loadHistory() {
  try { state.history = (await api("/api/history")).history; renderHistory(state.history); }
  catch (e) { $("history").textContent = e.message; }
}

// ---------- start ----------
const existing = document.body.dataset.user;
if (existing) showMain(existing);
applyLang();
