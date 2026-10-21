/* VoiceGuard Team Portal. No data is kept in the page: everything comes from /api/portal with the session token,
   which lives only in this tab (sessionStorage) and is dropped on sign-out, idle time-out or when the tab closes.
   All text from the server is put on the page as text (never as HTML). */
"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const A = "/api/portal";
  let token = null, me = null, current = "dashboard", lastActive = Date.now();
  try { token = sessionStorage.getItem("vgp"); } catch (e) { token = null; }

  // ---------------------------------------------------------------- helpers
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "height") el.style.height = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const fmt = (t) => t ? new Date(t).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "—";
  const pct = (x) => x === null || x === undefined ? "—" : (x * 100).toFixed(1) + "%";
  const num = (x) => x === null || x === undefined ? "—" : Number(x).toLocaleString();

  function toast(msg, bad) {
    const t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(toast.t);
    toast.t = setTimeout(() => t.classList.add("hidden"), 4000);
  }

  async function api(path, opts = {}) {
    const o = { method: opts.method || "GET", headers: {} };
    if (token) o.headers.Authorization = "Bearer " + token;
    if (opts.body instanceof FormData) o.body = opts.body;
    else if (opts.body !== undefined) { o.headers["Content-Type"] = "application/json"; o.body = JSON.stringify(opts.body); }
    const r = await fetch(A + path, o);
    if (opts.raw && r.ok) return r;
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (r.status === 401 && !opts.login) { signedOut(data && data.detail); throw new Error("signed out"); }
    if (!r.ok) throw new Error((data && (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail))) || ("Error " + r.status));
    return data;
  }

  function ask(title, fields, okText) {
    // fields: [{key, label, type?, value?, options?}] -> Promise<values|null>
    return new Promise((resolve) => {
      $("modal-title").textContent = title;
      const body = $("modal-body");
      body.replaceChildren();
      const inputs = {};
      for (const f of fields || []) {
        const id = "m-" + f.key;
        body.append(h("label", { for: id, text: f.label }));
        let el;
        if (f.options) {
          el = h("select", { id }, f.options.map(([v, t]) => h("option", { value: v, text: t })));
          if (f.value) el.value = f.value;
        } else if (f.type === "textarea") el = h("textarea", { id });
        else el = h("input", { id, type: f.type || "text" });
        if (f.value && !f.options) el.value = f.value;
        inputs[f.key] = el;
        body.append(el);
      }
      if (!fields || !fields.length) body.append(h("p", { class: "muted", text: "Are you sure?" }));
      $("modal-ok").textContent = okText || "OK";
      $("modal").classList.remove("hidden");
      const first = Object.values(inputs)[0];
      if (first) setTimeout(() => first.focus(), 30);
      const done = (ok) => {
        $("modal").classList.add("hidden");
        $("modal-ok").onclick = $("modal-cancel").onclick = null;
        resolve(ok ? Object.fromEntries(Object.entries(inputs).map(([k, el]) => [k, el.value.trim()])) : null);
      };
      $("modal-ok").onclick = () => done(true);
      $("modal-cancel").onclick = () => done(false);
    });
  }

  function table(cols, rows, onRow) {
    if (!rows || !rows.length) return h("div", { class: "empty", text: "Nothing here yet." });
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { text: c[0] })))),
      h("tbody", {}, rows.map((r) => h("tr", { class: onRow ? "click" : null, onclick: onRow ? () => onRow(r) : null },
        cols.map((c) => { const v = c[1](r); return h("td", { class: c[2] || null }, v instanceof Node ? v : String(v ?? "—")); }))))));
  }
  const stat = (label, n, sub, cls) => h("div", { class: "card stat " + (cls || "") }, h("div", { class: "l", text: label }),
    h("div", { class: "n", text: n }), sub ? h("div", { class: "s", text: sub }) : null);
  const pill = (text, cls) => h("span", { class: "pill " + (cls || ""), text });
  const levelPill = (l) => pill(l || "—", l === "danger" ? "bad" : l === "suspicious" ? "warn" : l === "safe" ? "good" : "");
  const sevPill = (s) => pill(s, s === "critical" || s === "high" ? "bad" : s === "medium" ? "warn" : "");
  const card = (title, ...kids) => h("div", { class: "card" }, title ? h("h3", { text: title }) : null, ...kids);
  const btn = (text, onclick, cls) => h("button", { class: cls || "", onclick, text });

  // ---------------------------------------------------------------- sign-in
  function showLogin(step, msg) {
    $("app").classList.add("hidden");
    $("login").classList.remove("hidden");
    for (const s of ["phone", "codes", "setup"]) $("step-" + s).classList.toggle("hidden", s !== step);
    $("login-err").textContent = msg || "";
  }
  function signedOut(msg) {
    token = null; me = null;
    try { sessionStorage.removeItem("vgp"); } catch (e) { /* private window */ }
    $("view").replaceChildren();
    showLogin("phone", msg || "");
  }
  function busy(b, on) { b.disabled = on; }

  $("btn-send").addEventListener("click", async () => {
    const phone = $("in-phone").value.trim();
    if (phone.replace(/\D/g, "").length < 10) { $("login-err").textContent = "Enter your full phone number."; return; }
    busy($("btn-send"), true);
    try {
      const r = await api("/login/start", { method: "POST", body: { phone }, login: true });
      $("sent-msg").textContent = r.message;
      showLogin("codes");
      $("in-otp").focus();
    } catch (e) { $("login-err").textContent = e.message; }
    busy($("btn-send"), false);
  });
  $("btn-back").addEventListener("click", () => showLogin("phone"));
  $("btn-verify").addEventListener("click", async () => {
    busy($("btn-verify"), true);
    try {
      const r = await api("/login/verify", { method: "POST", login: true,
        body: { phone: $("in-phone").value.trim(), otp: $("in-otp").value.trim(), code: $("in-totp").value.trim() } });
      $("in-otp").value = $("in-totp").value = "";
      if (r.stage === "setup") {
        token = r.token;
        $("qr").innerHTML = r.qr_svg;          // SVG made by our own server from the new key
        $("secret").textContent = r.secret;
        showLogin("setup");
        $("in-confirm").focus();
      } else start(r.token);
    } catch (e) { $("login-err").textContent = e.message; }
    busy($("btn-verify"), false);
  });
  $("btn-confirm").addEventListener("click", async () => {
    busy($("btn-confirm"), true);
    try {
      const r = await api("/totp/confirm", { method: "POST", body: { code: $("in-confirm").value.trim() }, login: true });
      $("qr").replaceChildren(); $("secret").textContent = ""; $("in-confirm").value = "";
      start(r.token);
    } catch (e) { $("login-err").textContent = e.message; }
    busy($("btn-confirm"), false);
  });
  for (const [input, button] of [["in-phone", "btn-send"], ["in-otp", "btn-verify"], ["in-totp", "btn-verify"], ["in-confirm", "btn-confirm"]])
    $(input).addEventListener("keydown", (e) => { if (e.key === "Enter") $(button).click(); });

  $("btn-logout").addEventListener("click", async () => {
    try { await api("/logout", { method: "POST" }); } catch (e) { /* already ended */ }
    signedOut("You signed out.");
  });

  // idle time-out (the server enforces it too)
  for (const ev of ["mousemove", "keydown", "click", "touchstart", "scroll"]) document.addEventListener(ev, () => { lastActive = Date.now(); }, { passive: true });
  setInterval(() => {
    if (!me) return;
    const left = me.idle_minutes * 60 - (Date.now() - lastActive) / 1000;
    $("idle").textContent = left < 300 ? `Signing out in ${Math.max(0, Math.ceil(left / 60))} min` : "";
    if (left <= 0) { api("/logout", { method: "POST" }).catch(() => {}); signedOut("Signed out after " + me.idle_minutes + " minutes without activity."); }
  }, 15000);

  // ---------------------------------------------------------------- shell
  const SECTIONS = [
    ["dashboard", "Dashboard", "▦"], ["fraud", "Fraud & scams", "⚠"], ["evidence", "Evidence", "🗂"],
    ["telecom", "Telecom data", "📡"], ["network", "Network", "📶"], ["ai", "AI models", "◈"], ["security", "Security", "🏰"],
    ["reports", "Daily reports", "📊"], ["logs", "Logs", "🧾"], ["team", "Team", "👥"], ["audit", "Audit log", "📜"], ["sessions", "My sessions", "🔑"],
  ];
  async function start(t) {
    token = t;
    try { sessionStorage.setItem("vgp", t); } catch (e) { /* private window: this tab only */ }
    try { me = await api("/me"); } catch (e) { if (e.message !== "signed out") signedOut(e.message); return; }
    $("login").classList.add("hidden");
    $("app").classList.remove("hidden");
    $("who-name").textContent = me.name;
    $("who-role").textContent = me.role_name;
    const nav = $("nav");
    nav.replaceChildren(...SECTIONS.filter(([k]) => k === "sessions" || me.can.includes(k)).map(([k, label, ic]) =>
      h("button", { "data-k": k, onclick: () => go(k) }, h("span", { class: "ic", text: ic }), label)));
    lastActive = Date.now();
    go(me.can.includes(current) || current === "sessions" ? current : "dashboard");
  }
  function go(k) {
    current = k;
    for (const b of $("nav").children) b.classList.toggle("on", b.dataset.k === k);
    $("title").textContent = (SECTIONS.find((s) => s[0] === k) || [0, ""])[1];
    document.querySelector(".side").classList.remove("open");
    render();
  }
  async function render() {
    const view = $("view");
    view.replaceChildren(h("div", { class: "empty", text: "Loading…" }));
    try {
      const out = await VIEWS[current]();
      view.replaceChildren(...[].concat(out).filter((x) => x !== null && x !== undefined && x !== false));
    } catch (e) {
      if (e.message !== "signed out") view.replaceChildren(h("div", { class: "card error", text: e.message }));
    }
  }
  $("btn-refresh").addEventListener("click", () => render());
  $("btn-menu").addEventListener("click", () => document.querySelector(".side").classList.toggle("open"));
  const manager = () => me && me.manager;

  // ---------------------------------------------------------------- views
  const VIEWS = {};

  VIEWS.dashboard = async () => {
    const d = await api("/dashboard");
    $("lock-banner").classList.toggle("hidden", !d.security.lockdown);
    const max = Math.max(1, ...d.series.map((x) => Math.max(x.danger + x.suspicious, x.reports)));
    return [
      h("div", { class: "grid" },
        stat("Danger calls (24 h)", num(d.fraud.danger_calls_24h), num(d.fraud.checks_7d) + " checks in 7 days", d.fraud.danger_calls_24h ? "bad" : ""),
        stat("Reported scam numbers", num(d.fraud.reported_numbers), num(d.fraud.reports_7d) + " new reports in 7 days"),
        stat("Telecom flags (7 d)", num(d.fraud.telecom_flags_7d), num(d.telecom.partners) + " operator key(s) active"),
        stat("Scam voices known", num(d.fraud.scam_voices)),
        stat("Evidence", num(d.evidence.total), num(d.evidence.open_cases) + " case(s) not closed"),
        stat("Open security incidents", num(d.security.open_incidents), d.security.serious + " serious",
          d.security.serious ? "bad" : d.security.open_incidents ? "warn" : "good"),
        stat("Users / families", num(d.people.users) + " / " + num(d.people.families), num(d.people.phones_active_24h) + " of " + num(d.people.phones) + " phones reported today"),
        stat("AI voice detector", d.ai.detector_auc ? pct(d.ai.detector_auc) : "—", "AUC on " + num(d.ai.trained_clips) + " clips" + (d.ai.candidates ? ` · ${d.ai.candidates} candidate(s) ready` : "")),
      ),
      d.security.lockdown ? card("", h("b", { text: "VoiceGuard is in LOCKDOWN: " }), d.security.lockdown.reason) : null,
      card("Last 14 days",
        h("div", { class: "bars" }, d.series.map((x) => h("div", { class: "day", title: `${x.day}: ${x.danger} danger, ${x.suspicious} suspicious, ${x.reports} reports` },
          h("div", { class: "seg reports", height: (x.reports / max * 45) + "%" }),
          h("div", { class: "seg suspicious", height: (x.suspicious / max * 50) + "%" }),
          h("div", { class: "seg danger", height: (x.danger / max * 50) + "%" })))),
        h("div", { class: "bars-x" }, d.series.map((x) => h("span", { text: x.day.slice(8) }))),
        h("div", { class: "legend" }, h("span", {}, h("i", { class: "seg danger" }), "Danger calls"),
          h("span", {}, h("i", { class: "seg suspicious" }), "Suspicious calls"), h("span", {}, h("i", { class: "seg reports" }), "Scam reports"))),
      h("div", { class: "grid" },
        stat("Server files", d.security.integrity_clean ? "Trusted" : "Changed", d.security.integrity_clean ? "match the approved version" : "check Security", d.security.integrity_clean ? "good" : "warn"),
        stat("App crashes open", num(d.security.open_crashes), "", d.security.open_crashes ? "warn" : ""),
        stat("AI", d.ai.ready ? "Ready" : "Starting", "", d.ai.ready ? "good" : "warn")),
    ];
  };

  // ---- fraud
  let fraudTab = "numbers";
  VIEWS.fraud = async () => {
    const tabs = h("div", { class: "tabs" }, [["numbers", "Reported numbers"], ["risk", "Risky calls"], ["voices", "Scam voices"]].map(([k, t]) =>
      h("button", { class: fraudTab === k ? "on" : "", text: t, onclick: () => { fraudTab = k; render(); } })));
    if (fraudTab === "risk") {
      const rows = await api("/fraud/risk?days=30");
      return [tabs, card("Risky calls checked by the AI (30 days)", table([
        ["When", (r) => fmt(r.created_at)], ["Level", (r) => levelPill(r.level)], ["Score", (r) => r.score, "num"],
        ["Caller number", (r) => r.number || "—"], ["Check", (r) => r.source], ["User", (r) => r.user], ["User phone", (r) => r.user_phone]], rows))];
    }
    if (fraudTab === "voices") {
      const rows = await api("/fraud/voices");
      return [tabs, card("Scam Voice ID - confirmed scammer voices (only a voice fingerprint is kept, never audio)", table([
        ["ID", (r) => r.id], ["Made with", (r) => r.kind || "—"], ["Imitated", (r) => r.clone_of || "—"],
        ["Calls", (r) => r.samples, "num"], ["Confirmed", (r) => r.confirmations, "num"], ["Reason", (r) => r.reason || "—"],
        ["Last heard", (r) => fmt(r.last_seen)]], rows))];
    }
    const q = h("input", { placeholder: "Search a number", value: VIEWS.fraud.q || "" });
    const rows = await api("/fraud/numbers?q=" + encodeURIComponent(VIEWS.fraud.q || ""));
    const search = () => { VIEWS.fraud.q = q.value.trim(); render(); };
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") search(); });
    return [tabs,
      card("", h("div", { class: "row" }, q, btn("Search", search), btn("Flag a number", flagNumber, "primary"), btn("Export CSV", exportCsv))),
      card("Numbers people and operators reported", table([
        ["Number", (r) => r.number], ["Reports", (r) => r.reports, "num"], ["Label", (r) => pill(r.label, r.label === "Suspected fraud" ? "bad" : "warn")],
        ["From", (r) => r.sources.join(", ")], ["Latest reason", (r) => r.reason || "—"], ["Last report", (r) => fmt(r.last)],
        ["", (r) => btn("Clear (false alarm)", (e) => { e.stopPropagation(); clearNumber(r.number); }, "small danger")]], rows,
        (r) => showReports(r.number)))];
  };
  async function flagNumber() {
    const v = await ask("Flag a scam number", [{ key: "number", label: "Phone number" }, { key: "reason", label: "Why (shown to people as the warning)", type: "textarea" }], "Flag");
    if (!v) return;
    try { await api("/fraud/flag", { method: "POST", body: v }); toast("Number flagged."); render(); } catch (e) { toast(e.message, true); }
  }
  async function clearNumber(n) {
    const v = await ask("Clear " + n + "? People will stop being warned about it.", [{ key: "reason", label: "Why is this number safe?", type: "textarea" }], "Clear reports");
    if (!v) return;
    try { const r = await api("/fraud/numbers/" + encodeURIComponent(n) + "/clear", { method: "POST", body: v }); toast(r.removed + " report(s) removed."); render(); } catch (e) { toast(e.message, true); }
  }
  async function showReports(n) {
    const rows = await api("/fraud/reports?number=" + encodeURIComponent(n));
    $("view").append(card("All reports for " + n, table([["When", (r) => fmt(r.created_at)], ["From", (r) => r.source], ["Reason", (r) => r.reason || "—"]], rows)));
    $("view").lastChild.scrollIntoView({ behavior: "smooth" });
  }
  async function exportCsv() {
    try {
      const r = await api("/fraud/export.csv", { raw: true });
      const a = h("a", { href: URL.createObjectURL(await r.blob()), download: "voiceguard-fraud-numbers.csv" });
      document.body.append(a); a.click(); a.remove();
      toast("Exported (recorded in the audit log).");
    } catch (e) { toast(e.message, true); }
  }

  // ---- evidence
  const CASE = { new: "New", reviewing: "Reviewing", sent_to_police: "Sent to police", closed: "Closed" };
  VIEWS.evidence = async () => {
    const f = VIEWS.evidence.f || "";
    const rows = await api("/evidence?status=" + f);
    const tabs = h("div", { class: "tabs" }, [["", "All"], ...Object.entries(CASE)].map(([k, t]) =>
      h("button", { class: f === k ? "on" : "", text: t, onclick: () => { VIEWS.evidence.f = k; render(); } })));
    const detail = h("div", {}, card("", h("p", { class: "muted", text: "Choose a case on the left to see the transcript, the AI report and the recording." })));
    const list = card("Saved evidence (" + rows.length + ")", table([
      ["When", (r) => fmt(r.created_at)], ["Level", (r) => levelPill(r.level)], ["Score", (r) => r.risk_score ?? "—", "num"],
      ["Caller", (r) => r.number || "—"], ["Claimed to be", (r) => r.claimed_name || "—"], ["User", (r) => r.user],
      ["Case", (r) => pill(CASE[r.status] || r.status, r.status === "closed" ? "good" : r.status === "sent_to_police" ? "info" : "warn")]],
      rows, (r) => openEvidence(r.id, detail)));
    return [tabs, h("div", { class: "split" }, list, detail)];
  };
  async function openEvidence(id, box) {
    box.replaceChildren(h("div", { class: "empty", text: "Loading…" }));
    try {
      const e = await api("/evidence/" + encodeURIComponent(id));
      const rep = e.report || {};
      const risk = rep.risk || {};
      const audio = h("div", {});
      if (e.has_audio) audio.append(btn("▶ Load recording", async (ev) => {
        ev.target.disabled = true;
        try {
          const r = await api("/evidence/" + encodeURIComponent(id) + "/audio", { raw: true });
          audio.replaceChildren(h("audio", { controls: true, src: URL.createObjectURL(await r.blob()) }));
        } catch (err) { toast(err.message, true); ev.target.disabled = false; }
      }));
      const status = h("select", {}, Object.entries(CASE).map(([k, t]) => h("option", { value: k, text: t })));
      status.value = e.case.status;
      const note = h("textarea", { placeholder: "Add a note (who you called, police complaint number…)" });
      const save = async (body) => {
        try { await api("/evidence/" + encodeURIComponent(id) + "/case", { method: "POST", body }); toast("Case updated."); openEvidence(id, box); }
        catch (err) { toast(err.message, true); }
      };
      box.replaceChildren(
        card("Evidence " + e.id,
          h("div", { class: "row" }, levelPill(e.level), pill("score " + (e.risk_score ?? "—")), pill(e.source)),
          h("p", {}, h("b", { text: "Caller: " }), e.number || "unknown", "  ·  ", h("b", { text: "Claimed: " }), e.claimed_name || "—"),
          h("p", {}, h("b", { text: "User: " }), e.user + " " + (e.user_phone || ""), "  ·  ", fmt(e.created_at)),
          (risk.reasons || []).length ? h("ul", {}, risk.reasons.slice(0, 6).map((r) => h("li", { text: typeof r === "string" ? r : (r.en || r.text || JSON.stringify(r)) }))) : null,
          h("h3", { text: "Transcript" }), h("div", { class: "transcript", text: e.transcript || "(no transcript)" }),
          e.notes ? h("p", { class: "muted", text: "User's note: " + e.notes }) : null,
          h("h3", { text: "Recording" }), e.has_audio ? audio : h("p", { class: "muted", text: "No recording saved." })),
        card("Case",
          h("div", { class: "row" }, status, btn("Save status", () => save({ status: status.value }), "primary"),
            btn("Assign to me", () => save({ assign_to_me: true }))),
          h("p", { class: "muted", text: "Assigned to: " + (e.case.assigned_to || "nobody") }),
          e.case.notes.map((n) => h("div", { class: "note" }, h("small", { text: n.by + " · " + fmt(n.at) }), n.text)),
          note, h("div", { class: "row end" }, btn("Add note", () => note.value.trim() && save({ note: note.value })))));
    } catch (err) { box.replaceChildren(card("", h("p", { class: "error", text: err.message }))); }
  }

  // ---- telecom
  VIEWS.telecom = async () => {
    const [partners, flags, events] = await Promise.all([api("/telecom/partners"), api("/telecom/flags"), api("/telecom/events")]);
    const keybox = h("div", {});
    const add = async () => {
      const v = await ask("New telecom operator key", [{ key: "name", label: "Operator name (e.g. Jio, Airtel, Vi, BSNL)" }], "Create key");
      if (!v) return;
      try {
        const r = await api("/telecom/partners", { method: "POST", body: v });
        keybox.replaceChildren(h("div", { class: "keybox" }, h("b", { text: "Key for " + r.name + " - copy it now, it is shown only once:" }),
          h("p", {}, h("code", { text: r.key })), btn("Copy", () => navigator.clipboard.writeText(r.key).then(() => toast("Copied.")))));
        const list = await api("/telecom/partners");
        partnersBox.replaceChildren(partnerTable(list));
      } catch (e) { toast(e.message, true); }
    };
    const partnerTable = (list) => table([
      ["Operator", (p) => p.name], ["Key", (p) => h("code", { text: p.key_hint })], ["Status", (p) => pill(p.active ? "active" : "revoked", p.active ? "good" : "")],
      ["Calls", (p) => p.calls, "num"], ["Last used", (p) => fmt(p.last_used)], ["Created", (p) => fmt(p.created_at) + " by " + p.created_by],
      ["", (p) => p.active && manager() ? btn("Revoke", async () => {
        if (!(await ask("Revoke the key of " + p.name + "? Their systems stop working with VoiceGuard at once.", [], "Revoke"))) return;
        try { await api("/telecom/partners/" + p.id + "/revoke", { method: "POST" }); toast("Key revoked."); render(); } catch (e) { toast(e.message, true); }
      }, "small danger") : ""]], list);
    const partnersBox = h("div", {}, partnerTable(partners));
    return [
      card("Operator API keys", manager() ? h("div", { class: "row" }, btn("New operator key", add, "primary")) : h("p", { class: "muted", text: "Only an owner or admin can create or revoke keys." }),
        keybox, partnersBox,
        h("details", {}, h("summary", { text: "How operators use their key" }),
          h("pre", { class: "code", text: `Flag a fraud number:\nPOST ${location.origin}/api/v1/telecom/flag\nX-API-Key: <key>\n{"number": "+9190000...", "reason": "SIM box / mass calling"}\n\nLook up a number before the phone rings:\nGET ${location.origin}/api/v1/telecom/lookup/+9190000...\nX-API-Key: <key>` }))),
      h("div", { class: "grid two" },
        card("Fraud numbers flagged by operators", table([["When", (r) => fmt(r.created_at)], ["Number", (r) => r.number], ["Operator & reason", (r) => r.reason]], flags)),
        card("Operator activity", table([["When", (r) => fmt(r.at)], ["Operator", (r) => r.partner], ["What", (r) => pill(r.kind, r.kind === "bad_key" ? "bad" : "info")],
          ["Number", (r) => r.number || "—"], ["Detail", (r) => r.detail]], events))),
    ];
  };

  // ---- AI
  VIEWS.ai = async () => {
    const d = await api("/ai");
    const metricGrid = (m) => h("div", { class: "metrics" }, Object.entries(m.by_condition || {}).map(([k, v]) =>
      h("div", { class: "metric", title: "AUC " + (v.auc ?? "—") }, h("b", { text: pct(v.acc) }), k.replace(/_/g, " "))));
    const slotCard = (slot, title, info) => card(title,
      info.active ? h("div", {},
        h("div", { class: "row" }, pill("LIVE", "good"), h("b", { text: info.active.file }), pill(info.active.head), pill("AUC " + pct(info.active.auc)),
          pill(num(info.active.clips) + " clips")),
        h("p", { class: "muted", text: "Updated " + fmt(info.active.updated) + (info.active.activated_by ? " · switched by " + info.active.activated_by + " from " + info.active.activated_from : "") }),
        h("small", { class: "muted", text: "Accuracy by phone condition:" }), metricGrid(info.active)) : h("p", { class: "muted", text: "Not trained yet." }),
      info.choices.length ? h("h3", { text: "Other versions" }) : null,
      info.choices.map((c) => h("div", { class: "card" },
        h("div", { class: "row" }, h("b", { text: c.file }), pill(c.file.includes("candidate") ? "candidate" : "backup", c.file.includes("candidate") ? "info" : ""),
          pill(c.head), pill("AUC " + pct(c.auc)), pill(num(c.clips) + " clips"), h("span", { class: "spacer" }),
          manager() ? btn("Make live", async () => {
            const v = await ask("Make " + c.file + " the live " + title + "?", [{ key: "reason", label: "Why (e.g. better on unseen voices: numbers)", type: "textarea" }], "Make live");
            if (!v) return;
            try { const r = await api("/ai/activate", { method: "POST", body: { slot, file: c.file, reason: v.reason } }); toast("Live now. Previous model kept as " + r.backup); render(); }
            catch (e) { toast(e.message, true); }
          }, "small primary") : null),
        metricGrid(c))));
    const file = h("input", { type: "file", accept: "audio/*" });
    const result = h("div", {});
    const test = async () => {
      if (!file.files[0]) return toast("Choose an audio clip first.", true);
      const fd = new FormData(); fd.append("file", file.files[0]);
      result.replaceChildren(h("p", { class: "muted", text: "Checking…" }));
      try {
        const r = await api("/ai/test", { method: "POST", body: fd });
        if (!r.ok) { result.replaceChildren(h("p", { class: "error", text: r.message || r.error })); return; }
        result.replaceChildren(h("div", { class: "row" }, levelPill(r.risk.level), pill("risk " + r.risk.score + "/100"),
          pill("AI voice " + pct(r.ai_voice.fake_prob), r.ai_voice.fake_prob > .5 ? "bad" : "good"),
          r.reverse_engineering ? pill("source: " + (r.reverse_engineering.label || r.reverse_engineering.kind || "—"), "info") : null,
          pill(r.seconds.toFixed(1) + " s clip"), pill((r.timings_s || {}).total + " s to check")),
          h("ul", {}, (r.risk.reasons || []).slice(0, 6).map((x) => h("li", { text: typeof x === "string" ? x : (x.en || x.text || JSON.stringify(x)) }))));
      } catch (e) { result.replaceChildren(h("p", { class: "error", text: e.message })); }
    };
    const checks = d.checks_7d || {};
    return [
      h("div", { class: "grid" },
        stat("AI status", d.ready ? "Ready" : "Starting", Object.keys(d.loaded || {}).filter((k) => d.loaded[k]).length + " model(s) loaded", d.ready ? "good" : "warn"),
        stat("Checks (7 days)", num(Object.values(checks).reduce((a, b) => a + b, 0)), `${checks.danger || 0} danger · ${checks.suspicious || 0} suspicious`),
        stat("Source tracer", pct(d.tracer.balanced_accuracy), "how the fake voice was made"),
        stat("Voice & speech", "WavLM + Whisper", (d.speaker_model || "").split("/").pop() + " · " + (d.speech_model || "").split("/").pop())),
      card("Test a clip with the live AI", h("p", { class: "muted", text: "Nothing is stored. No transcript is made." }),
        h("div", { class: "row" }, file, btn("Check", test, "primary")), result),
      h("div", { class: "grid two" }, slotCard("detector", "AI-voice detector", d.slots.detector), slotCard("room", "Live Call Check detector (loudspeaker)", d.slots.room)),
      manager() ? null : card("", h("p", { class: "muted", text: "Only an owner or admin can switch the live model." })),
    ];
  };

  // ---- security: the castle
  const SVGNS = "http://www.w3.org/2000/svg";
  function sv(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  // a wall from x to x+w, battlements on top
  function wallPath(x, top, w, bottom, mw = 14, gap = 9, mh = 11) {
    const pts = [[x, bottom], [x, top]];
    for (let cx = x; cx < x + w - 0.1; cx += mw + gap) {
      const e = Math.min(cx + mw, x + w);
      pts.push([cx, top - mh], [e, top - mh], [e, top], [Math.min(e + gap, x + w), top]);
    }
    pts.push([x + w, top], [x + w, bottom]);
    return "M" + pts.map((p) => p.join(" ")).join(" L") + " Z";
  }
  const STATE_WORD = { secure: "secure", warning: "needs a look", breached: "under attack" };
  let castleLayer = 0;

  function drawCastle(layers, onPick) {
    const st = Object.fromEntries(layers.map((l) => [l.id, l]));
    const part = (n, shapes, badge) => {
      const l = st[n] || { state: "secure", name: "", what: "" };
      const g = sv("g", { class: `part l${n} s-${l.state}${castleLayer === n ? " sel" : ""}`, tabindex: "0", role: "button",
        "aria-label": `Layer ${n} ${l.name}: ${STATE_WORD[l.state] || l.state}`,
        onclick: () => onPick(n), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPick(n); } } },
        sv("title", {}, `Layer ${n} · ${l.name} – ${l.what}: ${STATE_WORD[l.state] || l.state}` +
          (l.open_incidents ? ` (${l.open_incidents} open incident${l.open_incidents > 1 ? "s" : ""})` : "")),
        shapes,
        sv("g", { class: "badge" }, sv("circle", { cx: badge[0], cy: badge[1], r: 12 }),
          sv("text", { x: badge[0], y: badge[1] + 4.5, "text-anchor": "middle" }, String(n))));
      return g;
    };
    const slits = (x, y) => [0, 1].map((i) => sv("rect", { class: "win", x: x + i * 26, y, width: 6, height: 18, rx: 3 }));
    return sv("svg", { class: "castle", viewBox: "0 0 640 430", role: "img", "aria-label": "VoiceGuard castle: seven security layers" },
      sv("rect", { class: "sky", x: 0, y: 0, width: 640, height: 430, rx: 14 }),
      [[60, 40], [140, 70], [480, 36], [560, 80], [420, 64], [210, 30]].map(([x, y]) => sv("circle", { class: "star", cx: x, cy: y, r: 1.6 })),
      sv("rect", { class: "ground", x: 0, y: 362, width: 640, height: 68 }),
      // 4 inner wall (behind everything)
      part(4, [sv("path", { class: "body", d: wallPath(120, 208, 400, 300) })], [170, 248]),
      // 5 keep
      part(5, [sv("path", { class: "body", d: wallPath(255, 104, 130, 300, 13, 8) }),
        sv("path", { class: "win", d: "M276 150 v-14 a7 7 0 0 1 14 0 v14 z" }), sv("path", { class: "win", d: "M350 150 v-14 a7 7 0 0 1 14 0 v14 z" }),
        sv("path", { class: "win", d: "M276 200 v-14 a7 7 0 0 1 14 0 v14 z" }), sv("path", { class: "win", d: "M350 200 v-14 a7 7 0 0 1 14 0 v14 z" })], [320, 158]),
      // 6 vault, inside the keep
      part(6, [sv("rect", { class: "body", x: 299, y: 222, width: 42, height: 52, rx: 5 }),
        sv("circle", { class: "lock", cx: 320, cy: 244, r: 6 }), sv("rect", { class: "lock", x: 318, y: 246, width: 4, height: 12, rx: 1 })], [362, 232]),
      // 7 royal guard: banners on the keep and the towers
      part(7, [sv("line", { class: "pole", x1: 320, y1: 93, x2: 320, y2: 34 }), sv("path", { class: "body", d: "M321 36 L362 47 L321 58 Z" }),
        sv("line", { class: "pole", x1: 75, y1: 229, x2: 75, y2: 188 }), sv("path", { class: "body", d: "M76 190 L104 198 L76 206 Z" }),
        sv("line", { class: "pole", x1: 565, y1: 229, x2: 565, y2: 188 }), sv("path", { class: "body", d: "M566 190 L594 198 L566 206 Z" })], [382, 47]),
      // 1 moat
      part(1, [sv("path", { class: "body", d: "M8 372 Q 88 362 168 372 T 328 372 T 488 372 T 632 370 L632 404 Q 552 414 472 404 T 312 404 T 152 404 T 8 406 Z" }),
        sv("path", { class: "wave", d: "M40 388 q 12 -6 24 0 t 24 0 M180 392 q 12 -6 24 0 t 24 0 M420 390 q 12 -6 24 0 t 24 0 M540 388 q 12 -6 24 0 t 24 0" })], [610, 388]),
      // 2 outer wall and its towers
      part(2, [sv("path", { class: "body", d: wallPath(96, 292, 448, 370) }),
        sv("path", { class: "body", d: wallPath(40, 240, 70, 370, 12, 7) }), sv("path", { class: "body", d: wallPath(530, 240, 70, 370, 12, 7) }),
        slits(57, 270), slits(547, 270), slits(57, 318), slits(547, 318)], [150, 330]),
      // 3 gatehouse with the portcullis and the drawbridge
      part(3, [sv("path", { class: "body", d: wallPath(274, 262, 92, 370, 12, 8) }),
        sv("path", { class: "gate", d: "M298 370 L298 322 A22 22 0 0 1 342 322 L342 370 Z" }),
        [306, 314, 322, 330, 338].map((x) => sv("line", { class: "grill", x1: x, y1: 304, x2: x, y2: 370 })),
        [326, 344].map((y) => sv("line", { class: "grill", x1: 298, y1: y, x2: 342, y2: y })),
        sv("path", { class: "wood", d: "M298 370 L342 370 L352 404 L288 404 Z" })], [320, 282]),
    );
  }

  VIEWS.security = async () => {
    const d = await api("/security");
    $("lock-banner").classList.toggle("hidden", !d.lockdown);
    const lock = async () => {
      if (d.lockdown) {
        if (!(await ask("Lift the lockdown? The app starts working again for everyone.", [], "Lift lockdown"))) return;
        await api("/security/lockdown", { method: "POST", body: { on: false } });
      } else {
        const v = await ask("Lock VoiceGuard down? Every app request stops until you lift it. Sign-in, the Admin Console and this portal keep working.",
          [{ key: "reason", label: "Why", type: "textarea" }], "Lock down");
        if (!v) return;
        await api("/security/lockdown", { method: "POST", body: { on: true, reason: v.reason } });
      }
      render();
    };
    const bad = d.layers.filter((l) => l.state !== "secure");
    const castleBox = h("div", { class: "castle-wrap" });
    const detail = h("div", {});
    const layerList = h("div", { class: "layers" });
    const pick = async (n) => {
      castleLayer = castleLayer === n ? 0 : n;
      castleBox.replaceChildren(drawCastle(d.layers, pick));
      layerList.replaceChildren(...d.layers.map(layerRow));
      detail.replaceChildren();
      if (!castleLayer) return;
      const l = d.layers.find((x) => x.id === castleLayer);
      const box = card(`Layer ${l.id} · ${l.name} – ${l.what}`, h("p", { class: "muted", text: "Guards against: " + l.guards }),
        h("div", { class: "row" }, pill(STATE_WORD[l.state] || l.state, l.state === "secure" ? "good" : l.state === "warning" ? "warn" : "bad"),
          pill(l.open_incidents + " open incident(s)"), pill(l.events_24h + " events in 24 h")),
        h("div", { class: "empty", text: "Loading this layer's events…" }));
      detail.replaceChildren(box);
      try {
        const ev = await api(`/logs/security?layer=${l.id}&limit=40`);
        box.lastChild.replaceWith(table([["When", (e) => fmt(e.at)], ["Severity", (e) => sevPill(e.severity)], ["What", (e) => e.kind.replace(/_/g, " ")],
          ["Detail", (e) => e.detail], ["Address", (e) => e.ip || "—"], ["Page", (e) => e.path || "—"]], ev));
      } catch (e) { box.lastChild.replaceWith(h("p", { class: "error", text: e.message })); }
      detail.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };
    const layerRow = (l) => h("div", { class: "layer " + l.state + (castleLayer === l.id ? " sel" : ""), onclick: () => pick(l.id) },
      h("div", { class: "no", text: l.id }), h("div", {}, h("b", { text: l.name + " · " + l.what }), h("small", { text: l.guards })),
      h("div", { class: "right" }, pill(STATE_WORD[l.state] || l.state, l.state === "secure" ? "good" : l.state === "warning" ? "warn" : "bad"),
        h("small", { text: l.events_24h + " events / 24 h" })));
    castleBox.append(drawCastle(d.layers, pick));
    layerList.append(...d.layers.map(layerRow));
    const integ = d.integrity;
    const changed = [...integ.changed, ...integ.added, ...integ.removed];
    const incRows = d.incidents;
    if (castleLayer) setTimeout(() => { const n = castleLayer; castleLayer = 0; pick(n); }, 0);
    return [
      h("div", { class: "split castle-split" },
        card("", h("div", { class: "castle-head" }, h("h3", { text: "VoiceGuard castle" }),
          d.lockdown ? pill("LOCKDOWN", "bad") : bad.length ? pill(bad.length + " layer(s) need attention", bad.some((l) => l.state === "breached") ? "bad" : "warn") : pill("all 7 layers secure", "good")),
          castleBox, h("p", { class: "muted small", text: "Click a part of the castle to see that layer's events. Red = under attack now, amber = needs a look, stone = secure." })),
        h("div", {},
          card("The seven layers", layerList),
          card("Lockdown", h("p", { text: d.lockdown ? "ON since " + fmt(d.lockdown.at) + ": " + d.lockdown.reason : "Off. VoiceGuard is running normally." }),
            btn(d.lockdown ? "Lift lockdown" : "Lock down now", lock, d.lockdown ? "primary" : "danger solid")))),
      detail,
      h("div", { class: "grid two" },
        card("Server files", h("p", { text: integ.clean ? "All server code, AI models and settings match the approved version." : changed.length + " file(s) differ from the approved version:" }),
          changed.length ? h("ul", {}, changed.slice(0, 10).map((f) => h("li", {}, h("code", { text: f })))) : null,
          h("p", { class: "muted", text: "Approved " + fmt(integ.approved_at) + " by " + (integ.approved_by || "—") })),
        card("Phones", h("p", { text: `${d.phones.total} phone(s) report to the server · ${d.phones.at_risk} at risk (rooted, modified app, debugger…) · app trust mode: ${d.app_trust_mode}` }))),
      card("Open incidents (" + incRows.length + ")", table([
        ["Last", (i) => fmt(i.last_at)], ["Layer", (i) => i.layer, "num"], ["Severity", (i) => sevPill(i.severity)], ["What", (i) => i.title],
        ["Detail", (i) => i.detail], ["Times", (i) => i.count, "num"], ["Action", (i) => i.action || "—"], ["Status", (i) => i.status],
        ["", (i) => h("div", { class: "row" },
          i.status === "open" ? btn("Acknowledge", () => incident(i.id, "acknowledge"), "small") : null,
          btn("Resolve", () => incident(i.id, "resolve"), "small primary"))]], incRows)),
      card("Blocked now (" + d.blocks.length + ")", table([
        ["Kind", (b) => b.kind], ["Value", (b) => b.value], ["Reason", (b) => b.reason], ["Until", (b) => b.until ? fmt(b.until) : "permanent"], ["By", (b) => b.by],
        ["", (b) => btn("Remove", async () => { await api("/security/blocks/" + b.id + "/remove", { method: "POST" }); render(); }, "small")]], d.blocks)),
    ];
  };
  async function incident(id, action) {
    try { await api(`/security/incidents/${id}/${action}`, { method: "POST" }); render(); } catch (e) { toast(e.message, true); }
  }

  // ---- network: how the app works on each operator
  const OP_CLASS = { Jio: "op-jio", Airtel: "op-airtel", Vi: "op-vi", BSNL: "op-bsnl", MTNL: "op-mtnl" };
  VIEWS.network = async () => {
    const days = VIEWS.network.days || 7;
    const d = await api("/network?days=" + days);
    const tabs = h("div", { class: "tabs" }, [[1, "Today"], [7, "7 days"], [30, "30 days"]].map(([k, t]) =>
      h("button", { class: days === k ? "on" : "", text: t, onclick: () => { VIEWS.network.days = k; render(); } })));
    const healthPill = (x) => pill(x, x === "good" ? "good" : x === "fair" ? "warn" : x === "poor" ? "bad" : "");
    const mix = (gens, total) => {
      const order = ["5G", "4G", "3G", "2G", "Wi-Fi", "?"];
      const keys = Object.keys(gens).sort((a, b) => order.indexOf(a) - order.indexOf(b));
      return h("div", {}, h("div", { class: "mix" }, keys.map((k) => h("span", { class: "g-" + k.replace(/[^A-Za-z0-9]/g, ""), title: `${k}: ${gens[k]}`, width: 0 }))),
        h("div", { class: "mix-legend" }, keys.map((k) => h("span", {}, h("i", { class: "g-" + k.replace(/[^A-Za-z0-9]/g, "") }), `${k} ${Math.round(gens[k] / Math.max(1, total) * 100)}%`))));
    };
    const opCard = (o) => {
      const c = h("div", { class: "card op " + (OP_CLASS[o.operator] || "op-other") },
        h("div", { class: "row" }, h("h3", { text: o.operator }), h("span", { class: "spacer" }), healthPill(o.health)),
        h("div", { class: "op-big" }, h("b", { text: o.api_success === null ? "—" : (o.api_success * 100).toFixed(1) + "%" }), h("small", { text: "server calls that worked" })),
        h("div", { class: "metrics" },
          h("div", { class: "metric" }, h("b", { text: `${o.phones}` }), `phones · ${o.active_24h} today`),
          h("div", { class: "metric" }, h("b", { text: o.avg_ms === null ? "—" : o.avg_ms + " ms" }), "average response"),
          h("div", { class: "metric" }, h("b", { text: o.ws_drops_per_phone_day ?? "—" }), "drops / phone / day"),
          h("div", { class: "metric" }, h("b", { text: o.signal === null ? "—" : o.signal + " / 4" }), "signal bars"),
          h("div", { class: "metric" }, h("b", { text: o.downlink_mbps === null ? "—" : o.downlink_mbps + " Mbps" }), "download speed"),
          h("div", { class: "metric" }, h("b", { text: `${o.ai.checks}` }), "AI checks" + (o.ai.avg_seconds ? ` · ${o.ai.avg_seconds} s` : "")),
          h("div", { class: "metric" }, h("b", { text: `${o.calls_screened}` }), "calls screened"),
          h("div", { class: "metric" }, h("b", { text: `${o.spam_warned + o.auto_blocked}` }), "spam warned / blocked"),
          h("div", { class: "metric" }, h("b", { text: `${o.danger_results}` }), "danger results")),
        h("small", { class: "muted", text: "Network mix" }), mix(o.gens, o.phones),
        h("p", { class: "muted small", text: (o.telecom.partner ? `Operator key: ${o.telecom.partner} · ${o.telecom.flags} flags, ${o.telecom.lookups} lookups` : "No operator API key yet") +
          ` · ${o.protected}/${o.phones} fully protected` + (o.roaming ? ` · ${o.roaming} roaming` : "") +
          (o.api_unreachable ? ` · ${o.api_unreachable} calls could not reach the server` : "") }));
      for (const span of c.querySelectorAll(".mix span")) {
        const k = span.title.split(":")[0];
        span.style.width = (o.gens[k] / Math.max(1, o.phones) * 100) + "%";
      }
      return c;
    };
    return [
      h("div", { class: "row" }, tabs, h("span", { class: "spacer" }),
        h("small", { class: "muted", text: "Phones send only operator names, network type, signal bars and how their server calls went. Never phone numbers." })),
      d.operators.length ? h("div", { class: "grid ops" }, d.operators.map(opCard))
        : card("", h("p", { class: "muted", text: "No phone has sent network data yet. Phones with the new app version send it with their next report (when the app starts, reconnects, and every 6 hours)." })),
      card("Phones (" + d.phones.length + ")", table([
        ["Phone", (p) => p.model || "—"], ["User", (p) => p.user], ["Number", (p) => p.phone || "—"], ["Operator", (p) => p.operator],
        ["SIMs", (p) => p.sims.join(" + ") || "—"], ["Network", (p) => p.transport === "wifi" ? "Wi-Fi" : (p.gen || p.transport || "—")],
        ["Signal", (p) => p.signal === null ? "—" : p.signal + "/4"], ["Roaming", (p) => p.roaming ? "yes" : "no"],
        ["Calls worked", (p) => p.api_success === null ? "—" : (p.api_success * 100).toFixed(1) + "%"], ["App", (p) => p.app_version || "—"],
        ["Last report", (p) => fmt(p.updated)]], d.phones)),
    ];
  };

  // ---- daily reports
  const LAYER_NAMES = { 1: "Moat", 2: "Outer wall", 3: "Gatehouse", 4: "Inner wall", 5: "Keep", 6: "Vault", 7: "Royal guard" };
  const kvTable = (obj, label) => table([[label || "", (r) => r[0]], ["Count", (r) => r[1], "num"]], Object.entries(obj || {}));
  VIEWS.reports = async () => {
    const list = await api("/reports");
    const days = [{ ...list.today, live: true }, ...list.days.filter((x) => x.day !== list.today.day)];
    const sel = VIEWS.reports.day || list.today.day;
    const box = h("div", {}, h("div", { class: "empty", text: "Loading…" }));
    const left = card("Days (" + list.tz + ")", h("div", { class: "daylist" }, days.map((x) => h("button", {
      class: "day-btn" + (x.day === sel ? " on" : ""), onclick: () => { VIEWS.reports.day = x.day; render(); } },
      h("b", { text: x.live ? "Today (live)" : x.day }), h("small", {
        text: `${x.headline.incidents} incidents · ${x.headline.evidence} evidence · ${x.headline.ai_checks} AI checks` })))));
    showReport(sel, box, sel === list.today.day);
    return h("div", { class: "split reports-split" }, left, box);
  };
  async function showReport(day, box, live) {
    try {
      const r = await api("/reports/" + day);
      const hd = r.headline;
      const download = async () => {
        try {
          const res = await api(`/reports/${day}/download`, { raw: true });
          const a = h("a", { href: URL.createObjectURL(await res.blob()), download: `voiceguard-report-${day}.html` });
          document.body.append(a); a.click(); a.remove();
          toast("Downloaded (recorded in the audit log).");
        } catch (e) { toast(e.message, true); }
      };
      const parts = [
        card("", h("div", { class: "row" }, h("h3", { text: `Daily report · ${r.day}${live ? " (so far today)" : ""}` }), h("span", { class: "spacer" }),
          btn("Download", download), btn("Print", () => window.print()),
          !live && manager() ? btn("Rebuild", async () => { await api(`/reports/${day}/rebuild`, { method: "POST" }); toast("Rebuilt."); render(); }, "small") : null),
          h("p", { class: "muted small", text: `${r.complete ? "Complete day" : "Day in progress"} · times in ${r.tz} · generated ${fmt(r.generated_at)}` }),
          h("div", { class: "grid" },
            stat("Security incidents", hd.incidents, hd.serious + " serious", hd.serious ? "bad" : hd.incidents ? "warn" : "good"),
            stat("New evidence", hd.evidence), stat("AI checks", hd.ai_checks, hd.danger + " danger · " + hd.ai_voices + " AI voices", hd.danger ? "warn" : ""),
            stat("Scam reports", hd.scam_reports), stat("App crashes", hd.app_crashes, "", hd.app_crashes ? "warn" : ""))),
      ];
      if (r.security) {
        const x = r.security;
        const max = Math.max(1, ...Object.values(x.by_layer));
        parts.push(card("Security",
          h("p", { text: `${x.events} events · ${x.incidents_opened} incidents opened (${x.serious} serious) · ${x.incidents_resolved} resolved · ${x.open_now} open now · ${x.blocks} blocks · ${x.lockdowns} lockdown events · server files ${x.integrity_clean ? "trusted" : "CHANGED"}` }),
          h("div", { class: "hbars" }, Object.entries(x.by_layer).map(([k, v]) => h("div", { class: "hbar" }, h("span", { text: `${k} ${LAYER_NAMES[k] || ""}` }),
            h("div", { class: "track" }, h("i", { width: 0, "data-w": (v / max * 100).toFixed(1) })), h("b", { text: v })))),
          x.incidents.length ? table([["Time", (i) => fmt(i.at)], ["Layer", (i) => i.layer, "num"], ["Severity", (i) => sevPill(i.severity)], ["What", (i) => i.title],
            ["Times", (i) => i.count, "num"], ["Action", (i) => i.action || "—"], ["Status", (i) => i.status]], x.incidents) : h("p", { class: "muted", text: "No incidents." })));
      }
      if (r.evidence) {
        const x = r.evidence;
        parts.push(card("Evidence", h("p", { text: `${x.new} new · ${x.case_updates} case updates · ${x.sent_to_police} sent to police · ${x.closed} closed · ${x.views} views/listens · ${x.open_cases} cases open` }),
          table([["Time", (i) => fmt(i.at)], ["Level", (i) => levelPill(i.level)], ["Score", (i) => i.score ?? "—", "num"], ["Caller", (i) => i.number || "—"], ["Claimed", (i) => i.claimed || "—"]], x.items)));
      }
      if (r.ai) {
        const x = r.ai;
        parts.push(card("AI",
          h("div", { class: "metrics" },
            h("div", { class: "metric" }, h("b", { text: x.checks }), "checks"), h("div", { class: "metric" }, h("b", { text: x.ok }), "finished"),
            h("div", { class: "metric" }, h("b", { text: x.ai_voices }), "AI voices found"),
            h("div", { class: "metric" }, h("b", { text: x.avg_seconds ?? "—" }), "avg seconds"), h("div", { class: "metric" }, h("b", { text: x.p95_seconds ?? "—" }), "95% under (s)"),
            h("div", { class: "metric" }, h("b", { text: x.scam_voice_matches }), "known scammer voices"), h("div", { class: "metric" }, h("b", { text: x.new_scam_voices }), "new scam voices"),
            h("div", { class: "metric" }, h("b", { text: x.phone_quality_share === null ? "—" : Math.round(x.phone_quality_share * 100) + "%" }), "phone-quality audio")),
          h("div", { class: "grid two" }, kvTable(x.by_level, "Result"), kvTable(x.by_source, "Check type"), kvTable(x.made_by, "Voice made by"), kvTable(x.errors, "Problem")),
          x.switches.map((w) => h("p", { text: `Model switched by ${w.by}: ${w.what} (${w.why})` }))));
      }
      if (r.fraud) {
        const x = r.fraud;
        parts.push(card("Fraud & telecom", h("p", { text: `${x.reports} scam reports about ${x.numbers} numbers` }),
          h("div", { class: "grid two" }, kvTable(x.by_source, "Reported by"), kvTable(x.telecom_flags, "Operator flags"),
            kvTable(Object.fromEntries(x.top_numbers), "Top numbers"), kvTable(x.operator_calls, "Operator API calls"))));
      }
      if (r.network) {
        parts.push(card("Phones by operator", table([["Operator", (o) => o[0]], ["Phones", (o) => o[1].phones, "num"],
          ["Server calls OK", (o) => o[1].api_success === null ? "—" : (o[1].api_success * 100).toFixed(1) + "%"], ["Avg ms", (o) => o[1].avg_ms ?? "—", "num"],
          ["Connection drops", (o) => o[1].ws_drops, "num"], ["Calls screened", (o) => o[1].calls_screened, "num"], ["AI checks", (o) => o[1].ai_checks, "num"]], Object.entries(r.network))));
      }
      if (r.team) parts.push(card("Team portal", kvTable(r.team, "")));
      box.replaceChildren(...parts);
      for (const i of box.querySelectorAll(".track i")) i.style.width = i.dataset.w + "%";
    } catch (e) { box.replaceChildren(card("", h("p", { class: "error", text: e.message }))); }
  }

  // ---- logs
  VIEWS.logs = async () => {
    const tabsAll = [["server", "Server log", "security"], ["security", "Security events", "security"], ["ai", "AI checks", "ai"], ["evidence", "Evidence activity", "evidence"]]
      .filter((t) => me.can.includes(t[2]));
    if (!tabsAll.length) return card("", h("p", { class: "muted", text: "Your role has no logs." }));
    const cur = tabsAll.some((t) => t[0] === VIEWS.logs.tab) ? VIEWS.logs.tab : tabsAll[0][0];
    const f = VIEWS.logs.f || (VIEWS.logs.f = {});
    const tabs = h("div", { class: "tabs" }, tabsAll.map(([k, t]) => h("button", { class: cur === k ? "on" : "", text: t, onclick: () => { VIEWS.logs.tab = k; render(); } })));
    const input = (key, ph) => { const el = h("input", { placeholder: ph, value: f[key] || "" }); el.addEventListener("change", () => { f[key] = el.value.trim(); render(); }); return el; };
    const select = (key, opts) => { const el = h("select", {}, opts.map(([v, t]) => h("option", { value: v, text: t }))); el.value = f[key] || ""; el.addEventListener("change", () => { f[key] = el.value; render(); }); return el; };
    if (cur === "server") {
      const r = await api(`/logs/server?lines=${f.lines || 300}&q=${encodeURIComponent(f.q || "")}`);
      return [tabs, card("", h("div", { class: "row" }, input("q", "Filter (e.g. error, security, analyze)"),
        select("lines", [["300", "last 300 lines"], ["1000", "last 1000 lines"], ["2000", "last 2000 lines"]]))),
        card("Server log", h("pre", { class: "code log", text: r.lines.join("\n") || "(empty)" }))];
    }
    if (cur === "security") {
      const r = await api(`/logs/security?layer=${f.layer || 0}&severity=${f.sev || ""}&q=${encodeURIComponent(f.sq || "")}`);
      return [tabs, card("", h("div", { class: "row" },
        select("layer", [["", "All layers"], ...Object.entries(LAYER_NAMES).map(([k, v]) => [k, `Layer ${k} ${v}`])]),
        select("sev", [["", "All severities"], ["critical", "critical"], ["high", "high"], ["medium", "medium"], ["low", "low"]]), input("sq", "Search kind, detail or address"))),
        card("Security events (" + r.length + ")", table([["When", (e) => fmt(e.at)], ["Layer", (e) => `${e.layer} ${LAYER_NAMES[e.layer] || ""}`], ["Severity", (e) => sevPill(e.severity)],
          ["What", (e) => e.kind.replace(/_/g, " ")], ["Detail", (e) => e.detail], ["Address", (e) => e.ip || "—"], ["Page", (e) => e.path || "—"]], r))];
    }
    if (cur === "ai") {
      const r = await api(`/logs/ai?source=${f.src || ""}&level=${f.lvl || ""}&problems=${f.prob === "1"}`);
      return [tabs, card("", h("div", { class: "row" },
        select("src", [["", "All check types"], ["voice_note", "Voice notes"], ["live_call", "Live calls"], ["demo_call", "Demo calls"], ["hd_call", "HD calls"], ["portal_test", "Portal tests"]]),
        select("lvl", [["", "All results"], ["danger", "danger"], ["suspicious", "suspicious"], ["safe", "safe"]]),
        select("prob", [["", "Finished and failed"], ["1", "Only problems"]]))),
        card("AI checks (" + r.length + ")", table([["When", (c) => fmt(c.at)], ["Type", (c) => c.source], ["Result", (c) => c.ok ? levelPill(c.level) : pill(c.error || "failed", "bad")],
          ["Score", (c) => c.score ?? "—", "num"], ["AI voice", (c) => c.fake_prob === null ? "—" : Math.round(c.fake_prob * 100) + "%", "num"],
          ["Made by", (c) => (c.made_by || "—").replace(/_/g, " ")], ["Seconds", (c) => c.seconds ?? "—", "num"], ["Audio", (c) => c.narrowband ? "phone" : "clear"],
          ["Scam voice", (c) => c.scam_voice ? pill("match", "bad") : "—"], ["User", (c) => c.user]], r))];
    }
    const r = await api("/logs/evidence");
    return [tabs, card("Evidence activity (" + r.length + ")", table([["When", (x) => fmt(x.at)], ["Who", (x) => x.who], ["What", (x) => x.what],
      ["Evidence", (x) => x.item || "—"], ["Detail", (x) => x.detail || "—"]], r))];
  };

  // ---- team
  VIEWS.team = async () => {
    const d = await api("/team");
    const roleOpts = d.roles.filter((r) => me.role === "owner" || !["owner", "admin"].includes(r.key)).map((r) => [r.key, r.name]);
    const add = async () => {
      const v = await ask("Add a team member", [{ key: "name", label: "Name" }, { key: "phone", label: "Phone number (they sign in with it)" },
        { key: "role", label: "Role", options: roleOpts, value: "viewer" }], "Add member");
      if (!v) return;
      try { await api("/team", { method: "POST", body: v }); toast("Added. They can sign in at " + location.origin + "/portal and set up their authenticator."); render(); }
      catch (e) { toast(e.message, true); }
    };
    const act = async (m, action, question) => {
      if (question && !(await ask(question, [], "Yes"))) return;
      try { await api(`/team/${m.id}/${action}`, { method: "POST" }); toast("Done."); render(); } catch (e) { toast(e.message, true); }
    };
    const edit = async (m) => {
      const v = await ask("Change " + m.name, [{ key: "name", label: "Name", value: m.name },
        { key: "role", label: "Role", options: roleOpts.some((o) => o[0] === m.role) ? roleOpts : [[m.role, m.role_name], ...roleOpts], value: m.role },
        { key: "active", label: "Account", options: [["1", "Active"], ["0", "Switched off"]], value: m.active ? "1" : "0" }], "Save");
      if (!v) return;
      const body = { name: v.name };
      if (v.role !== m.role) body.role = v.role;
      if ((v.active === "1") !== m.active) body.active = v.active === "1";
      try { await api("/team/" + m.id, { method: "POST", body }); toast("Saved."); render(); } catch (e) { toast(e.message, true); }
    };
    return [
      card("Team members", h("div", { class: "row" }, btn("Add team member", add, "primary")), table([
        ["Name", (m) => m.name], ["Phone", (m) => m.phone], ["Role", (m) => pill(m.role_name, m.role === "owner" ? "info" : "")],
        ["Status", (m) => !m.active ? pill("off") : m.locked ? pill("locked", "bad") : !m.two_step ? pill("setup pending", "warn") : pill("active", "good")],
        ["Last sign-in", (m) => fmt(m.last_login)], ["Sessions", (m) => m.sessions, "num"],
        ["", (m) => h("div", { class: "row" },
          btn("Change", () => edit(m), "small"),
          m.locked ? btn("Unlock", () => act(m, "unlock"), "small") : null,
          btn("Reset authenticator", () => act(m, "reset-2fa", "Reset " + m.name + "'s authenticator? They will set it up again at the next sign-in (use this for a lost phone)."), "small"),
          m.sessions ? btn("Sign out everywhere", () => act(m, "sign-out"), "small danger") : null)]], d.members)),
      card("What each role can open", table([["Role", (r) => r.name], ["Sections", (r) => r.can.join(", ")],
        ["Sees users' phone numbers", (r) => r.sees_phone_numbers ? "yes" : "no (hidden)"]], d.roles)),
    ];
  };

  // ---- audit
  VIEWS.audit = async () => {
    const q = h("input", { placeholder: "Search member, action or item", value: VIEWS.audit.q || "" });
    const go2 = () => { VIEWS.audit.q = q.value.trim(); render(); };
    q.addEventListener("keydown", (e) => { if (e.key === "Enter") go2(); });
    const rows = await api("/audit?q=" + encodeURIComponent(VIEWS.audit.q || ""));
    return [card("", h("div", { class: "row" }, q, btn("Search", go2))),
      card("Everything team members did in the portal", table([["When", (r) => fmt(r.at)], ["Member", (r) => r.member || "—"], ["Action", (r) => r.action],
        ["Item", (r) => r.target || "—"], ["Detail", (r) => r.detail || "—"], ["Address", (r) => r.ip]], rows))];
  };

  // ---- sessions
  VIEWS.sessions = async () => {
    const rows = await api("/sessions");
    return [card("Signed-in sessions" + (me.can.includes("team") ? " (whole team)" : ""), h("p", { class: "muted", text: `Sessions end after ${me.idle_minutes} minutes without activity, and always at ${fmt(me.session_ends)} for this one.` }),
      table([["Member", (r) => r.member], ["Started", (r) => fmt(r.started)], ["Last used", (r) => fmt(r.last_used)], ["Address", (r) => r.ip],
        ["Browser", (r) => (r.agent || "").slice(0, 60)], ["", (r) => r.this ? pill("this one", "info") : btn("End", async () => {
          await api("/sessions/" + r.id + "/end", { method: "POST" }); render();
        }, "small danger")]], rows))];
  };

  // ---------------------------------------------------------------- go
  if (token) start(token); else showLogin("phone");
})();
