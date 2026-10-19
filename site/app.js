/* Dashboard for stock-signal-monitor. Renders site/data.json with Plotly. */
(function () {
  "use strict";

  const TF_KO = { short: "단기", mid: "중기", long: "장기" };
  const state = { data: null, ticker: null, range: 130 };

  const $ = (sel) => document.querySelector(sel);

  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else node.setAttribute(k, v);
    }
    (children || []).forEach((c) => node.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return node;
  }

  function maxStars() {
    return (state.data && state.data.max_stars) || 4;
  }

  function chip(action, score) {
    const max = maxStars();
    const n = Math.max(0, Math.min(max, score));
    const label = action === "HOLD" ? "HOLD" : `${action} ${"★".repeat(n)}${"☆".repeat(max - n)}`;
    return el("span", { class: `chip ${action}` }, [el("span", { class: "dot", "aria-hidden": "true" }), label]);
  }

  /** Sentiment badge: sign + number, coloured by direction (shape/sign carries the meaning, not colour alone). */
  function sentimentBadge(score) {
    if (score == null) return el("span", { class: "muted", text: "–" });
    const cls = score > 0 ? "pos" : score < 0 ? "neg" : "neu";
    return el("span", { class: `sent ${cls}`, text: (score > 0 ? "+" : "") + score });
  }

  function newsCell(t) {
    const score = t.news ? t.news.score : null;
    const td = el("td", { class: "num" }, [sentimentBadge(score)]);
    if (t.news && t.news.summary && t.news.summary.length) td.title = t.news.summary.join(" / ");
    return td;
  }

  const fmtUSD = (v) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const fmtKRW = (v) => `${Math.round(v).toLocaleString("ko-KR")}원`;

  /** Price cell: USD on top, KRW equivalent underneath (or KRW alone for Korean listings). */
  function priceCell(t, usd, krw) {
    if (t.currency === "KRW") return el("td", { class: "num", text: fmtKRW(usd) });
    const parts = [el("span", { class: "usd", text: fmtUSD(usd) })];
    if (krw != null) parts.push(el("span", { class: "krw", text: fmtKRW(krw) }));
    return el("td", { class: "num price" }, parts);
  }

  function kdCell(kd) {
    const cls = kd.bullish ? "up" : "down";
    return el("td", { class: "kd" }, [
      el("span", { class: cls, text: kd.bullish ? "▲ " : "▼ " }),
      `${kd.k.toFixed(0)} / ${kd.d.toFixed(0)}`,
    ]);
  }

  // ---------- summary table ----------
  function renderSummary() {
    const tbody = $("#summary tbody");
    tbody.replaceChildren();
    for (const t of state.data.tickers) {
      const L = t.latest;
      const chipCell = el("td", {}, [chip(L.action, L.score)]);
      if (L.reasons && L.reasons.length) chipCell.title = L.reasons.join(" · ");  // shows e.g. "실적 발표 D-3, 보류" on HOLD
      const tr = el("tr", { "data-ticker": t.ticker, class: t.ticker === state.ticker ? "on" : "" }, [
        el("td", { text: t.ticker }),
        chipCell,
        el("td", { class: "num", text: L.action === "HOLD" ? "–" : String(L.score) }),
        priceCell(t, L.close, L.close_krw),
        newsCell(t),
        kdCell(L.kd.short), kdCell(L.kd.mid), kdCell(L.kd.long),
        el("td", { class: "num", text: L.date }),
      ]);
      tr.addEventListener("click", () => select(t.ticker));
      tbody.appendChild(tr);
    }
    for (const e of state.data.errors || []) {
      tbody.appendChild(el("tr", {}, [el("td", { text: e.ticker }), el("td", { class: "error", text: e.error, colspan: "8" })]));
    }
  }

  // ---------- news (sector line + per-ticker summary and headlines) ----------
  function renderSectorNews() {
    const box = $("#news-sector");
    box.replaceChildren();
    const n = state.data.news;
    if (!n) { box.appendChild(el("span", { class: "muted", text: "뉴스 수집 꺼짐" })); return; }
    if (!n.scored) {
      box.appendChild(el("span", { class: "muted", text: `헤드라인 ${n.headline_count}건 수집 · 감성 분류 없음${n.error ? ` (${n.error})` : ""}` }));
      return;
    }
    box.appendChild(el("span", { class: "label", text: "업종 감성" }));
    box.appendChild(sentimentBadge(n.sector ? n.sector.score : null));
    if (n.sector && n.sector.summary.length) box.appendChild(el("span", { class: "summary", text: n.sector.summary.join(" · ") }));
    box.appendChild(el("span", { class: "muted small", text: `${n.fetched_at.slice(0, 16).replace("T", " ")} UTC · 가중치 ${n.weight}` }));
  }

  function renderNews(t) {
    $("#news-ticker-name").textContent = t.ticker;
    const box = $("#news-ticker");
    box.replaceChildren();
    const n = t.news;
    if (!n || (n.score == null && !n.headlines.length)) {
      box.appendChild(el("p", { class: "muted", text: "최근 72시간 관련 헤드라인이 없습니다." }));
      return;
    }
    const head = el("div", { class: "news-head" }, [el("span", { class: "label", text: "감성" }), sentimentBadge(n.score)]);
    box.appendChild(head);
    if (n.summary && n.summary.length) {
      box.appendChild(el("ul", { class: "news-summary" }, n.summary.map((s) => el("li", { text: s }))));
    }
    if (n.headlines.length) {
      box.appendChild(el("ul", { class: "headlines" }, n.headlines.map((h) => el("li", {}, [
        el("a", { href: h.url, target: "_blank", rel: "noopener noreferrer", text: h.title }),
        el("span", { class: "muted small", text: ` ${h.source || ""} ${h.published}`.trimEnd() }),
      ]))));
    }
  }

  // ---------- hit-rate tracking ----------
  const SENT_KO = { "-2": "매우 부정 (-2)", "-1": "부정 (-1)", "+0": "중립 (0)", "+1": "긍정 (+1)", "+2": "매우 긍정 (+2)", none: "감성 없음" };
  const CROSS_KO = { golden: "골든", dead: "데드" };

  function renderTracking() {
    const tr = state.data.tracking;
    const note = $("#tracking-note");
    const table = $("#tracking");
    const tbody = table.querySelector("tbody");
    tbody.replaceChildren();
    if (!tr) { note.textContent = "추적 데이터가 없습니다."; table.hidden = true; return; }
    if (!tr.ready) {
      note.textContent = `집계 중 (수익률 확정 ${tr.settled}/${tr.min_settled}건, 기록 ${tr.count}건)`;
      table.hidden = true;
      return;
    }
    note.textContent = `기록 ${tr.count}건 · 수익률 확정 ${tr.settled}건 · 적중 = 골든 뒤 상승 / 데드 뒤 하락`;
    table.hidden = false;
    const cell = (h) => {
      if (!h || !h.n) return [el("td", { class: "num muted", text: "–" }), el("td", { class: "num muted", text: "–" })];
      return [
        el("td", { class: "num", text: `${h.hit.toFixed(0)}% (${h.n})` }),
        el("td", { class: `num ${h.avg > 0 ? "up" : h.avg < 0 ? "down" : ""}`, text: `${h.avg > 0 ? "+" : ""}${h.avg.toFixed(2)}%` }),
      ];
    };
    for (const r of tr.rows) {
      tbody.appendChild(el("tr", {}, [
        el("td", { text: SENT_KO[r.sentiment] || r.sentiment }),
        el("td", { text: CROSS_KO[r.cross] || r.cross }),
        ...cell(r.h5), ...cell(r.h10),
      ]));
    }
  }

  // ---------- history table ----------
  function renderHistory(t) {
    $("#hist-ticker").textContent = t.ticker;
    const tbody = $("#history tbody");
    tbody.replaceChildren();
    const rows = [...t.signals].reverse();
    if (!rows.length) {
      tbody.appendChild(el("tr", {}, [el("td", { colspan: "5", class: "muted", text: "최근 구간에 매수·매도 신호가 없습니다." })]));
      return;
    }
    for (const s of rows) {
      tbody.appendChild(el("tr", {}, [
        el("td", { class: "num", text: s.date }),
        el("td", {}, [chip(s.action, s.score)]),
        el("td", { class: "num", text: String(s.score) }),
        priceCell(t, s.close, s.close_krw),
        el("td", { class: "reasons", text: s.reasons.join(" · ") }),
      ]));
    }
  }

  // ---------- chart ----------
  function renderChart(t) {
    const n = Math.min(state.range, t.dates.length);
    const slice = (arr) => arr.slice(arr.length - n);
    const dates = slice(t.dates);
    const first = dates[0], last = dates[dates.length - 1];
    const th = state.data.thresholds;

    const colK = cssVar("--series-k"), colD = cssVar("--series-d"), colP = cssVar("--series-price");
    const good = cssVar("--good"), bad = cssVar("--critical");
    const ink = cssVar("--ink-2"), muted = cssVar("--muted"), grid = cssVar("--grid"), axis = cssVar("--axis");
    const surface = cssVar("--surface"), zone = cssVar("--zone");

    const hasKrw = t.currency !== "KRW" && Array.isArray(t.close_krw);
    const priceTrace = {
      type: "scatter", mode: "lines", name: "종가", x: dates, y: slice(t.close),
      line: { color: colP, width: 2 }, xaxis: "x", yaxis: "y", showlegend: false,
      hovertemplate: t.currency === "KRW" ? "종가 %{y:,.0f}원<extra></extra>" : "종가 $%{y:,.2f}<extra></extra>",
    };
    if (hasKrw) {
      priceTrace.customdata = slice(t.close_krw);
      priceTrace.hovertemplate = "종가 $%{y:,.2f}  (약 %{customdata:,.0f}원)<extra></extra>";
    }
    const traces = [priceTrace];
    const shapes = [];
    const annotations = [];
    const tfNames = ["short", "mid", "long"];
    tfNames.forEach((name, i) => {
      const tf = t.timeframes[name];
      const ax = `y${i + 2}`;
      const ko = TF_KO[name];
      traces.push({
        type: "scatter", mode: "lines", name: `%K`, legendgroup: "k", showlegend: i === 0,
        x: dates, y: slice(tf.k), line: { color: colK, width: 2 }, yaxis: ax,
        hovertemplate: `${ko} %K %{y:.1f}<extra></extra>`,
      });
      traces.push({
        type: "scatter", mode: "lines", name: `%D`, legendgroup: "d", showlegend: i === 0,
        x: dates, y: slice(tf.d), line: { color: colD, width: 2 }, yaxis: ax,
        hovertemplate: `${ko} %D %{y:.1f}<extra></extra>`,
      });
      // oversold / overbought zones + hairline bounds
      for (const [y0, y1] of [[0, th.oversold], [th.overbought, 100]]) {
        shapes.push({ type: "rect", xref: "paper", x0: 0, x1: 1, yref: ax, y0, y1, fillcolor: zone, line: { width: 0 }, layer: "below" });
      }
      // panel label sits in the gap above the panel, never on the data
      annotations.push({
        xref: "paper", x: 0, yref: ax, y: 100, xanchor: "left", yanchor: "bottom",
        text: `${ko} ${tf.label}`, showarrow: false, font: { size: 12, color: muted }, yshift: 1,
      });
      // cross markers: shape + status colour, never colour alone
      const cx = t.crosses.filter((c) => c.tf === name && c.date >= first && c.date <= last);
      for (const [type, color, symbol, label] of [["golden", good, "triangle-up", "골든크로스"], ["dead", bad, "triangle-down", "데드크로스"]]) {
        const pts = cx.filter((c) => c.type === type);
        traces.push({
          type: "scatter", mode: "markers", name: label, legendgroup: type, showlegend: i === 0,
          x: pts.map((c) => c.date), y: pts.map((c) => c.k), yaxis: ax,
          marker: { symbol, size: 10, color, line: { color: surface, width: 2 } },
          hoverinfo: "skip", // unified hover would list these even on dates without a cross
        });
      }
    });

    // ~6 evenly spaced date ticks, short labels (first one carries the year)
    const step = Math.max(1, Math.ceil(n / 6));
    const tickvals = [], ticktext = [];
    for (let i = 0; i < n; i += step) {
      tickvals.push(dates[i]);
      ticktext.push(i === 0 ? dates[i] : dates[i].slice(5));
    }

    const axisBase = {
      gridcolor: grid, zeroline: false, linecolor: axis, tickfont: { color: muted, size: 11 },
      fixedrange: true,
    };
    const stochAxis = (domain) => ({ ...axisBase, range: [0, 100], tickvals: [20, 50, 80], domain });

    const layout = {
      paper_bgcolor: surface, plot_bgcolor: surface,
      font: { family: 'system-ui, -apple-system, "Segoe UI", sans-serif', color: ink, size: 12 },
      margin: { l: 48, r: 16, t: 8, b: 40 },
      hovermode: "x unified",
      hoverlabel: { bgcolor: surface, bordercolor: axis, font: { color: ink, size: 12 } },
      legend: { orientation: "h", y: 1.02, yanchor: "bottom", x: 0, font: { color: ink } },
      xaxis: {
        ...axisBase, type: "category", anchor: "y4", showgrid: false, fixedrange: false,
        tickvals, ticktext, tickangle: 0, rangeslider: { visible: false },
      },
      yaxis: { ...axisBase, domain: [0.65, 1], tickformat: ",.0f" },
      yaxis2: stochAxis([0.44, 0.61]),
      yaxis3: stochAxis([0.23, 0.40]),
      yaxis4: stochAxis([0.02, 0.19]),
      shapes, annotations,
    };
    for (const k of ["yaxis2", "yaxis3", "yaxis4"]) layout[k].anchor = "x";

    Plotly.react($("#chart"), traces, layout, { displayModeBar: false, responsive: true });
  }

  function select(ticker) {
    state.ticker = ticker;
    const t = state.data.tickers.find((x) => x.ticker === ticker);
    document.querySelectorAll("#ticker-buttons button").forEach((b) => b.classList.toggle("on", b.dataset.ticker === ticker));
    document.querySelectorAll("#summary tbody tr").forEach((r) => r.classList.toggle("on", r.dataset.ticker === ticker));
    renderChart(t);
    renderNews(t);
    renderHistory(t);
    try { localStorage.setItem("ssm.ticker", ticker); } catch (_) { /* private mode */ }
  }

  function renderTickerButtons() {
    const wrap = $("#ticker-buttons");
    wrap.replaceChildren();
    for (const t of state.data.tickers) {
      const b = el("button", { type: "button", role: "tab", "data-ticker": t.ticker, text: t.ticker });
      b.addEventListener("click", () => select(t.ticker));
      wrap.appendChild(b);
    }
  }

  /** Render (or re-render after a refresh) everything from a data.json payload. */
  function render(data) {
    state.data = data;
    $("#presets").textContent = Object.values(data.presets).join(" / ");
    $("#generated").textContent = `갱신 ${new Date(data.generated_at).toLocaleString("ko-KR")}`;
    if (data.fx) {
      $("#fx").textContent = `환율 ${Math.round(data.fx.usdkrw).toLocaleString("ko-KR")}원/$ (${data.fx.date})`;
    }
    const badge = $("#market");
    if (data.market && data.market.last_bar_intraday) {
      badge.textContent = `장중 ${data.market.ny_time} ET · 마지막 봉 미확정`;
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
    renderSummary();
    renderSectorNews();
    renderTracking();
    renderTickerButtons();
    let remembered = state.ticker;
    if (!remembered) { try { remembered = localStorage.getItem("ssm.ticker"); } catch (_) { /* ignore */ } }
    const firstTicker = data.tickers.find((t) => t.ticker === remembered) ? remembered : (data.tickers[0] && data.tickers[0].ticker);
    if (firstTicker) select(firstTicker);
  }

  function init(data) {
    document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => {
      state.range = Number(b.dataset.range);
      document.querySelectorAll("[data-range]").forEach((x) => x.classList.toggle("on", x === b));
      renderChart(state.data.tickers.find((x) => x.ticker === state.ticker));
    }));
    // re-theme the chart when the OS theme flips
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
        if (state.ticker) renderChart(state.data.tickers.find((x) => x.ticker === state.ticker));
      });
    }
    initRefresh();
    render(data);
  }

  // ---------- "지금 갱신": dispatch a GitHub Actions run, then wait for the new data.json ----------
  const TOKEN_KEY = "ssm.gh_token";
  const POLL_MS = 10_000;
  const POLL_LIMIT_MS = 8 * 60_000;

  /** owner/repo from the Pages URL (user.github.io/repo/), falling back to the GitHub link in the header. */
  function repoSlug() {
    const m = location.hostname.match(/^([^.]+)\.github\.io$/);
    const seg = location.pathname.split("/").filter(Boolean)[0];
    if (m && seg) return `${m[1]}/${seg}`;
    const href = ($("a.repo") || {}).href || "";
    const r = href.match(/github\.com\/([^/]+\/[^/#?]+)/);
    return r ? r[1] : "";
  }

  function loadToken() { try { return localStorage.getItem(TOKEN_KEY) || ""; } catch (_) { return ""; } }
  function saveToken(t) { try { localStorage.setItem(TOKEN_KEY, t); } catch (_) { /* private mode */ } }
  function forgetToken() { try { localStorage.removeItem(TOKEN_KEY); } catch (_) { /* ignore */ } }

  function setStatus(text, cls) {
    const s = $("#refresh-status");
    s.textContent = text;
    s.className = `sub refresh-status${cls ? " " + cls : ""}`;
    s.hidden = !text;
  }

  async function dispatchWorkflow(token, file, inputs) {
    const url = `https://api.github.com/repos/${repoSlug()}/actions/workflows/${file}/dispatches`;
    const resp = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: "main", inputs }),
    });
    if (resp.status === 204) return;
    const hint = {
      401: "토큰이 올바르지 않거나 만료되었습니다.",
      403: "토큰에 Actions 쓰기 권한이 없습니다.",
      404: "토큰이 이 저장소에 접근할 수 없습니다 (Repository access 확인).",
      422: "워크플로 입력값이 맞지 않습니다.",
    }[resp.status] || "";
    let detail = "";
    try { detail = (await resp.json()).message || ""; } catch (_) { /* no body */ }
    throw new Error(`GitHub API ${resp.status}. ${hint} ${detail}`.trim());
  }

  async function fetchData() {
    const r = await fetch(`data.json?t=${Date.now()}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`data.json ${r.status}`);
    return r.json();
  }

  /** Poll data.json until generated_at moves past ``since`` (deploy included), then re-render. */
  async function waitForNewData(since, startedAt) {
    while (Date.now() - startedAt < POLL_LIMIT_MS) {
      await new Promise((res) => setTimeout(res, POLL_MS));
      const mins = Math.floor((Date.now() - startedAt) / 60_000);
      setStatus(`갱신 중… ${mins}분 경과 (Actions 실행 → Pages 배포, 보통 1~3분)`);
      try {
        const fresh = await fetchData();
        if (fresh.generated_at > since) {
          render(fresh);
          setStatus(`갱신 완료 ${new Date(fresh.generated_at).toLocaleTimeString("ko-KR")}`, "ok");
          return true;
        }
      } catch (_) { /* transient during deploy: keep polling */ }
    }
    const slug = repoSlug();
    setStatus(`아직 새 데이터가 오지 않았습니다. GitHub Actions 탭에서 실행 상태를 확인하세요: https://github.com/${slug}/actions`, "err");
    return false;
  }

  function initRefresh() {
    const btn = $("#refresh-btn"), dlg = $("#refresh-dialog"), form = $("#refresh-form");
    if (!btn || !dlg || typeof dlg.showModal !== "function") { if (btn) btn.hidden = true; return; }
    const tokenInput = $("#gh-token");

    btn.addEventListener("click", () => {
      tokenInput.value = loadToken();
      $("#gh-forget").hidden = !tokenInput.value;
      dlg.showModal();
    });
    $("#refresh-cancel").addEventListener("click", () => dlg.close());
    $("#gh-forget").addEventListener("click", () => { forgetToken(); tokenInput.value = ""; $("#gh-forget").hidden = true; });

    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const token = tokenInput.value.trim();
      if (!token) return;
      const mode = form.elements.mode.value;
      if ($("#gh-remember").checked) saveToken(token); else forgetToken();
      dlg.close();
      btn.disabled = true;
      const since = (state.data && state.data.generated_at) || "";
      const startedAt = Date.now();
      setStatus("GitHub Actions 실행을 요청하는 중…");
      try {
        if (mode === "telegram") {
          // Daily signal check (resend) → finishes → Deploy dashboard runs via workflow_run.
          await dispatchWorkflow(token, "monitor.yml", { resend: "true", refresh_news: "true" });
        } else {
          await dispatchWorkflow(token, "pages.yml", { refresh_news: "true" });
        }
        setStatus("실행 요청됨. 새 데이터를 기다리는 중…");
        await waitForNewData(since, startedAt);
      } catch (err) {
        setStatus(`갱신 실패: ${err.message}`, "err");
      } finally {
        btn.disabled = false;
      }
    });
  }

  fetch("data.json", { cache: "no-store" })
    .then((r) => { if (!r.ok) throw new Error(`data.json ${r.status}`); return r.json(); })
    .then(init)
    .catch((err) => {
      $("#summary tbody").appendChild(el("tr", {}, [el("td", { colspan: "9", class: "error", text: `데이터를 불러오지 못했습니다: ${err.message}` })]));
    });
})();
