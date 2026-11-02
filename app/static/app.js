/* Kinetic Captions UI — vanilla JS, no dependencies. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const form = $("form");
  const STORE = "kc.settings.v1";
  const AUDIO_EXT = ["mp3", "wav", "m4a", "aac"];
  const VIDEO_EXT = ["mp4", "mov", "webm"];
  const PRESET_LOOK = {
    motiversity: { weight: 900, color: null, size: 1.0, pos: "center" },
    hormozi: { weight: 900, color: "#FACC15", size: 0.85, pos: "center" },
    clean: { weight: 600, color: null, size: 0.55, pos: "end" },
  };
  const FONT_FAMILY = { montserrat: "'KC Montserrat'", anton: "'KC Anton'", bebas: "'KC Bebas Neue'", courgette: "'KC Courgette'" };
  const FONT_CASE = { courgette: "lower" };
  const LOADED = new Set();
  // ------------------------------------------------------------ i18n
  // One dictionary for every user-visible string. "{x}" = parameter.
  const I18N = {
    ja: {
      title: "Kinetic Captions — 字幕動画をかんたんに",
      skip: "設定へスキップ",
      tagline: "音声か動画を入れるだけで、プロっぽい字幕動画に",
      aria_stage: "ファイルとプレビュー",
      aria_settings: "設定",
      aria_progress: "全体の進み具合",
      drop_title: "ここにファイルをドロップ",
      drop_again: "別のファイルをドロップ",
      drop_sub: "またはクリックして選ぶ",
      drop_hint: "音声（mp3・wav・m4a・aac）／動画（mp4・mov・webm）",
      uploading: "アップロード中…",
      uploading_pct: "アップロード中… {p}%",
      checking_file: "ファイルを確認しています…",
      change_file: "別のファイル",
      error_detail: "詳しい情報",
      error_close: "エラーを閉じる",
      done_badge: "完成しました",
      download: "ダウンロード",
      redo: "設定を変えて作り直す",
      transcript: "文字おこし",
      transcript_empty: "（文字おこしが空でした）",
      copy: "コピー",
      copied: "コピーしました",
      copy_failed: "コピーできませんでした",
      style: "スタイル",
      moti_name: "モチバーシティ",
      moti_desc: "白・極太・頭の後ろに回り込む",
      horm_name: "ホルモジ",
      horm_desc: "黄色／緑で強調・中央",
      clean_name: "シンプル",
      clean_desc: "下に控えめ",
      font: "フォント",
      script_tag: "（筆記体）",
      courgette_label: "Courgette（筆記体）",
      fa_label: "Google Fonts から追加",
      fa_placeholder: "フォント名・URL・埋め込みコードを貼り付け",
      fa_add: "追加",
      fa_help: '<a href="https://fonts.google.com/" target="_blank" rel="noopener">fonts.google.com</a> で好きなフォントを開き、そのページのURLか「Get embed code」のコードをそのまま貼り付けてください。例：Pacifico',
      fa_fetching: "Google Fonts から取得しています…",
      fa_failed: "追加できませんでした",
      fa_added: "「{label}」を追加して選びました",
      fa_added_lower: "（筆記体なので小文字で表示します）",
      font_empty: "フォント名かURLを入れてください",
      advanced: "詳細設定",
      adv_note: "おまかせでOK",
      type_hint: "ファイルを選ぶと、種類に合わせた設定だけが表示されます。",
      text_color: "文字色",
      accent_color: "強調色",
      auto: "おまかせ",
      size: "文字サイズ",
      smaller: "小さめ",
      larger: "大きめ",
      position: "位置",
      pos_auto: "おまかせ（自動）",
      pos_top: "上",
      pos_center: "中央",
      pos_bottom: "下",
      behind: "頭の後ろに回り込ませる",
      behind_hint: "大きな文字が人物の後ろに隠れるように配置します。",
      background: "背景",
      bg_keep: "そのまま",
      bg_cutout: "黒で人物だけ",
      bg_black: "黒",
      bg_white: "白",
      bg_custom: "好きな色",
      bg_color: "背景の色",
      aspect: "画面比率",
      aspect_portrait: "9:16 縦",
      aspect_landscape: "16:9 横",
      language: "言語",
      lang_auto: "自動",
      lang_en: "英語",
      lang_ja: "日本語",
      accuracy: "精度",
      acc_fast: "速い",
      acc_standard: "標準",
      acc_high: "高精度",
      acc_hint: "高精度は時間がかかります。文字おこしの間違いが多いときに。",
      reset: "初期設定に戻す",
      cta_create: "字幕動画を作る",
      cta_busy: "作成中…",
      cta_redo: "この設定で作り直す",
      hint_pick: "まずはファイルを選んでください",
      hint_busy: "完成まで、このページを開いたままにしてください",
      hint_redo: "2回目からは文字おこしを再利用するので速く終わります",
      hint_ready: "設定はそのままでもOK。ボタンを押すだけです",
      err_type: "このファイル形式には対応していません。",
      err_type_hint: "音声（mp3・wav・m4a・aac）か動画（mp4・mov・webm）を選んでください。",
      err_offline: "サーバーにつながりませんでした。",
      err_offline_hint_upload: "アプリ（python -m app）が起動しているか確認して、もう一度お試しください。",
      err_offline_hint: "アプリが起動しているか確認してください。",
      err_upload: "アップロードに失敗しました。",
      err_upload_hint: "別のファイルで試すか、もう一度アップロードしてください。",
      err_start: "作成を始められませんでした。",
      err_start_hint: "もう一度アップロードしてからお試しください。",
      err_status: "状態を取得できませんでした。",
      err_failed: "作成に失敗しました。",
      err_failed_hint: "設定（精度を「速い」にする など）を変えるか、別のファイルでお試しください。",
      reconnecting: "サーバーとの接続を確認しています…",
      kind_video: "動画 → 人物を検出して自動配置",
      kind_audio: "音声 → 黒背景に字幕",
      sep: " ・ ",
      loaded: "{name} を読み込みました。{kind}",
      stage_prepare: "準備",
      stage_transcribe: "文字おこし",
      stage_matte: "人物検出",
      stage_layout: "演出",
      stage_render: "書き出し",
      step: "ステップ {n} / {total}",
      queued: "順番待ち",
      queue_wait: "順番待ちです",
      queue_ahead: "前の作業が終わるのを待っています（あと {n} 件）",
      msg_prepare: "準備しています",
      msg_transcribe: "文字おこし中",
      msg_transcribe_matte: "文字おこし中（人物検出も同時に実行中）",
      msg_matte: "人物・物体を検出中",
      msg_layout: "演出を決めています",
      msg_render: "動画を書き出しています",
      msg_done: "完成しました",
      with_pct: "{msg}（{p}%）",
      elapsed: "経過 {t}",
      done_announce: "完成しました。動画を再生またはダウンロードできます。",
      reset_announce: "初期設定に戻しました",
      redo_announce: "設定を変えて「この設定で作り直す」を押してください",
      lang_changed: "日本語に切り替えました",
    },
    en: {
      title: "Kinetic Captions — Captioned videos made easy",
      skip: "Skip to settings",
      tagline: "Drop in audio or video and get a pro-looking captioned video",
      aria_stage: "File and preview",
      aria_settings: "Settings",
      aria_progress: "Overall progress",
      drop_title: "Drop a file here",
      drop_again: "Drop another file",
      drop_sub: "or click to choose one",
      drop_hint: "Audio (mp3, wav, m4a, aac) / Video (mp4, mov, webm)",
      uploading: "Uploading…",
      uploading_pct: "Uploading… {p}%",
      checking_file: "Checking the file…",
      change_file: "Change file",
      error_detail: "Details",
      error_close: "Dismiss error",
      done_badge: "Your video is ready",
      download: "Download",
      redo: "Change settings and remake",
      transcript: "Transcript",
      transcript_empty: "(The transcript is empty)",
      copy: "Copy",
      copied: "Copied",
      copy_failed: "Couldn't copy",
      style: "Style",
      moti_name: "Motiversity",
      moti_desc: "White, extra bold, wraps behind the head",
      horm_name: "Hormozi",
      horm_desc: "Yellow/green highlights, centered",
      clean_name: "Simple",
      clean_desc: "Subtle, at the bottom",
      font: "Font",
      script_tag: " (script)",
      courgette_label: "Courgette (script)",
      fa_label: "Add from Google Fonts",
      fa_placeholder: "Paste a font name, URL, or embed code",
      fa_add: "Add",
      fa_help: 'Open any font on <a href="https://fonts.google.com/" target="_blank" rel="noopener">fonts.google.com</a>, then paste its page URL or its “Get embed code” snippet as is. Example: Pacifico',
      fa_fetching: "Fetching from Google Fonts…",
      fa_failed: "Couldn't add the font",
      fa_added: "Added and selected “{label}”",
      fa_added_lower: " (script font, so captions stay lowercase)",
      font_empty: "Enter a font name or URL",
      advanced: "Advanced settings",
      adv_note: "Defaults work fine",
      type_hint: "Choose a file to see only the settings that apply to it.",
      text_color: "Text color",
      accent_color: "Highlight color",
      auto: "Auto",
      size: "Text size",
      smaller: "Smaller",
      larger: "Larger",
      position: "Position",
      pos_auto: "Auto",
      pos_top: "Top",
      pos_center: "Center",
      pos_bottom: "Bottom",
      behind: "Wrap words behind the head",
      behind_hint: "Places big words so they tuck behind the person.",
      background: "Background",
      bg_keep: "Keep original",
      bg_cutout: "Person on black",
      bg_black: "Black",
      bg_white: "White",
      bg_custom: "Custom color",
      bg_color: "Background color",
      aspect: "Aspect ratio",
      aspect_portrait: "9:16 Vertical",
      aspect_landscape: "16:9 Horizontal",
      language: "Language",
      lang_auto: "Auto",
      lang_en: "English",
      lang_ja: "Japanese",
      accuracy: "Accuracy",
      acc_fast: "Fast",
      acc_standard: "Standard",
      acc_high: "High",
      acc_hint: "High takes longer. Use it when the transcript has lots of mistakes.",
      reset: "Reset to defaults",
      cta_create: "Create captioned video",
      cta_busy: "Creating…",
      cta_redo: "Remake with these settings",
      hint_pick: "Choose a file to get started",
      hint_busy: "Keep this page open until it's done",
      hint_redo: "Remakes reuse the transcript, so they finish faster",
      hint_ready: "The default settings are fine. Just press the button",
      err_type: "This file type isn't supported.",
      err_type_hint: "Choose audio (mp3, wav, m4a, aac) or video (mp4, mov, webm).",
      err_offline: "Couldn't connect to the server.",
      err_offline_hint_upload: "Make sure the app (python -m app) is running, then try again.",
      err_offline_hint: "Make sure the app is running.",
      err_upload: "Upload failed.",
      err_upload_hint: "Try another file, or upload it again.",
      err_start: "Couldn't start creating the video.",
      err_start_hint: "Upload the file again and retry.",
      err_status: "Couldn't get the status.",
      err_failed: "Couldn't create the video.",
      err_failed_hint: "Change the settings (e.g. set Accuracy to “Fast”) or try another file.",
      reconnecting: "Checking the connection to the server…",
      kind_video: "Video → finds the person and places captions automatically",
      kind_audio: "Audio → captions on a black background",
      sep: " · ",
      loaded: "Loaded {name}. {kind}",
      stage_prepare: "Prepare",
      stage_transcribe: "Transcribe",
      stage_matte: "Find people",
      stage_layout: "Style",
      stage_render: "Render",
      step: "Step {n} / {total}",
      queued: "Waiting",
      queue_wait: "Waiting in line",
      queue_ahead: "Waiting for {n} job(s) ahead to finish",
      msg_prepare: "Getting ready",
      msg_transcribe: "Transcribing",
      msg_transcribe_matte: "Transcribing (finding people at the same time)",
      msg_matte: "Finding people and objects",
      msg_layout: "Planning the animation",
      msg_render: "Rendering the video",
      msg_done: "Done",
      with_pct: "{msg} ({p}%)",
      elapsed: "Elapsed {t}",
      done_announce: "Done! You can play or download the video.",
      reset_announce: "Settings reset to defaults",
      redo_announce: "Change the settings, then press “Remake with these settings”",
      lang_changed: "Switched to English",
    },
  };
  const LANG_KEY = "kc.lang";
  const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
  function initialLang() {
    const q = new URLSearchParams(location.search).get("lang");
    if (q && has(I18N, q)) {   // ?lang=ja|en overrides and persists
      try { localStorage.setItem(LANG_KEY, q); } catch (_) { /* private mode */ }
      return q;
    }
    try { const v = localStorage.getItem(LANG_KEY); if (v && has(I18N, v)) return v; } catch (_) { /* private mode */ }
    return (navigator.language || "").toLowerCase().startsWith("ja") ? "ja" : "en";
  }
  let lang = initialLang();
  function t(key, p) {
    const str = has(I18N[lang], key) ? I18N[lang][key] : has(I18N.ja, key) ? I18N.ja[key] : key;
    return p ? str.replace(/\{(\w+)\}/g, (m, k) => (has(p, k) ? String(p[k]) : m)) : str;
  }
  // text "spec": plain string | {k, p} (dictionary key) | {ja, en} (server-provided, both languages)
  const K = (k, p) => ({ k, p });
  function L(spec) {
    if (spec == null) return "";
    if (typeof spec === "string") return spec;
    if (spec.k) return t(spec.k, spec.p);
    return spec[lang] || spec.ja || spec.en || "";
  }
  const srvMsg = (data, fallbackKey) => (data && (data.error_i18n || data.error)) || K(fallbackKey);
  // every API call says which language the server should answer in
  const api = (url, o = {}) => fetch(url, { ...o, headers: { ...(o.headers || {}), "X-Lang": lang } });
  const SCRIPT_SUFFIX = /\s*（[^）]*）\s*$/;   // e.g. built-in "Courgette（…）" -> localised tag
  function fontLabel(raw) {
    raw = String(raw || "");
    return SCRIPT_SUFFIX.test(raw) ? raw.replace(SCRIPT_SUFFIX, "") + t("script_tag") : raw;
  }
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
  let jobId = null, pollTimer = null, tickTimer = null, startedAt = 0, endedAt = 0;
  let busy = false, hasResult = false;
  // last-rendered dynamic state, so a language switch can re-render it in place
  let lastJob = null, errState = null, fontMsg = null, upSpec = K("uploading"), transcriptEmpty = false;

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
      const r = await api("/api/fonts");
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
        lab.innerHTML = `<input type="radio" name="font" value="${esc(f.id)}"><span class="f-sample" style="font-family:'${esc(f.family)}';font-weight:${weight}">${esc(f.sample || "NEVER GIVE UP")}</span><span class="f-name" data-label="${esc(f.label)}">${esc(fontLabel(f.label))}</span>`;
        box.appendChild(lab);
      });
      setRadio("font", cur);
      if (!radio("font")) box.querySelector("input").checked = true;
    } catch (_) { /* static fallback stays */ }
  }

  function relabelFonts() {
    $("fonts").querySelectorAll(".f-name[data-label]").forEach((el) => { el.textContent = fontLabel(el.dataset.label); });
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
    errState = { msg, hint, detail };
    renderError();
    $("error").hidden = false;
  }
  function renderError() {
    if (!errState) return;
    const hint = L(errState.hint);
    $("error-msg").textContent = L(errState.msg);
    $("error-hint").textContent = hint;
    $("error-hint").hidden = !hint;
    $("error-detail").textContent = errState.detail || "";
    $("error-detail-wrap").hidden = !errState.detail;
  }
  function clearError() { $("error").hidden = true; errState = null; }

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
    $("cta-label").textContent = t(busy ? "cta_busy" : hasResult ? "cta_redo" : "cta_create");
    $("cta-hint").textContent = t(!file ? "hint_pick" : busy ? "hint_busy" : hasResult ? "hint_redo" : "hint_ready");
  }

  // ------------------------------------------------------------ upload
  function pickFile() { if (!busy) $("file").click(); }

  function handleFile(f) {
    if (!f || busy) return;
    clearError();
    const ext = (f.name.split(".").pop() || "").toLowerCase();
    if (!AUDIO_EXT.includes(ext) && !VIDEO_EXT.includes(ext)) {
      showError(K("err_type"), K("err_type_hint"));
      return;
    }
    const drop = $("drop");
    drop.disabled = true;
    $("up-progress").hidden = false;
    $("up-fill").style.width = "0%";
    setUp(K("uploading_pct", { p: 0 }));

    // XHR (not fetch) so we can show upload progress; body is the raw file.
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.setRequestHeader("X-Filename", encodeURIComponent(f.name));
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-Lang", lang);
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      const p = Math.round((e.loaded / e.total) * 100);
      $("up-fill").style.width = p + "%";
      setUp(p < 100 ? K("uploading_pct", { p }) : K("checking_file"));
    };
    const done = () => { $("up-progress").hidden = true; drop.disabled = false; };
    xhr.onerror = () => { done(); showError(K("err_offline"), K("err_offline_hint_upload")); };
    xhr.onload = () => {
      done();
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch (_) { /* ignore */ }
      if (xhr.status !== 200) { showError(srvMsg(data, "err_upload"), K("err_upload_hint")); return; }
      onUploaded(data, f);
    };
    xhr.send(f);
  }

  function setUp(spec) { upSpec = spec; $("up-text").textContent = L(spec); }

  function renderFileCard() {
    if (!file) return;
    const isVideo = file.kind === "video";
    $("file-sub").textContent = [fmtDur(file.duration), isVideo ? `${file.width}×${file.height}` : null, fmtSize(file.size)].filter(Boolean).join(t("sep"));
    $("kind-badge").textContent = t(isVideo ? "kind_video" : "kind_audio");
  }

  function onUploaded(info, f) {
    file = info; hasResult = false;
    if (localURL) URL.revokeObjectURL(localURL);
    localURL = null;
    $("drop").classList.add("compact");
    const dt = $("drop").querySelector(".drop-title");
    dt.dataset.i18n = "drop_again"; dt.textContent = t("drop_again");
    $("drop").hidden = true;
    $("file-card").hidden = false;
    $("file-name").textContent = info.name;
    const isVideo = info.kind === "video";
    $("file-icon-use").setAttribute("href", isVideo ? "#i-film" : "#i-audio");
    renderFileCard();
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
    announce(t("loaded", { name: info.name, kind: $("kind-badge").textContent }));
  }

  // ------------------------------------------------------------ jobs
  async function startJob(e) {
    if (e) e.preventDefault();
    if (!file || busy) return;
    clearError(); save();
    busy = true; updateCTA();
    try {
      const r = await api("/api/jobs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_id: file.file_id, opts: toOpts(readForm(), file.kind) }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) { const er = new Error(data.error || ""); er.spec = srvMsg(data, "err_start"); throw er; }
      jobId = data.id;
      renderProgress(data);
      $("progress").hidden = false;
      startedAt = Date.now(); endedAt = 0;
      clearInterval(tickTimer);
      tickTimer = setInterval(renderElapsed, 1000);
      renderElapsed();
      if (window.matchMedia("(max-width: 1023px)").matches) $("progress").scrollIntoView({ behavior: "smooth", block: "start" });
      poll();
    } catch (err) {
      busy = false; updateCTA();
      const offline = err instanceof TypeError;
      showError(offline ? K("err_offline") : (err.spec || err.message),
        K(offline ? "err_offline_hint" : "err_start_hint"));
    }
  }

  function poll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const r = await api(`/api/job/${jobId}`, { cache: "no-store" });
        const j = await r.json();
        if (!r.ok) { const er = new Error(j.error || ""); er.spec = srvMsg(j, "err_status"); throw er; }
        renderProgress(j);
        if (j.state === "done") return finish(j);
        if (j.state === "error") return fail(j);
        poll();
      } catch (err) {
        if (err instanceof TypeError) { $("progress-msg").textContent = t("reconnecting"); poll(); return; }
        fail({ error: err.spec || err.message });
      }
    }, 800);
  }

  function renderElapsed() {
    $("elapsed").textContent = t("elapsed", { t: fmtDur(startedAt ? ((endedAt || Date.now()) - startedAt) / 1000 : 0) });
  }

  // progress text is rendered here from `stage` + `frac`; the server's `msg` is not shown
  function progressText(j, pct) {
    if (j.state === "queued") return j.queue_ahead ? t("queue_ahead", { n: j.queue_ahead }) : t("queue_wait");
    if (j.state === "done" || j.stage === "done") return t("msg_done");
    const key = j.stage === "transcribe" && j.kind === "video" ? "msg_transcribe_matte" : "msg_" + j.stage;
    const msg = has(I18N.ja, key) ? t(key) : t("msg_prepare");
    return j.state === "running" ? t("with_pct", { msg, p: pct }) : msg;
  }

  function renderProgress(j) {
    lastJob = j;
    const stages = j.stages || [];
    const total = j.total || stages.length;
    const step = Math.max(1, j.step || 1);
    $("step-label").textContent = j.state === "queued" ? t("queued") : t("step", { n: Math.min(step, total), total });
    const ol = $("stepper");
    if (ol.childElementCount !== stages.length) {
      ol.innerHTML = stages.map((s) => `<li data-stage="${esc(s)}"><span class="s-name"></span></li>`).join("");
    }
    [...ol.children].forEach((li, i) => {
      const state = j.state === "done" || i < step - 1 ? "done" : i === step - 1 && j.state !== "queued" ? "active" : "";
      li.className = state;
      const name = li.querySelector(".s-name");
      const label = esc(has(I18N.ja, "stage_" + stages[i]) ? t("stage_" + stages[i]) : stages[i]);
      name.innerHTML = state === "done" ? `<svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>${label}` : label;
      if (state === "active") li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });
    const pct = Math.round((j.frac || 0) * 100);
    $("bar-fill").style.width = pct + "%";
    $("bar").setAttribute("aria-valuenow", String(pct));
    $("progress-msg").textContent = progressText(j, pct);
  }

  function stopTimers() { clearTimeout(pollTimer); clearInterval(tickTimer); endedAt = Date.now(); }

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
    transcriptEmpty = !res.transcript;
    $("transcript").textContent = res.transcript || t("transcript_empty");
    $("result-actions").hidden = false;
    setTimeout(() => { $("progress").hidden = true; }, 600);
    updateCTA();
    announce(t("done_announce"));
    dl.focus({ preventScroll: true });
    $("preview").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function fail(j) {
    stopTimers();
    busy = false; updateCTA();
    $("progress").hidden = true;
    showError(j.error_i18n || j.error || K("err_failed"), K("err_failed_hint"), j.detail);
  }

  // ------------------------------------------------------------ events
  async function addFont() {
    const q = $("font-query").value.trim(), btn = $("font-add-btn");
    if (!q) { setFontMsg("err", K("font_empty")); $("font-query").focus(); return; }
    btn.disabled = true; setFontMsg("busy", K("fa_fetching"));
    try {
      const r = await api("/api/fonts/add", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ query: q }) });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) { const er = new Error(j.error || ""); er.spec = srvMsg(j, "fa_failed"); throw er; }
      await loadFonts();
      setRadio("font", j.font.id);
      form.dispatchEvent(new Event("change", { bubbles: true }));
      updatePlaceholder();
      $("font-query").value = "";
      setFontMsg("ok", { added: j.font.label, lower: j.font.case === "lower" });
    } catch (e) {
      setFontMsg("err", e.spec || e.message || K("fa_failed"));
    } finally { btn.disabled = false; }
  }
  function setFontMsg(cls, spec) { fontMsg = { cls, spec }; renderFontMsg(); }
  function renderFontMsg() {
    const el = $("font-add-msg");
    if (!fontMsg) { el.className = "fa-msg"; el.textContent = ""; return; }
    const sp = fontMsg.spec;
    el.className = "fa-msg " + fontMsg.cls;
    el.textContent = sp && sp.added !== undefined
      ? t("fa_added", { label: fontLabel(sp.added) }) + (sp.lower ? t("fa_added_lower") : "")
      : L(sp);
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
  $("reset").addEventListener("click", () => { writeForm(DEFAULTS); save(); announce(t("reset_announce")); });
  $("error-close").addEventListener("click", clearError);
  $("redo").addEventListener("click", () => {
    $("advanced").open = true;
    $("settings").scrollIntoView({ behavior: "smooth", block: "start" });
    const sel = form.querySelector('input[name="preset"]:checked');
    if (sel) sel.focus({ preventScroll: true });
    announce(t("redo_announce"));
  });
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("transcript").textContent); $("copy-label").textContent = t("copied"); }
    catch (_) { $("copy-label").textContent = t("copy_failed"); }
    setTimeout(() => { $("copy-label").textContent = t("copy"); }, 2000);
  });

  // smooth scrolling off when reduced motion is requested
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const orig = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (o) { return orig.call(this, typeof o === "object" ? { ...o, behavior: "auto" } : o); };
  }

  // ------------------------------------------------------------ language
  function applyStatic() {
    document.documentElement.lang = lang;
    document.title = t("title");
    document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
    document.querySelectorAll("[data-i18n-html]").forEach((el) => { el.innerHTML = t(el.dataset.i18nHtml); });  // trusted: own dictionary
    document.querySelectorAll("[data-i18n-attr]").forEach((el) => {
      el.dataset.i18nAttr.split(";").forEach((pair) => {
        const [attr, key] = pair.split(":").map((x) => x.trim());
        if (attr && key) el.setAttribute(attr, t(key));
      });
    });
    document.querySelectorAll(".lang-btn").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  }
  function setLang(next, persist) {
    if (!has(I18N, next)) return;
    lang = next;
    if (persist) { try { localStorage.setItem(LANG_KEY, lang); } catch (_) { /* private mode */ } }
    applyStatic();
    // dynamic parts: re-render from state (file / job / result are untouched)
    updateCTA();
    renderFileCard();
    relabelFonts();
    renderError();
    renderFontMsg();
    $("up-text").textContent = L(upSpec);
    if (lastJob) renderProgress(lastJob);
    renderElapsed();
    if (transcriptEmpty) $("transcript").textContent = t("transcript_empty");
  }
  document.querySelectorAll(".lang-btn").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.lang === lang) return;
    setLang(b.dataset.lang, true);
    announce(t("lang_changed"));
  }));
  if (new URLSearchParams(location.search).has("lang")) {   // keep the URL clean once persisted
    try { const u = new URL(location.href); u.searchParams.delete("lang"); history.replaceState(null, "", u); } catch (_) { /* ignore */ }
  }

  setLang(lang, false);
  writeForm(load());
  loadFonts().then(() => { const s = load(); if (s.font) setRadio("font", s.font); updatePlaceholder(); });
  applyKind(null);
  updateCTA();
})();
