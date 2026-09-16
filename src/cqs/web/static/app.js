"use strict";

let WORK = null;
let META = { kinds: {}, verifications: {} };

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "show";
  t.style.background = isErr ? "var(--bad)" : "var(--fg)";
  t.style.color = "#fff";
  setTimeout(() => (t.className = ""), 3600);
}

async function api(path, opts) {
  const r = await fetch(path, opts);
  const d = await r.json().catch(() => ({ error: "応答を解釈できません" }));
  if (!r.ok || d.error) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}
const get = (name, params = {}) => {
  const q = new URLSearchParams({ ...(WORK ? { work: WORK } : {}), ...params });
  return api(`/api/${name}?${q}`);
};
const post = (name, body) =>
  api(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...(WORK ? { work: WORK } : {}), ...body }),
  });

// --- 作品タブ ---------------------------------------------------------------
async function loadWorks() {
  const { works } = await get("works");
  const box = $("works");
  box.innerHTML = "";
  if (!works.length) {
    box.innerHTML = '<span class="muted">作品がありません。「管理」タブで作成してください。</span>';
    return;
  }
  if (!WORK || !works.some((w) => w.file_slug === WORK)) WORK = works[0].file_slug;
  for (const w of works) {
    const b = document.createElement("button");
    b.textContent = w.error ? `${w.file_slug}（読込失敗）` : w.title;
    b.className = w.file_slug === WORK ? "active" : "";
    b.onclick = () => { WORK = w.file_slug; loadWorks(); refreshTab(); };
    box.appendChild(b);
  }
  const w = works.find((x) => x.file_slug === WORK);
  $("workstats").textContent = w && !w.error
    ? `出典 ${w.sources} / 版 ${w.versions} / 主張 ${w.claims_active} / エンティティ ${w.entities} / 未照合候補 ${w.candidates_pending}`
    : "";
  document.title = w && !w.error ? `${w.title} — 作品QAシステム` : "作品QAシステム";
  updateAskPlaceholder();
}

