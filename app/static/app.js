/* Kinetic Captions UI — vanilla JS, no dependencies. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const form = $("form");
  const STORE = "kc.settings.v1";
  const AUDIO_EXT = ["mp3", "wav", "m4a", "aac"];
  const VIDEO_EXT = ["mp4", "mov", "webm"];
  const STAGE_LABEL = { prepare: "準備", transcribe: "文字おこし", matte: "人物検出", layout: "演出", render: "書き出し" };
  const PRESET_LOOK = {
    motiversity: { weight: 900, color: null, size: 1.0, pos: "center" },
    hormozi: { weight: 900, color: "#FACC15", size: 0.85, pos: "center" },
    clean: { weight: 600, color: null, size: 0.55, pos: "end" },
  };
  const FONT_FAMILY = { montserrat: "'KC Montserrat'", anton: "'KC Anton'", bebas: "'KC Bebas Neue'", courgette: "'KC Courgette'" };
  const FONT_CASE = { courgette: "lower" };
  const LOADED = new Set();
  const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function registerFace(f) {
    FONT_FAMILY[f.id] = `'${f.family}'`;
    if (f.case) FONT_CASE[f.id] = f.case;
    if (!f.url || LOADED.has(f.id)) return;
    LOADED.add(f.id);
    const face = new FontFace(f.family, `url("${f.url}")`);
    face.load().then((ff) => { document.fonts.add(ff); }).catch(() => {});
  }

  let file = null;        // server info for uploaded file
  let localURL = null;    // object URL for local source preview
  let jobId = null, pollTimer = null, tickTimer = null, startedAt = 0;
  let busy = false, hasResult = false;

  // ------------------------------------------------------------ settings
  const DEFAULTS = {
    preset: "motiversity", font: "montserrat", text_color: "#ffffff", accent_auto: true,
    accent_color: "#facc15", size: "1.0", position: "auto", behind: true, bg_video: "keep",
    bg_audio: "black", bg_custom: "#1e1b4b", canvas: "9:16", language: "", model: "small",
  };

  function radio(name) { const el = form.querySelector(`input[name="${name}"]:checked`); return el ? el.value : null; }
  function setRadio(name, v) { const el = form.querySelector(`input[name="${name}"][value="${CSS.escape(String(v))}"]`); if (el) el.checked = true; }

  function readForm() {
    return {
      preset: radio("preset"), font: radio("font"), text_color: $("text_color").value,
      accent_auto: $("accent_auto").checked, accent_color: $("accent_color").value,
      size: $("size").value, position: radio("position"), behind: $("behind").checked,
      bg_video: radio("bg_video"), bg_audio: radio("bg_audio"), bg_custom: $("bg_custom").value,
      canvas: radio("canvas"), language: radio("language"), model: radio("model"),
    };
  }

  function writeForm(s) {
    s = { ...DEFAULTS, ...s };
    ["preset", "font", "position", "bg_video", "bg_audio", "canvas", "language", "model"].forEach((k) => setRadio(k, s[k]));
    $("text_color").value = s.text_color; $("accent_auto").checked = !!s.accent_auto;
    $("accent_color").value = s.accent_color; $("size").value = s.size; $("behind").checked = !!s.behind;
    $("bg_custom").value = s.bg_custom;
    syncDerived();
  }

  function toOpts(s, kind) {
    const o = {
      preset: s.preset, font: s.font, text_color: s.text_color,
      accent_color: s.accent_auto ? null : s.accent_color, size: parseFloat(s.size),
      position: s.position, language: s.language || null, model: s.model,
    };
    if (kind === "video") { o.behind = s.behind; o.background = s.bg_video; }
    else { o.behind = false; o.canvas = s.canvas; o.background = s.bg_audio === "custom" ? s.bg_custom : s.bg_audio; }
    return o;
  }

  function save() { try { localStorage.setItem(STORE, JSON.stringify(readForm())); } catch (_) { /* private mode */ } }
  function load() { try { return JSON.parse(localStorage.getItem(STORE) || "{}"); } catch (_) { return {}; } }

  function syncDerived() {
    const s = readForm();
    $("accent_color").disabled = s.accent_auto;
    $("size_out").textContent = parseFloat(s.size).toFixed(1) + "×";
    $("text_color_hex").textContent = s.text_color.toUpperCase();
    $("bg_custom_hex").textContent = s.bg_custom.toUpperCase();
    $("bg_custom_wrap").hidden = s.bg_audio !== "custom";
    updatePlaceholder();
  }

  // ------------------------------------------------------------ fonts
  async function loadFonts() {
    try {
      const r = await fetch("/api/fonts");
      if (!r.ok) return;
      const { fonts } = await r.json();
      if (!fonts || !fonts.length) return;
      const cur = radio("font") || "montserrat";
      const box = $("fonts");
      box.innerHTML = "";
      fonts.forEach((f) => {
        registerFace(f);
        const lab = document.createElement("label");
        lab.className = "font-opt";
        const weight = f.id === "montserrat" ? 900 : 400;
        lab.innerHTML = `<input type="radio" name="font" value="${esc(f.id)}"><span class="f-sample" style="font-family:'${esc(f.family)}';font-weight:${weight}">${esc(f.sample || "NEVER GIVE UP")}</span><span class="f-name">${esc(f.label)}</span>`;
        box.appendChild(lab);
      });
      setRadio("font", cur);
      if (!radio("font")) box.querySelector("input").checked = true;
    } catch (_) { /* static fallback stays */ }
  }

  // ------------------------------------------------------------ UI helpers
  function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const m = Math.floor(sec / 60), s = sec % 60;
    return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  }
  function fmtSize(b) { return b > 1e9 ? (b / 1e9).toFixed(1) + " GB" : (b / 1e6).toFixed(1) + " MB"; }
  function announce(t) { $("announce").textContent = t; }

  function showError(msg, hint, detail) {
    $("error-msg").textContent = msg;
    $("error-hint").textContent = hint || "";
    $("error-hint").hidden = !hint;
    $("error-detail").textContent = detail || "";
    $("error-detail-wrap").hidden = !detail;
    $("error").hidden = false;
  }
  function clearError() { $("error").hidden = true; }

  function setAspect(w, h) {
    const wrap = $("player-wrap");
    wrap.style.setProperty("--ar", `${w} / ${h}`);
    wrap.style.setProperty("--arn", (w / h).toFixed(4));
  }

  function applyKind(kind) {
    form.querySelectorAll("[data-only]").forEach((el) => { el.hidden = el.dataset.only !== kind; });
    $("type-hint").hidden = !!kind;
  }

  function updatePlaceholder() {
    const ph = $("placeholder"), t = $("ph-text");
    const s = readForm();
    const look = PRESET_LOOK[s.preset] || PRESET_LOOK.motiversity;
    t.style.fontFamily = `${FONT_FAMILY[s.font] || FONT_FAMILY.montserrat}, var(--font-ui)`;
    t.style.fontWeight = s.font === "montserrat" ? look.weight : 400;
    t.style.color = s.text_color;
    t.style.fontSize = `calc(clamp(18px, 4vw, 34px) * ${look.size * parseFloat(s.size)})`;
    t.innerHTML = s.preset === "hormozi"
      ? `NEVER <span style="color:${s.accent_auto ? look.color : s.accent_color}">GIVE</span> UP`
      : s.preset === "clean" ? "never give up" : "NEVER GIVE UP";
    if (FONT_CASE[s.font] === "lower") t.innerHTML = t.innerHTML.replace(/>?[^<>]+/g, (m) => m.toLowerCase());
    ph.style.alignItems = s.position === "top" ? "start" : s.position === "bottom" || (s.position === "auto" && look.pos === "end") ? "end" : "center";
    ph.style.padding = "12% 0";
    if (file && file.kind === "audio" && !hasResult) {
      const bg = s.bg_audio === "custom" ? s.bg_custom : s.bg_audio === "white" ? "#ffffff" : "#000000";
      ph.style.setProperty("--ph-bg", bg);
      if (s.text_color.toLowerCase() === "#ffffff" && bg === "#ffffff") t.style.color = "#09090b";
      const [w, h] = s.canvas.split(":").map(Number);
      setAspect(w, h);
    }
  }

  function updateCTA() {
    $("cta").disabled = !file || busy;
    $("drop").disabled = busy;
    $("change-file").disabled = busy;
    $("cta-label").textContent = busy ? "作成中…" : hasResult ? "この設定で作り直す" : "字幕動画を作る";
    $("cta-hint").textContent = !file ? "まずはファイルを選んでください"
      : busy ? "完成まで、このページを開いたままにしてください"
      : hasResult ? "2回目からは文字おこしを再利用するので速く終わります"
      : "設定はそのままでもOK。ボタンを押すだけです";
  }

  // ------------------------------------------------------------ upload
  function pickFile() { if (!busy) $("file").click(); }

  function handleFile(f) {
    if (!f || busy) return;
    clearError();
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    if (!AUDIO_EXT.includes(ext) && !VIDEO_EXT.includes(ext)) {
      showError("このファイル形式には対応していません。", "音声（mp3・wav・m4a・aac）か動画（mp4・mov・webm）を選んでください。");
      return;
    }
    const drop = $("drop");
    drop.disabled = true;
    $("up-progress").hidden = false;
    $("up-fill").style.width = "0%";
    $("up-text").textContent = "アップロード中… 0%";

    // XHR (not fetch) so we can show upload progress; body is the raw file.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(f.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      $("up-fill").style.width = p + "%";
      $("up-text").textContent = p < 100 ? `アップロード中… ${p}%` : "ファイルを確認しています…";
    };
    const done = () => { $("up-progress").hidden = true; drop.disabled = false; };
    xhr.onerror = () => { done(); showError("サーバーにつながりませんでした。", "アプリ（python -m app）が起動しているか確認して、もう一度お試しください。"); };
    xhr.onload = () => {
      done();
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
      if (xhr.status !== 200) { showError(data.error || "アップロードに失敗しました。", "別のファイルで試すか、もう一度アップロードしてください。"); return; }
      onUploaded(data, f);
    };
    xhr.send(f);
  }

  function onUploaded(info, f) {
    file = info; hasResult = false;
    if (localURL) URL.revokeObjectURL(localURL);
    localURL = null;
    $("drop").classList.add("compact");
    $("drop").querySelector(".drop-title").textContent = "別のファイルをドロップ";
    $("drop").hidden = true;
    $("file-card").hidden = false;
    $("file-name").textContent = info.name;
    const isVideo = info.kind === "video";
    $("file-icon-use").setAttribute("href", isVideo ? "#i-film" : "#i-audio");
    $("file-sub").textContent = [fmtDur(info.duration), isVideo ? `${info.width}×${info.height}` : null, fmtSize(info.size)].filter(Boolean).join(" ・ ");
    $("kind-badge").textContent = isVideo ? "動画 → 人物を検出して自動配置" : "音声 → 黒背景に字幕";
    applyKind(info.kind);
    $("progress").hidden = true;
    $("result-actions").hidden = true;
    $("preview").hidden = false;
    const player = $("player");
    player.pause();
    if (isVideo) {
      localURL = URL.createObjectURL(f);
      player.src = localURL; player.hidden = false; $("placeholder").hidden = true;
      setAspect(info.width || 9, info.height || 16);
    } else {
      player.removeAttribute("src"); player.load(); player.hidden = true; $("placeholder").hidden = false;
    }
    updatePlaceholder();
    updateCTA();
    announce(`${info.name} を読み込みました。${$("kind-badge").textContent}`);
  }

  // ------------------------------------------------------------ jobs
  async function startJob(e) {
    if (e) e.preventDefault();
    if (!file || busy) return;
    clearError(); save();
    busy = true; updateCTA();
    try {
      const r = await fetch("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: file.file_id, opts: toOpts(readForm(), file.kind) }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.error || "作成を始められませんでした。");
      jobId = data.id;
      renderProgress(data);
      $("progress").hidden = false;
      startedAt = Date.now();
      clearInterval(tickTimer);
      tickTimer = setInterval(() => { $("elapsed").textContent = "経過 " + fmtDur((Date.now() - startedAt) / 1000); }, 1000);
      $("elapsed").textContent = "経過 0:00";
      if (window.matchMedia("(max-width: 1023px)").matches) $("progress").scrollIntoView({ behavior: "smooth", block: "start" });
      poll();
    } catch (err) {
      busy = false; updateCTA();
      const offline = err instanceof TypeError;
      showError(offline ? "サーバーにつながりませんでした。" : err.message,
        offline ? "アプリが起動しているか確認してください。" : "もう一度アップロードしてからお試しください。");
    }
  }

  function poll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const r = await fetch(`/api/job/${jobId}`, { cache: "no-store" });
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || "状態を取得できませんでした。");
        renderProgress(j);
        if (j.state === "done") return finish(j);
        if (j.state === "error") return fail(j);
        poll();
      } catch (err) {
        if (err instanceof TypeError) { $("progress-msg").textContent = "サーバーとの接続を確認しています…"; poll(); return; }
        fail({ error: err.message });
      }
    }, 800);
  }

  function renderProgress(j) {
    const stages = j.stages || [];
    const total = j.total || stages.length;
    const step = Math.max(1, j.step || 1);
    $("step-label").textContent = j.state === "queued" ? "順番待ち" : `Step ${Math.min(step, total)} / ${total}`;
    const ol = $("stepper");
    if (ol.childElementCount !== stages.length) {
      ol.innerHTML = stages.map((s) => `<li data-stage="${s}"><span class="s-name">${STAGE_LABEL[s] || s}</span></li>`).join("");
    }
    [...ol.children].forEach((li, i) => {
      const state = j.state === "done" || i < step - 1 ? "done" : i === step - 1 && j.state !== "queued" ? "active" : "";
      li.className = state;
      const name = li.querySelector(".s-name");
      const label = STAGE_LABEL[stages[i]] || stages[i];
      name.innerHTML = state === "done" ? `<svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>${label}` : label;
      if (state === "active") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    const pct = Math.round((j.frac || 0) * 100);
    $("bar-fill").style.width = pct + "%";
    $("bar").setAttribute("aria-valuenow", String(pct));
    let msg = j.msg || "";
    if (j.state === "queued" && j.queue_ahead) msg = `前の作業が終わるのを待っています（あと ${j.queue_ahead} 件）`;
    else if (j.state === "running") msg = `${msg}（${pct}%）`;
    $("progress-msg").textContent = msg;
  }

  function stopTimers() { clearTimeout(pollTimer); clearInterval(tickTimer); }

  function finish(j) {
    stopTimers();
    busy = false; hasResult = true;
    const res = j.result || {};
    const player = $("player");
    $("placeholder").hidden = true;
    player.hidden = false;
    player.onloadedmetadata = () => { if (player.videoWidth) setAspect(player.videoWidth, player.videoHeight); };
    player.src = res.url + "?t=" + Date.now();
    const dl = $("download");
    dl.href = res.url + "?download=1";
    dl.setAttribute("download", res.file || "kinetic-captions.mp4");
    $("transcript").textContent = res.transcript || "（文字おこしが空でした）";
    $("result-actions").hidden = false;
    setTimeout(() => { $("progress").hidden = true; }, 600);
    updateCTA();
    announce("完成しました。動画を再生またはダウンロードできます。");
    dl.focus({ preventScroll: true });
    $("preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function fail(j) {
    stopTimers();
    busy = false; updateCTA();
    $("progress").hidden = true;
    showError(j.error || "作成に失敗しました。", "設定（精度を「速い」にする など）を変えるか、別のファイルでお試しください。", j.detail);
  }

  // ------------------------------------------------------------ events
  async function addFont() {
    const q = $("font-query").value.trim(), msg = $("font-add-msg"), btn = $("font-add-btn");
    if (!q) { msg.className = "fa-msg err"; msg.textContent = "フォント名かURLを入れてください"; $("font-query").focus(); return; }
    btn.disabled = true; msg.className = "fa-msg busy"; msg.textContent = "Google Fonts から取得しています…";
    try {
      const r = await fetch("/api/fonts/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || "追加できませんでした");
      await loadFonts();
      setRadio("font", j.font.id);
      form.dispatchEvent(new Event("change", { bubbles: true }));
      updatePlaceholder();
      $("font-query").value = "";
      msg.className = "fa-msg ok";
      msg.textContent = `「${j.font.label}」を追加して選びました${j.font.case === "lower" ? "（筆記体なので小文字で表示します）" : ""}`;
    } catch (e) {
      msg.className = "fa-msg err"; msg.textContent = e.message;
    } finally { btn.disabled = false; }
  }
  $("font-add-btn").addEventListener("click", addFont);
  $("font-query").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addFont(); } });

  const drop = $("drop");
  drop.addEventListener("click", pickFile);
  $("change-file").addEventListener("click", pickFile);
  $("file").addEventListener("change", (e) => { handleFile(e.target.files[0]); e.target.value = ""; });
  ["dragenter", "dragover"].forEach((ev) => document.addEventListener(ev, (e) => {
    e.preventDefault();
    if (!busy) { drop.hidden = false; drop.classList.add("over"); }
  }));
  ["dragleave", "drop"].forEach((ev) => document.addEventListener(ev, (e) => {
    e.preventDefault();
    if (ev === "dragleave" && e.relatedTarget) return;
    drop.classList.remove("over");
    if (file && ev === "dragleave") drop.hidden = true;
  }));
  document.addEventListener("drop", (e) => {
    const f = e.dataTransfer && e.dataTransfer.files[0];
    if (f) handleFile(f); else if (file) drop.hidden = true;
  });

  form.addEventListener("input", () => { syncDerived(); save(); });
  form.addEventListener("change", () => { syncDerived(); save(); });
  form.addEventListener("submit", startJob);
  $("reset").addEventListener("click", () => { writeForm(DEFAULTS); save(); announce("初期設定に戻しました"); });
  $("error-close").addEventListener("click", clearError);
  $("redo").addEventListener("click", () => {
    $("advanced").open = true;
    $("settings").scrollIntoView({ behavior: "smooth", block: "start" });
    const sel = form.querySelector('input[name="preset"]:checked');
    if (sel) sel.focus({ preventScroll: true });
    announce("設定を変えて「この設定で作り直す」を押してください");
  });
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("transcript").textContent); $("copy-label").textContent = "コピーしました"; }
    catch (_) { $("copy-label").textContent = "コピーできませんでした"; }
    setTimeout(() => { $("copy-label").textContent = "コピー"; }, 2000);
  });

  // smooth scrolling off when reduced motion is requested
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const orig = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (o) { return orig.call(this, typeof o === "object" ? { ...o, behavior: "auto" } : o); };
  }

  writeForm(load());
  loadFonts().then(() => { const s = load(); if (s.font) setRadio("font", s.font); updatePlaceholder(); });
  applyKind(null);
  updateCTA();
})();