// --- 操作タブ ---------------------------------------------------------------
function currentTab() {
  return document.querySelector("#tabs button.active").dataset.tab;
}
for (const b of document.querySelectorAll("#tabs button")) {
  b.onclick = () => {
    document.querySelectorAll("#tabs button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("tab-" + b.dataset.tab).classList.add("active");
    refreshTab();
  };
}
function refreshTab() {
  const t = currentTab();
  if (t === "manage") { loadSourceList(); loadEntities(); }
  if (t === "candidates") loadCandidates();
}

// --- 表示部品 ---------------------------------------------------------------
function claimCard(c) {
  const ents = c.entities?.length ? ` / 対象: ${esc(c.entities.join(", "))}` : "";
  const contra = c.contradicts?.length ? ` <span class="badge v-needs_recheck">矛盾: claim ${c.contradicts.join(", ")}</span>` : "";
  const src = c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noreferrer">${esc(c.url)}</a>` : esc(c.source_title || "出典なし");
  return `<div class="card">
    <span class="badge v-${esc(c.verification)}">${esc(c.verification_label)}</span>
    <span class="badge">${esc(c.kind_label)}</span>${contra}
    <div>${esc(c.text)}</div>
    <div class="meta">claim ${c.id}${ents} / ${src}
      ${c.source_version_id ? `/ <a href="#" onclick="showSource(${c.source_version_id});return false">原文を見る</a>` : ""}
      / <a href="#" onclick="markRecheck(${c.id});return false">要再確認にする</a></div>
    <div class="meta">${esc(c.handling || "")}</div>
  </div>`;
}

function sourceCard(s) {
  const title = esc(s.source_title || s.title || "");
  const url = s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noreferrer">${esc(s.url)}</a>` : "";
  const warn = s.citable === false ? '<span class="badge v-unverified">根拠にできない出典</span>' : "";
  return `<div class="card">
    <span class="badge">${esc(s.kind_label)}</span>${warn} ${title}
    <div class="excerpt">…${esc(s.excerpt)}…</div>
    <div class="meta">source_version ${s.source_version_id} ${url}
      / <a href="#" onclick="showSource(${s.source_version_id});return false">原文を見る</a>
      / <a href="#" onclick="claimForm(${s.source_version_id});return false">主張として登録</a></div>
    <div id="cf-${s.source_version_id}"></div>
  </div>`;
}

window.showSource = async (id) => {
  const d = await get("source", { id, length: 4000 });
  const w = window.open("", "_blank");
  w.document.write(
    `<meta charset="utf-8"><title>${esc(d.source_title || "")}</title>` +
    `<body style="font:14px/1.8 system-ui;max-width:70ch;margin:24px auto;padding:0 16px;white-space:pre-wrap">` +
    `<b>${esc(d.source_title || d.title || "")}</b> [${esc(d.kind_label)}] v${d.version_no}<br>` +
    `<a href="${esc(d.url || "")}">${esc(d.url || "")}</a><br>取得 ${esc(d.fetched_at)} / 全${d.total_length}字<hr>` +
    esc(d.excerpt) + (d.has_more ? "\n\n…（以降省略）" : "") + `</body>`
  );
};

window.markRecheck = async (id) => {
  await post("claim_verification", { claim_id: id, verification: "needs_recheck" });
  toast(`claim ${id} を要再確認にしました`);
  doSearch();
};

window.claimForm = (svid) => {
  const box = $("cf-" + svid);
  if (box.innerHTML) { box.innerHTML = ""; return; }
  box.innerHTML = `<form onsubmit="submitClaim(event, ${svid})" style="margin-top:8px">
    <textarea name="text" rows="2" placeholder="主張の本文" required></textarea>
    <div class="inline">
      <input name="entities" placeholder="対象エンティティ（カンマ区切り）" style="flex:1">
      <select name="verification"><option value="">出典種別から自動</option>
        ${Object.entries(META.verifications).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}
      </select>
      <button type="submit">登録</button>
    </div></form>`;
};

window.submitClaim = async (ev, svid) => {
  ev.preventDefault();
  const f = ev.target;
  try {
    const d = await post("claim", {
      text: f.text.value,
      source_version_id: svid,
      verification: f.verification.value || null,
      entities: f.entities.value.split(",").map((s) => s.trim()).filter(Boolean),
    });
    toast(`claim ${d.claim_id} を登録しました`);
    f.closest("div").innerHTML = "";
    doSearch();
    loadWorks();
  } catch (e) { toast(e.message, true); }
};

// --- 質問 -------------------------------------------------------------------
$("askForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const q = $("askQ").value.trim();
  if (!q) return;
  $("askOut").innerHTML = '<p class="muted">検索中…</p>';
  try {
    const d = await post("ask", { question: q, use_llm: $("askLLM").checked });
    const ctx = d.context;
    let html = "";
    if (ctx.targets && ctx.targets.length)
      html += `<p class="muted">対象ごとに分けて調べました: ${esc(ctx.targets.map(
        t => `${t}（${(ctx.claims_per_target || {})[t] || 0}件）`).join("、"))}</p>`;
    if (d.answer) html += `<div class="answer">${esc(d.answer)}</div><p class="muted">${esc(d.model)} / 主張${ctx.claims.length}件を参照</p>`;
    else html += `<p class="muted">${esc(d.reason || "")}</p>
      <details open><summary>AIチャットに貼るプロンプト（このまま貼れば同じ答えになります）</summary><pre class="prompt">${esc(d.prompt.system)}\n\n---\n\n${esc(d.prompt.user)}</pre></details>`;
    if (d.follow_ups && d.follow_ups.length)
      html += `<h2>次に訊けること</h2><div class="chips">` + d.follow_ups.map(
        x => `<button class="chip next-q" type="button">${esc(x)}</button>`).join("") + `</div>`;
    html += `<h2>参照した主張（${ctx.claims.length}件）</h2>` + (ctx.claims.map(claimCard).join("") || '<p class="muted">該当なし</p>');
    html += `<h2>原文層の該当箇所</h2>` + (ctx.sources.map(sourceCard).join("") || '<p class="muted">該当なし</p>');
    $("askOut").innerHTML = html;
    for (const b of $("askOut").querySelectorAll(".next-q"))
      b.onclick = () => { $("askQ").value = b.textContent; $("askForm").requestSubmit(); };
  } catch (e) { $("askOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};

// --- 検索 -------------------------------------------------------------------
async function doSearch() {
  const q = $("searchQ").value.trim();
  const kind = $("searchKind").value, verification = $("searchVerif").value;
  try {
    const [c, s] = await Promise.all([
      get("claims", { query: q, kind, verification, limit: 50 }),
      q ? get("sources", { query: q, kind, limit: 15 }) : Promise.resolve({ results: [] }),
    ]);
    $("claimOut").innerHTML = c.claims.map(claimCard).join("") || '<p class="muted">該当なし</p>';
    $("srcOut").innerHTML = s.results.map(sourceCard).join("") || '<p class="muted">該当なし</p>';
  } catch (e) { toast(e.message, true); }
}
$("searchForm").onsubmit = (ev) => { ev.preventDefault(); doSearch(); };

// --- 直接入力 ---------------------------------------------------------------
$("docForm").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    const d = await post("document", {
      title: $("docTitle").value, kind: $("docKind").value,
      url: $("docUrl").value || null, text: $("docText").value,
    });
    $("inputOut").innerHTML = `<div class="card">取り込みました: source_version ${d.source_version_id}（${d.text_length}字）
      <div class="meta"><a href="#" onclick="proposeFrom(${d.source_version_id});return false">この版から主張候補を切り出す</a></div>
      <div id="pp-${d.source_version_id}"></div></div>`;
    $("docText").value = "";
    loadWorks();
  } catch (e) { toast(e.message, true); }
};

$("repForm").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    const d = await post("report", { title: $("repTitle").value, text: $("repText").value });
    $("inputOut").innerHTML = `<div class="card">調査報告を取り込みました: source_version ${d.source_version_id}<br>
      候補 ${d.candidates} 件（うちURL付き ${d.with_url} 件）。「候補の照合」タブで確認・照合してください。</div>`;
    $("repText").value = "";
    loadWorks();
  } catch (e) { toast(e.message, true); }
};

$("fetchForm").onsubmit = async (ev) => {
  ev.preventDefault();
  const url = $("fetchUrl").value.trim();
  if (!url) return;
  $("inputOut").innerHTML = '<p class="muted">取得中…</p>';
  try {
    const d = await post("fetch", { url, kind: $("fetchKind").value || null });
    $("inputOut").innerHTML = `<div class="card">[${esc(d.kind_label)}] ${esc(d.title || "")} v${d.version_no}
      ${d.changed ? "（新しい版）" : "（変化なし）"} ${d.text_length}字
      ${d.rechecked_claims ? `<br>本文が変化したため ${d.rechecked_claims} 件の主張を要再確認にしました` : ""}
      <div class="meta">source_version ${d.source_version_id} /
        <a href="#" onclick="showSource(${d.source_version_id});return false">原文を見る</a> /
        <a href="#" onclick="proposeFrom(${d.source_version_id});return false">主張候補を切り出す</a></div>
      <div id="pp-${d.source_version_id}"></div></div>`;
    loadWorks();
  } catch (e) { $("inputOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};

window.proposeFrom = async (svid) => {
  const box = $("pp-" + svid);
  box.innerHTML = '<p class="muted">切り出し中…</p>';
  try {
    const d = await post("propose", { source_version_id: svid });
    if (!d.candidates.length) { box.innerHTML = '<p class="muted">候補なし（エンティティを登録すると絞り込めます）</p>'; return; }
    box.innerHTML = d.candidates.map((c, i) => `<div class="card">
      <div>${esc(c.text)}</div>
      <div class="meta">${c.entities.length ? "対象候補: " + esc(c.entities.join(", ")) : ""}
        / <a href="#" onclick="quickClaim(${svid}, ${i});return false">これを主張として登録</a></div>
      <span id="qc-${svid}-${i}" hidden>${esc(JSON.stringify(c))}</span></div>`).join("");
  } catch (e) { box.innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};

window.quickClaim = async (svid, i) => {
  const c = JSON.parse($(`qc-${svid}-${i}`).textContent);
  try {
    const d = await post("claim", { text: c.text, source_version_id: svid, entities: c.entities, locator: c.text.slice(0, 80) });
    toast(`claim ${d.claim_id} を登録しました`);
    loadWorks();
  } catch (e) { toast(e.message, true); }
};

// --- 候補 -------------------------------------------------------------------
async function loadCandidates() {
  try {
    const d = await get("candidates", { status: "pending" });
    $("candOut").innerHTML = d.candidates.length
      ? `<table><tr><th>ID</th><th>主張</th><th>URL</th><th>メモ</th></tr>` +
        d.candidates.map((c) => `<tr><td>${c.id}</td><td>${esc(c.claim_text)}</td>
          <td>${c.url ? `<a href="${esc(c.url)}" target="_blank" rel="noreferrer">${esc(c.url)}</a>` : '<span class="muted">なし</span>'}</td>
          <td class="muted">${esc(c.note || "")}</td></tr>`).join("") + "</table>"
      : '<p class="muted">未照合の候補はありません。</p>';
  } catch (e) { $("candOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
$("candReload").onclick = loadCandidates;
$("candVerify").onclick = async () => {
  $("candOut").innerHTML = '<p class="muted">URLを取得して照合中…（1件ずつ間隔を空けます）</p>';
  try {
    const d = await post("verify", {
      limit: Number($("candLimit").value),
      promote: $("candPromote").checked ? Number($("candThreshold").value) : null,
    });
    $("candOut").innerHTML = d.results.map((r) => r.error
      ? `<div class="card err">候補${r.candidate_id}: ${esc(r.error)}</div>`
      : `<div class="card">
          <span class="badge">一致 ${Math.round(r.score * 100)}%</span>
          <span class="badge">${esc(r.kind_label)}</span>
          ${r.promoted ? `<span class="badge v-official">claim ${r.claim_id} として登録</span>` : '<span class="badge v-unverified">未登録</span>'}
          <div>${esc(r.claim_text)}</div>
          <div class="meta"><a href="${esc(r.url)}" target="_blank" rel="noreferrer">${esc(r.url)}</a>
            / <a href="#" onclick="showSource(${r.source_version_id});return false">取得した原文を見る</a>
            ${r.promoted ? "" : `/ <a href="#" onclick="claimForm(${r.source_version_id});return false">主張として登録</a>`}</div>
          ${r.missing?.length ? `<div class="meta">ページに無い語: ${esc(r.missing.join(", "))}</div>` : ""}
          <div id="cf-${r.source_version_id}"></div></div>`).join("") || '<p class="muted">対象なし</p>';
    loadWorks();
  } catch (e) { $("candOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};

// --- 管理 -------------------------------------------------------------------
async function loadSourceList() {
  try {
    const d = await get("sources");
    $("srcListOut").innerHTML = d.sources.length
      ? `<table><tr><th>ID</th><th>種別</th><th>版</th><th>タイトル / URL</th><th>最終取得</th></tr>` +
        d.sources.map((s) => `<tr><td>${s.id}</td><td>${esc(s.kind_label)}</td><td>${s.versions}</td>
          <td>${esc(s.title || "")}<br><span class="muted">${esc(s.url || "")}</span></td>
          <td class="muted">${esc((s.last_fetched || "").slice(0, 16))}</td></tr>`).join("") + "</table>"
      : '<p class="muted">出典がありません。</p>';
  } catch (e) { $("srcListOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
$("refetchBtn").onclick = async () => {
  $("manageOut").innerHTML = '<p class="muted">再取得中…</p>';
  try {
    const d = await post("refetch", {});
    const ch = d.results.filter((r) => r.changed);
    $("manageOut").innerHTML = `<div class="card">${d.results.length}件を再取得し、${ch.length}件で本文が変化しました。` +
      ch.map((r) => `<br>更新: ${esc(r.url)} → v${r.version_no}（要再確認 ${r.rechecked_claims || 0}件）`).join("") +
      d.results.filter((r) => r.error).map((r) => `<br><span class="err">× ${esc(r.url)}: ${esc(r.error)}</span>`).join("") + "</div>";
    loadSourceList(); loadWorks();
  } catch (e) { $("manageOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
};

async function loadEntities() {
  try {
    const d = await get("entities");
    $("entOut").innerHTML = d.entities.length
      ? `<table><tr><th>名前</th><th>種別</th><th>主張</th><th>別名</th></tr>` +
        d.entities.map((e) => `<tr><td><a href="#" onclick="searchEntity('${esc(e.name)}');return false">${esc(e.name)}</a></td>
          <td class="muted">${esc(e.kind || "")}</td><td>${e.claims}</td>
          <td class="muted">${esc((e.aliases || []).join(", "))}</td></tr>`).join("") + "</table>"
      : '<p class="muted">エンティティがありません。</p>';
  } catch (e) { $("entOut").innerHTML = `<p class="err">${esc(e.message)}</p>`; }
}
window.searchEntity = (name) => {
  document.querySelector('#tabs button[data-tab="search"]').click();
  $("searchQ").value = name;
  doSearch();
};
$("entForm").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    await post("entity", {
      name: $("entName").value, kind: $("entKind").value || null,
      aliases: $("entAlias").value.split(",").map((s) => s.trim()).filter(Boolean),
    });
    $("entName").value = ""; $("entAlias").value = "";
    loadEntities(); loadWorks();
  } catch (e) { toast(e.message, true); }
};
$("workForm").onsubmit = async (ev) => {
  ev.preventDefault();
  try {
    const d = await post("work", { title: $("workTitle").value, slug: $("workSlug").value || null });
    toast(`作成しました: ${d.slug}`);
    $("workTitle").value = ""; $("workSlug").value = "";
    WORK = d.slug;
    loadWorks();
  } catch (e) { toast(e.message, true); }
};

// --- 初期化 -----------------------------------------------------------------
(async () => {
  META = await api("/api/meta");
  const kindOpts = Object.entries(META.kinds).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  $("searchKind").innerHTML = '<option value="">出典種別すべて</option>' + kindOpts;
  $("fetchKind").innerHTML = '<option value="">種別を自動判定</option>' + kindOpts;
  $("docKind").innerHTML = kindOpts;
  $("docKind").value = "manual";
  $("searchVerif").innerHTML = '<option value="">確認状態すべて</option>' +
    Object.entries(META.verifications).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  await loadWorks();
})();
