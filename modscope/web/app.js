(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const SEVERITY = {
    error: { label: "严重", short: "必须处理" },
    warning: { label: "警告", short: "需要留意" },
    notice: { label: "提示", short: "补充信息" },
  };
  const CATEGORY = {
    loader: { label: "加载器", icon: "i-cube" },
    dependency: { label: "依赖", icon: "i-layers" },
    version: { label: "版本", icon: "i-refresh" },
    integrity: { label: "文件完整性", icon: "i-alert" },
    duplicate: { label: "重复", icon: "i-copy" },
    class: { label: "类冲突", icon: "i-code" },
    mixin: { label: "Mixin", icon: "i-spark" },
    resource: { label: "资源", icon: "i-box" },
    metadata: { label: "元数据", icon: "i-file" },
    info: { label: "提示", icon: "i-help" },
  };
  const LOADER_LABEL = {
    fabric: "Fabric",
    quilt: "Quilt",
    forge: "Forge",
    legacyforge: "Legacy Forge",
    neoforge: "NeoForge",
    liteloader: "LiteLoader",
    rift: "Rift",
    bukkit: "Bukkit / Paper",
    unknown: "未知",
  };
  const STATUS_LABEL = { active: "启用", disabled: "已禁用", nested: "内置" };
  const ICON = {
    dependency: "i-layers",
    version: "i-refresh",
    loader: "i-cube",
    integrity: "i-alert",
    duplicate: "i-copy",
    class: "i-code",
    mixin: "i-spark",
    resource: "i-box",
    metadata: "i-file",
    info: "i-help",
  };
  const PHASES = [
    { title: "读取 JAR 索引", subtitle: "打开压缩包、定位元数据与 class 文件。" },
    { title: "解析 MOD 元数据", subtitle: "识别 Forge、Fabric、Quilt、LiteLoader 与旧版格式。" },
    { title: "求解依赖与版本", subtitle: "构建依赖图、比对 Minecraft / Java / 加载器版本。" },
    { title: "检查类与 Mixin", subtitle: "比对同名类、配置与资源命名空间。" },
  ];

  const elements = {
    landing: $("#landing"),
    scanning: $("#scanning"),
    report: $("#report"),
    dropzone: $("#dropzone"),
    folderInput: $("#folderInput"),
    filesInput: $("#filesInput"),
    selectionChip: $("#selectionChip"),
    selectionText: $("#selectionText"),
    clearSelection: $("#clearSelection"),
    pathInput: $("#pathInput"),
    pickFolderBtn: $("#pickFolderBtn"),
    scanPathBtn: $("#scanPathBtn"),
    mcVersion: $("#mcVersion"),
    loaderSelect: $("#loaderSelect"),
    javaVersion: $("#javaVersion"),
    deepScan: $("#deepScan"),
    includeDisabled: $("#includeDisabled"),
    sideSegmented: $("#sideSegmented"),
    startScanBtn: $("#startScanBtn"),
    progressBar: $("#progressBar"),
    progressText: $("#progressText"),
    progressPercent: $("#progressPercent"),
    scanningTitle: $("#scanningTitle"),
    scanningSubtitle: $("#scanningSubtitle"),
    scanPhases: $("#scanPhases"),
    cancelScanBtn: $("#cancelScanBtn"),
    reportSource: $("#reportSource"),
    scoreRing: $("#scoreRing"),
    scoreValue: $("#scoreValue"),
    gradeLabel: $("#gradeLabel"),
    reportHeadline: $("#reportHeadline"),
    reportMeta: $("#reportMeta"),
    reportCounts: $("#reportCounts"),
    quickStats: $("#quickStats"),
    scanDuration: $("#scanDuration"),
    tabFindingCount: $("#tabFindingCount"),
    tabModCount: $("#tabModCount"),
    severityFilters: $("#severityFilters"),
    categoryFilters: $("#categoryFilters"),
    findingSearch: $("#findingSearch"),
    resultCount: $("#resultCount"),
    findingsList: $("#findingsList"),
    findingsEmpty: $("#findingsEmpty"),
    clearFiltersBtn: $("#clearFiltersBtn"),
    modSearch: $("#modSearch"),
    modStatusFilter: $("#modStatusFilter"),
    modResultCount: $("#modResultCount"),
    modsTableBody: $("#modsTableBody"),
    modsEmpty: $("#modsEmpty"),
    detailsGrid: $("#detailsGrid"),
    jarsTableBody: $("#jarsTableBody"),
    jarCountLabel: $("#jarCountLabel"),
    caveatsList: $("#caveatsList"),
    generatedAt: $("#generatedAt"),
    rescanBtn: $("#rescanBtn"),
    copySummaryBtn: $("#copySummaryBtn"),
    printBtn: $("#printBtn"),
    exportBtn: $("#exportBtn"),
    exportMenu: $("#exportMenu"),
    helpDialog: $("#helpDialog"),
    closeHelp: $("#closeHelp"),
    themeToggle: $("#themeToggle"),
    toastRegion: $("#toastRegion"),
    printReport: $("#printReport"),
    wholeReport: $(".report-view"),
  };

  const state = {
    selectedFiles: [],
    selectionBytes: 0,
    uploadSessionId: null,
    uploadSignature: "",
    report: null,
    activeTab: "findings",
    severity: "all",
    category: "all",
    findingSearch: "",
    modSearch: "",
    modStatus: "active",
    scanning: false,
    scanId: 0,
    scanController: null,
    activeXhrs: new Set(),
    phaseTimer: null,
    progressTimer: null,
    phaseIndex: 0,
    progressValue: 0,
    savedSignature: "",
    pickerBusy: false,
    lastPrinted: false,
  };

  // ---------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  const escapeAttr = (value) => escapeHtml(value).replace(/`/g, "&#96;");

  const fmtBytes = (bytes) => {
    if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let index = 0;
    let value = bytes;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value >= 100 || index === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[index]}`;
  };

  const fmtDuration = (ms) => {
    if (!Number.isFinite(ms)) return "—";
    if (ms < 1000) return `${Math.round(ms)} ms`;
    return `${(ms / 1000).toFixed(1)} s`;
  };

  const fmtDate = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
  };

  const fmtLocalTime = () => {
    const date = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}`;
  };

  const loaderLabel = (loader) => LOADER_LABEL[loader] || loader || "未知";
  const loaderClass = (loader) => (loader || "unknown").replace(/[^a-z0-9_-]/gi, "").toLowerCase();
  const severityLabel = (severity) => (SEVERITY[severity]?.label || severity || "提示");
  const categoryLabel = (category) => (CATEGORY[category]?.label || category || "提示");

  const isModFilename = (name) => {
    let lower = String(name || "").toLowerCase();
    if (lower.endsWith(".disabled")) lower = lower.slice(0, -9);
    return lower.endsWith(".jar") || lower.endsWith(".litemod");
  };

  const dedupeFiles = (files) => {
    const seen = new Set();
    const result = [];
    for (const file of files) {
      if (!isModFilename(file.name)) continue;
      const key = `${file.name}\u0000${file.size}\u0000${file.lastModified}`;
      if (seen.has(key)) continue;
      seen.add(key);
      result.push(file);
    }
    return result.sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
  };

  async function api(path, options = {}) {
    const headers = Object.assign({ "X-ModScope-Request": "1" }, options.headers || {});
    const init = {
      method: options.method || "GET",
      headers,
      signal: options.signal,
    };
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(options.json);
    } else if (options.body !== undefined) {
      init.body = options.body;
    }
    let response;
    try {
      response = await fetch(path, init);
    } catch (error) {
      if (error.name === "AbortError") throw error;
      throw new Error("无法连接到本地分析服务。请确认启动窗口仍在运行。");
    }
    let data = null;
    try {
      data = await response.json();
    } catch (_) {
      // A non-JSON response is turned into a useful generic message below.
    }
    if (!response.ok) {
      const message = (data && (data.error || data.message)) || `请求失败（HTTP ${response.status}）`;
      const detail = data && data.detail ? ` ${data.detail}` : "";
      const error = new Error(message + detail);
      error.detail = detail;
      throw error;
    }
    if (data && data.ok === false) {
      throw new Error(data.error || "请求失败。");
    }
    return data || {};
  }

  // ---------------------------------------------------------------------
  // Toast, dialogs, theme
  // ---------------------------------------------------------------------
  function showToast(title, message = "", type = "info") {
    const toast = document.createElement("div");
    toast.className = `toast ${type === "error" ? "error" : ""}`;
    const icon = type === "error" ? "i-alert" : "i-check";
    toast.innerHTML = `
      <svg class="icon"><use href="#${icon}"/></svg>
      <div><strong>${escapeHtml(title)}</strong>${message ? `<span>${escapeHtml(message)}</span>` : ""}</div>
    `;
    elements.toastRegion.appendChild(toast);
    while (elements.toastRegion.children.length > 3) {
      elements.toastRegion.removeChild(elements.toastRegion.firstElementChild);
    }
    window.setTimeout(() => {
      toast.style.opacity = "0";
      toast.style.transform = "translateY(6px)";
      window.setTimeout(() => toast.remove(), 220);
    }, type === "error" ? 7000 : 4600);
  }

  function openHelp() {
    if (typeof elements.helpDialog.showModal === "function") {
      elements.helpDialog.showModal();
    } else {
      elements.helpDialog.setAttribute("open", "");
    }
  }

  function closeHelp() {
    if (typeof elements.helpDialog.close === "function") elements.helpDialog.close();
    else elements.helpDialog.removeAttribute("open");
  }

  function initTheme() {
    let saved = null;
    try { saved = localStorage.getItem("modscope.theme"); } catch (_) {}
    const theme = saved === "light" ? "light" : "dark";
    document.documentElement.dataset.theme = theme;
    updateThemeLabel();
  }

  function updateThemeLabel() {
    const isDark = document.documentElement.dataset.theme !== "light";
    elements.themeToggle.setAttribute("aria-label", isDark ? "切换到浅色主题" : "切换到深色主题");
    elements.themeToggle.setAttribute("title", isDark ? "切换到浅色主题" : "切换到深色主题");
  }

  function toggleTheme() {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("modscope.theme", next); } catch (_) {}
    updateThemeLabel();
  }

  // ---------------------------------------------------------------------
  // Options
  // ---------------------------------------------------------------------
  function readOptions() {
    const javaText = elements.javaVersion.value.trim();
    const java = javaText ? Number(javaText) : null;
    if (java !== null && (!Number.isInteger(java) || java < 1 || java > 99)) {
      throw new Error("Java 主版本需要填写 1 到 99 之间的整数。");
    }
    return {
      mc_version: elements.mcVersion.value.trim(),
      loader: elements.loaderSelect.value,
      side: $(".segmented button.active", elements.sideSegmented)?.dataset.side || "client",
      java_version: java,
      deep_scan: elements.deepScan.checked,
      include_disabled: elements.includeDisabled.checked,
    };
  }

  function saveOptions() {
    try {
      localStorage.setItem("modscope.options", JSON.stringify(readOptions()));
    } catch (_) {}
  }

  function restoreOptions() {
    let options = null;
    try { options = JSON.parse(localStorage.getItem("modscope.options") || "null"); } catch (_) {}
    if (!options || typeof options !== "object") return;
    if (typeof options.mc_version === "string") elements.mcVersion.value = options.mc_version;
    if (["auto", "forge", "neoforge", "fabric", "quilt", "liteloader"].includes(options.loader)) {
      elements.loaderSelect.value = options.loader;
    }
    if (options.side === "server" || options.side === "client") {
      $$("button", elements.sideSegmented).forEach((button) => {
        button.classList.toggle("active", button.dataset.side === options.side);
      });
    }
    if (Number.isInteger(options.java_version)) elements.javaVersion.value = String(options.java_version);
    if (typeof options.deep_scan === "boolean") elements.deepScan.checked = options.deep_scan;
    if (typeof options.include_disabled === "boolean") elements.includeDisabled.checked = options.include_disabled;
  }

  function updateStartButton() {
    const hasFiles = state.selectedFiles.length > 0;
    const hasPath = elements.pathInput.value.trim().length > 0;
    elements.startScanBtn.disabled = !hasFiles && !hasPath;
    elements.scanPathBtn.disabled = !hasPath;
    const text = elements.startScanBtn.querySelector(".button-text");
    if (text) {
      text.textContent = hasFiles
        ? `分析 ${state.selectedFiles.length} 个文件`
        : (hasPath ? "扫描路径" : "开始分析");
    }
  }

  // ---------------------------------------------------------------------
  // File selection
  // ---------------------------------------------------------------------
  function setSelection(files, sourceLabel = "") {
    const normalized = dedupeFiles(files);
    if (!normalized.length) {
      showToast("没有可分析的文件", "请选择 .jar 或 .litemod 文件；.disabled 文件也会被识别。", "error");
      return;
    }
    const signature = fileSignature(normalized);
    if (signature !== state.uploadSignature && state.uploadSessionId) {
      dropUploadSession();
    }
    state.selectedFiles = normalized;
    state.selectionBytes = normalized.reduce((sum, file) => sum + file.size, 0);
    elements.dropzone.classList.add("is-ready");
    elements.selectionChip.hidden = false;
    elements.selectionText.textContent = `已选择 ${normalized.length} 个文件 · ${fmtBytes(state.selectionBytes)}`;
    elements.selectionText.title = normalized.map((file) => file.name).join("\n");
    // The start button lives below the fold on shorter screens.  Bring the
    // action into view so selecting a folder always has an obvious next step.
    window.setTimeout(() => {
      elements.startScanBtn.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }, 80);
    // Selecting uploadable files means the path field should not compete for
    // the primary action.
    if (sourceLabel) showToast("文件已就绪", `${normalized.length} 个 MOD 文件，点击“开始分析”即可扫描。`);
    updateStartButton();
  }

  function clearSelection() {
    if (state.uploadSessionId) dropUploadSession();
    state.selectedFiles = [];
    state.selectionBytes = 0;
    elements.selectionChip.hidden = true;
    elements.dropzone.classList.remove("is-ready");
    elements.folderInput.value = "";
    elements.filesInput.value = "";
    updateStartButton();
  }

  function fileSignature(files) {
    return files
      .map((file) => `${file.name}:${file.size}:${file.lastModified}`)
      .sort()
      .join("|");
  }

  async function handleFolderInput(event) {
    const files = Array.from(event.target.files || []);
    const topLevel = files.filter((file) => {
      const relative = file.webkitRelativePath || file.name;
      const segments = relative.split("/");
      return segments.length <= 2;
    });
    if (files.length && !topLevel.length) {
      showToast("没有找到根目录下的 MOD", "mods 文件夹中的子文件夹不会被游戏加载，已跳过。", "error");
    } else {
      setSelection(topLevel);
    }
    event.target.value = "";
  }

  function handleFilesInput(event) {
    setSelection(Array.from(event.target.files || []));
    event.target.value = "";
  }

  function fileFromEntry(entry) {
    return new Promise((resolve, reject) => {
      entry.file((file) => resolve(file), reject);
    });
  }

  function readDirectories(directoryReader) {
    return new Promise((resolve, reject) => {
      const all = [];
      const readBatch = () => {
        directoryReader.readEntries((entries) => {
          if (!entries.length) {
            resolve(all);
            return;
          }
          all.push(...entries);
          readBatch();
        }, reject);
      };
      readBatch();
    });
  }

  async function filesFromDrop(dataTransfer) {
    const items = Array.from(dataTransfer.items || []);
    const entries = [];
    for (const item of items) {
      if (item.kind !== "file") continue;
      if (typeof item.webkitGetAsEntry === "function") {
        const entry = item.webkitGetAsEntry();
        if (entry) entries.push(entry);
      }
    }
    if (!entries.length) return Array.from(dataTransfer.files || []);
    const files = [];
    for (const entry of entries) {
      if (entry.isFile) {
        try { files.push(await fileFromEntry(entry)); } catch (_) {}
      } else if (entry.isDirectory) {
        try {
          const children = await readDirectories(entry.createReader());
          for (const child of children) {
            if (child.isFile) {
              try { files.push(await fileFromEntry(child)); } catch (_) {}
            }
          }
        } catch (_) {}
      }
    }
    return files;
  }

  function initDropzone() {
    let dragDepth = 0;
    elements.dropzone.addEventListener("click", (event) => {
      if (event.target.closest("button, input, .chip-remove")) return;
      elements.folderInput.click();
    });
    elements.dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        elements.folderInput.click();
      }
    });
    $("#chooseFolderBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      elements.folderInput.click();
    });
    $("#chooseFilesBtn").addEventListener("click", (event) => {
      event.stopPropagation();
      elements.filesInput.click();
    });
    elements.clearSelection.addEventListener("click", (event) => {
      event.stopPropagation();
      clearSelection();
    });
    elements.folderInput.addEventListener("change", handleFolderInput);
    elements.filesInput.addEventListener("change", handleFilesInput);

    elements.dropzone.addEventListener("dragenter", (event) => {
      event.preventDefault();
      event.stopPropagation();
      dragDepth += 1;
      elements.dropzone.classList.add("dragging");
    });
    elements.dropzone.addEventListener("dragover", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
    });
    elements.dropzone.addEventListener("dragleave", (event) => {
      event.preventDefault();
      dragDepth = Math.max(0, dragDepth - 1);
      if (!dragDepth) elements.dropzone.classList.remove("dragging");
    });
    elements.dropzone.addEventListener("drop", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      dragDepth = 0;
      elements.dropzone.classList.remove("dragging");
      if (!event.dataTransfer) return;
      const files = await filesFromDrop(event.dataTransfer);
      if (!files.length) {
        showToast("没有识别到文件", "请拖入 mods 文件夹，或直接拖入 .jar / .litemod 文件。", "error");
        return;
      }
      setSelection(files, "drop");
    });
    ["dragenter", "dragover", "drop"].forEach((name) => {
      window.addEventListener(name, (event) => {
        if (!event.dataTransfer || !Array.from(event.dataTransfer.types || []).includes("Files")) return;
        if (!elements.landing.hidden) {
          event.preventDefault();
          if (name === "drop" && event.target !== elements.dropzone && !elements.dropzone.contains(event.target)) {
            event.stopPropagation();
          }
        }
      });
    });
  }

  // ---------------------------------------------------------------------
  // Scan flow
  // ---------------------------------------------------------------------
  function setView(name) {
    elements.landing.hidden = name !== "landing";
    elements.scanning.hidden = name !== "scanning";
    elements.report.hidden = name !== "report";
    window.scrollTo({ top: 0, behavior: "instant" in document.documentElement.style ? "instant" : "auto" });
  }

  function setProgress(value) {
    const normalized = Math.max(0, Math.min(100, value));
    state.progressValue = normalized;
    elements.progressBar.style.width = `${normalized.toFixed(1)}%`;
    elements.progressPercent.textContent = `${Math.round(normalized)}%`;
  }

  function setPhase(index) {
    state.phaseIndex = index;
    const phases = $$(".phase", elements.scanPhases);
    const allDone = index >= PHASES.length;
    phases.forEach((phase, phaseIndex) => {
      phase.classList.toggle("active", !allDone && phaseIndex === index);
      phase.classList.toggle("done", allDone || phaseIndex < index);
    });
    if (allDone) return;
    const data = PHASES[Math.min(index, PHASES.length - 1)];
    if (data && state.scanning) {
      elements.scanningTitle.textContent = data.title;
      elements.scanningSubtitle.textContent = data.subtitle;
    }
  }

  function startPhaseTicker(startIndex = 0) {
    stopScanTimers();
    setPhase(startIndex);
    state.phaseTimer = window.setInterval(() => {
      if (state.phaseIndex < PHASES.length - 1) setPhase(state.phaseIndex + 1);
    }, 1450);
  }

  function startFakeProgress(from, to = 91) {
    window.clearInterval(state.progressTimer);
    state.progressValue = from;
    setProgress(from);
    state.progressTimer = window.setInterval(() => {
      if (!state.scanning) return;
      const remaining = to - state.progressValue;
      if (remaining <= 0.2) return;
      setProgress(state.progressValue + Math.max(0.35, remaining * 0.045));
    }, 300);
  }

  function stopScanTimers() {
    window.clearInterval(state.phaseTimer);
    window.clearInterval(state.progressTimer);
    state.phaseTimer = null;
    state.progressTimer = null;
  }

  function resetScanningUI() {
    stopScanTimers();
    elements.scanning.classList.remove("is-indeterminate");
    elements.scanning.querySelector(".scanning-card")?.classList.remove("is-indeterminate");
    setProgress(0);
    elements.progressText.textContent = "准备扫描";
    $$(".phase", elements.scanPhases).forEach((phase) => {
      phase.classList.remove("active", "done");
    });
  }

  function showScanning() {
    resetScanningUI();
    state.scanning = true;
    elements.scanningTitle.textContent = "正在准备扫描…";
    elements.scanningSubtitle.textContent = "读取元数据、构建依赖图、比对类文件。";
    elements.cancelScanBtn.disabled = false;
    elements.cancelScanBtn.textContent = "取消扫描";
    setView("scanning");
    document.title = "扫描中… · ModScope";
    setProgress(2);
    setPhase(0);
    startFakeProgress(2, 16);
  }

  function cancelScan() {
    if (!state.scanning) return;
    state.scanId += 1;
    state.scanning = false;
    stopScanTimers();
    if (state.scanController) {
      try { state.scanController.abort(); } catch (_) {}
      state.scanController = null;
    }
    for (const xhr of state.activeXhrs) {
      try { xhr.abort(); } catch (_) {}
    }
    state.activeXhrs.clear();
    if (state.uploadSessionId) dropUploadSession();
    setView("landing");
    document.title = "ModScope · 我的世界 MOD 冲突查找器";
    showToast("扫描已取消", "没有修改任何 MOD 文件。");
  }

  async function startScan(mode = null) {
    if (state.scanning) return;
    let options;
    try {
      options = readOptions();
      saveOptions();
    } catch (error) {
      showToast("选项有误", error.message, "error");
      return;
    }
    let scanKind = mode;
    if (!scanKind) scanKind = state.selectedFiles.length ? "upload" : "path";
    if (scanKind === "upload" && !state.selectedFiles.length) {
      showToast("还没有选择文件", "先选择 mods 文件夹或 JAR 文件。", "error");
      return;
    }
    if (scanKind === "path" && !elements.pathInput.value.trim()) {
      showToast("还没有填写路径", "请粘贴 mods 文件夹地址，或选择文件。", "error");
      return;
    }
    const scanId = ++state.scanId;
    state.scanning = true;
    state.scanController = new AbortController();
    showScanning();
    let uploadFinished = scanKind !== "upload";
    if (scanKind === "upload" && state.uploadSessionId && state.uploadSignature === fileSignature(state.selectedFiles)) {
      uploadFinished = true;
    }
    if (scanKind === "upload") {
      elements.scanningTitle.textContent = "正在接收 MOD 文件…";
      elements.scanningSubtitle.textContent = "文件仅发送到本机分析服务，不会离开这台电脑。";
    } else {
      elements.scanningTitle.textContent = "正在读取本机文件夹…";
      elements.scanningSubtitle.textContent = "直接读取 mods 目录，不会复制 JAR 文件。";
      elements.progressText.textContent = "正在读取本机目录并构建依赖图";
      startPhaseTicker(0);
      startFakeProgress(6, 88);
    }
    try {
      let response;
      if (scanKind === "upload") {
        const signature = fileSignature(state.selectedFiles);
        if (!state.uploadSessionId || state.uploadSignature !== signature) {
          if (state.uploadSessionId) dropUploadSession();
          const created = await api("/api/uploads", {
            method: "POST", json: {}, signal: state.scanController.signal,
          });
          if (!created.session_id) throw new Error("本地服务没有返回上传会话。");
          state.uploadSessionId = created.session_id;
          state.uploadSignature = signature;
          state.savedSignature = signature;
          await uploadSelectedFiles(scanId);
        }
        uploadFinished = true;
        if (state.scanId !== scanId || !state.scanning) return;
        setPhase(1);
        startPhaseTicker(1);
        startFakeProgress(Math.max(state.progressValue, 37), 91);
        response = await api("/api/scan", {
          method: "POST",
          json: { upload_session: state.uploadSessionId, ...options },
          signal: state.scanController.signal,
        });
      } else {
        response = await api("/api/scan", {
          method: "POST",
          json: { path: elements.pathInput.value.trim(), ...options },
          signal: state.scanController.signal,
        });
      }
      if (state.scanId !== scanId || !state.scanning) return;
      if (!response.report) throw new Error("扫描没有返回报告。");
      stopScanTimers();
      setProgress(100);
      setPhase(PHASES.length);
      elements.scanningTitle.textContent = "分析完成";
      elements.scanningSubtitle.textContent = "正在整理诊断结果…";
      state.scanning = false;
      await sleep(280);
      if (state.scanId !== scanId) return;
      state.report = response.report;
      setReport(response.report);
      setView("report");
      document.title = "扫描结果 · ModScope";
    } catch (error) {
      if (error.name === "AbortError" || state.scanId !== scanId) return;
      console.error(error);
      state.scanning = false;
      stopScanTimers();
      if (state.scanController) {
        try { state.scanController.abort(); } catch (_) {}
      }
      if (scanKind === "upload" && !uploadFinished) {
        if (state.uploadSessionId) dropUploadSession();
      } else if (scanKind === "upload" && /上传会话|过期/.test(error.message || "")) {
        if (state.uploadSessionId) dropUploadSession();
      }
      setView("landing");
      document.title = "ModScope · 我的世界 MOD 冲突查找器";
      showToast("扫描失败", error.message || "未知错误", "error");
    } finally {
      if (state.scanId === scanId) {
        state.scanning = false;
        state.scanController = null;
        state.activeXhrs.clear();
      }
    }
  }

  function uploadFileXhr(sessionId, file, onProgress, signal) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      state.activeXhrs.add(xhr);
      let settled = false;
      const cleanup = () => {
        state.activeXhrs.delete(xhr);
        if (signal) signal.removeEventListener("abort", abortHandler);
      };
      const abortHandler = () => {
        if (!settled) {
          settled = true;
          cleanup();
          try { xhr.abort(); } catch (_) {}
          reject(new DOMException("Aborted", "AbortError"));
        }
      };
      if (signal) {
        if (signal.aborted) {
          abortHandler();
          return;
        }
        signal.addEventListener("abort", abortHandler, { once: true });
      }
      xhr.open("POST", `/api/upload/${encodeURIComponent(sessionId)}?name=${encodeURIComponent(file.name)}`);
      xhr.setRequestHeader("X-ModScope-Request", "1");
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress(event.loaded);
      });
      xhr.onload = () => {
        if (settled) return;
        settled = true;
        cleanup();
        let payload = null;
        try { payload = JSON.parse(xhr.responseText || "{}"); } catch (_) {}
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress(file.size);
          resolve(payload);
        } else {
          reject(new Error((payload && payload.error) || `上传失败（HTTP ${xhr.status}）`));
        }
      };
      xhr.onerror = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new Error("上传过程中与本地服务的连接中断。"));
      };
      xhr.onabort = () => {
        if (settled) return;
        settled = true;
        cleanup();
        reject(new DOMException("Aborted", "AbortError"));
      };
      xhr.send(file);
    });
  }

  async function uploadSelectedFiles(scanId) {
    const files = state.selectedFiles.slice();
    const totalBytes = Math.max(1, files.reduce((sum, file) => sum + file.size, 0));
    const loadedByFile = new Map(files.map((file) => [file, 0]));
    let completed = 0;
    const updateProgress = () => {
      const loaded = Array.from(loadedByFile.values()).reduce((sum, value) => sum + value, 0);
      setProgress(Math.min(34, (loaded / totalBytes) * 34));
      elements.progressText.textContent = `上传到本地分析器 · ${completed}/${files.length} 个文件`;
    };
    elements.progressText.textContent = `准备接收 ${files.length} 个文件`;
    updateProgress();
    const queue = files.slice();
    const workers = Array.from({ length: Math.min(3, queue.length) }, async () => {
      while (queue.length) {
        if (state.scanId !== scanId || !state.scanning) {
          throw new DOMException("Aborted", "AbortError");
        }
        const file = queue.shift();
        await uploadFileXhr(
          state.uploadSessionId,
          file,
          (loaded) => {
            loadedByFile.set(file, Math.max(loadedByFile.get(file) || 0, Math.min(loaded, file.size)));
            updateProgress();
          },
          state.scanController.signal,
        );
        completed += 1;
        loadedByFile.set(file, file.size);
        updateProgress();
      }
    });
    await Promise.all(workers);
    setProgress(34);
    setPhase(1);
    elements.progressText.textContent = "文件已就绪，开始静态分析";
  }

  async function dropUploadSession() {
    const sessionId = state.uploadSessionId;
    state.uploadSessionId = null;
    state.uploadSignature = "";
    if (!sessionId) return;
    try {
      await api(`/api/uploads/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    } catch (_) {}
  }

  function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  // ---------------------------------------------------------------------
  // Report rendering
  // ---------------------------------------------------------------------
  function setReport(report) {
    state.report = report;
    state.activeTab = "findings";
    state.severity = "all";
    state.category = "all";
    state.findingSearch = "";
    state.modSearch = "";
    elements.findingSearch.value = "";
    elements.modSearch.value = "";
    const activeCount = (report.mods || []).filter((mod) => mod.status === "active").length;
    state.modStatus = activeCount ? "active" : "all";
    renderReport(report);
  }

  function renderReport(report) {
    renderSummary(report);
    renderFilters(report);
    renderFindings();
    renderMods();
    renderDetails(report);
    setTab("findings");
    elements.tabFindingCount.textContent = String(report.findings.length);
    elements.tabModCount.textContent = String(report.mods.length);
    elements.reportSource.textContent = report.source?.label || "—";
    elements.reportSource.title = report.source?.label || "";
    elements.generatedAt.textContent = fmtDate(report.generated_at);
  }

  function renderSummary(report) {
    const summary = report.summary;
    const target = report.target || {};
    const stats = report.stats || {};
    const score = Number(summary.health_score) || 0;
    elements.reportHeadline.textContent = summary.headline || "扫描完成。";
    elements.gradeLabel.textContent = summary.grade_label || "—";
    elements.scanDuration.textContent = `· ${fmtDuration(report.duration_ms)}`;
    setScore(score);
    elements.reportMeta.innerHTML = [
      metaChip("i-cube", "加载器", target.loader_label || loaderLabel(target.loader)),
      metaChip("i-refresh", "Minecraft", report.options.mc_version || "未指定"),
      metaChip("i-layers", "运行端", report.options.side === "server" ? "服务端" : "客户端"),
      report.options.java_version ? metaChip("i-code", "Java", String(report.options.java_version)) : "",
      metaChip("i-scan", "深度扫描", report.options.deep_scan ? "开启" : "关闭"),
    ].join("");
    const counts = [
      { type: "error", count: summary.errors, label: "严重" },
      { type: "warning", count: summary.warnings, label: "警告" },
      { type: "notice", count: summary.notices, label: "提示" },
    ];
    elements.reportCounts.innerHTML = counts.map((item) => `
      <div class="count-pill ${item.type}">
        <i></i><div><strong>${Number(item.count) || 0}</strong><span>${escapeHtml(item.label)}</span></div>
      </div>
    `).join("");
    const classConflicts = (stats.core_conflicts || 0) + (stats.library_conflicts || 0);
    elements.quickStats.innerHTML = [
      quickStat("启用 MOD", stats.active_mods ?? "—"),
      quickStat("JAR 文件", stats.jar_count ?? "—"),
      quickStat("已禁用", stats.disabled_mods ?? "—"),
      quickStat("内置 JAR", stats.nested_mods ?? "—"),
      quickStat("类冲突", classConflicts || "0"),
      quickStat("扫描耗时", fmtDuration(report.duration_ms).replace(" s", "s")),
    ].join("");
  }

  function metaChip(icon, label, value) {
    return `<span class="meta-chip"><svg class="icon"><use href="#${icon}"/></svg>${escapeHtml(label)} <strong>${escapeHtml(value)}</strong></span>`;
  }

  function quickStat(label, value) {
    const display = String(value);
    const suffix = display.endsWith("s") && /^\d/.test(display) ? "" : "";
    return `<div class="quick-stat"><small>${escapeHtml(label)}</small><strong>${escapeHtml(display)}${suffix}</strong></div>`;
  }

  function setScore(score) {
    const color = score >= 85 ? "var(--accent)" : (score >= 60 ? "var(--amber)" : "var(--red)");
    elements.scoreRing.style.setProperty("--score-color", color);
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const target = Math.max(0, Math.min(100, score));
    elements.scoreValue.textContent = String(target);
    elements.scoreRing.style.setProperty("--score", String(target));
    if (reduced) return;
    const start = performance.now();
    const duration = 780;
    const from = 0;
    const tick = (now) => {
      const progress = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      const value = from + (target - from) * eased;
      elements.scoreValue.textContent = String(Math.round(value));
      elements.scoreRing.style.setProperty("--score", value.toFixed(2));
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function renderFilters(report) {
    const counts = { all: report.findings.length, error: 0, warning: 0, notice: 0 };
    const categoryCounts = {};
    for (const finding of report.findings) {
      counts[finding.severity] = (counts[finding.severity] || 0) + 1;
      categoryCounts[finding.category] = (categoryCounts[finding.category] || 0) + 1;
    }
    const severityItems = [
      ["all", "全部问题", counts.all, ""],
      ["error", "严重", counts.error, "error"],
      ["warning", "警告", counts.warning, "warning"],
      ["notice", "提示", counts.notice, "notice"],
    ];
    elements.severityFilters.innerHTML = severityItems.map(([value, label, count, type]) => `
      <button type="button" class="filter-button ${type} ${state.severity === value ? "active" : ""}"
              data-severity-filter="${value}">
        <span class="dot"></span>${escapeHtml(label)}<span class="count">${count}</span>
      </button>
    `).join("");
    const categoryItems = Object.keys(categoryCounts).sort((a, b) => {
      const order = ["loader", "dependency", "version", "integrity", "duplicate", "class", "mixin", "resource", "metadata", "info"];
      return order.indexOf(a) - order.indexOf(b);
    });
    elements.categoryFilters.innerHTML = `
      <button type="button" class="filter-button ${state.category === "all" ? "active" : ""}" data-category-filter="all">
        <span class="dot"></span>全部类别<span class="count">${report.findings.length}</span>
      </button>
    ` + categoryItems.map((category) => `
      <button type="button" class="filter-button ${state.category === category ? "active" : ""}"
              data-category-filter="${escapeAttr(category)}">
        <span class="dot"></span>${escapeHtml(categoryLabel(category))}<span class="count">${categoryCounts[category]}</span>
      </button>
    `).join("");
    $(".filter-rail").hidden = report.findings.length === 0;
    $(".findings-layout").classList.toggle("no-rail", report.findings.length === 0);
  }

  function filteredFindings() {
    if (!state.report) return [];
    const query = state.findingSearch.trim().toLowerCase();
    return state.report.findings.filter((finding) => {
      if (state.severity !== "all" && finding.severity !== state.severity) return false;
      if (state.category !== "all" && finding.category !== state.category) return false;
      if (!query) return true;
      const haystack = [
        finding.title, finding.summary, finding.detail, finding.recommendation,
        ...(finding.evidence || []),
        ...(finding.mods || []).flatMap((mod) => [mod.id, mod.name, mod.file]),
        finding.category, finding.severity,
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    });
  }

  function renderFindings() {
    if (!state.report) return;
    const allFindings = state.report.findings || [];
    if (!allFindings.length) {
      elements.findingsList.innerHTML = `
        <div class="findings-clear">
          <div class="clear-mark"><svg class="icon"><use href="#i-check"/></svg></div>
          <div>
            <h3>没有发现需要处理的冲突</h3>
            <p>依赖、版本、加载器、类与 Mixin 检查均已通过。仍建议保留一次启动日志，作为动态行为的最后确认。</p>
          </div>
        </div>
      `;
      elements.findingsEmpty.hidden = true;
      elements.resultCount.textContent = "0 条结果";
      return;
    }
    const findings = filteredFindings();
    elements.resultCount.textContent = `${findings.length} 条结果`;
    elements.findingsEmpty.hidden = findings.length > 0;
    elements.findingsList.innerHTML = findings.map(renderFindingCard).join("");
  }

  function renderFindingCard(finding) {
    const severity = finding.severity || "notice";
    const category = finding.category || "info";
    const confidence = finding.confidence || "high";
    const confidenceLabel = confidence === "medium" ? "中等置信度" : (confidence === "low" ? "较低置信度" : "");
    const mods = finding.mods || [];
    const seenMods = new Set();
    const uniqueMods = mods.filter((mod) => {
      const key = `${mod.id || ""}\u0000${mod.name || ""}\u0000${mod.version || ""}`;
      if (seenMods.has(key)) return false;
      seenMods.add(key);
      return true;
    });
    const shownMods = uniqueMods.slice(0, 6);
    const evidence = finding.evidence || [];
    const countText = finding.count && finding.count > 1 ? `涉及 ${finding.count} 项` : "";
    return `
      <article class="finding-card severity-${escapeAttr(severity)}">
        <div class="finding-card-inner">
          <div class="finding-head">
            <div class="finding-icon"><svg class="icon"><use href="#${ICON[category] || "i-alert"}"/></svg></div>
            <div class="finding-title-wrap">
              <div class="finding-badges">
                <span class="severity-badge ${escapeAttr(severity)}">${escapeHtml(severityLabel(severity))}</span>
                <span class="category-badge">${escapeHtml(categoryLabel(category))}</span>
                ${confidenceLabel ? `<span class="confidence-badge">${escapeHtml(confidenceLabel)}</span>` : ""}
              </div>
              <h3 class="finding-title">${escapeHtml(finding.title)}</h3>
              <p class="finding-summary">${escapeHtml(finding.summary)}</p>
              ${finding.detail ? `<p class="finding-detail">${escapeHtml(finding.detail)}</p>` : ""}
            </div>
          </div>
          <div class="finding-body">
            ${shownMods.length ? `<div class="finding-mods">${shownMods.map((mod) => `
              <span class="mod-pill" title="${escapeAttr(`${mod.name || mod.id}${mod.version ? " " + mod.version : ""} · ${mod.file || ""}`)}">
                <strong>${escapeHtml(mod.name || mod.id)}</strong>${mod.id && mod.name !== mod.id ? `<span>${escapeHtml(mod.id)}</span>` : ""}
              </span>
            `).join("")}${uniqueMods.length > shownMods.length ? `<span class="mod-pill more">+${uniqueMods.length - shownMods.length}</span>` : ""}</div>` : ""}
            ${finding.recommendation ? `<div class="recommendation"><svg class="icon"><use href="#i-spark"/></svg><span><strong>建议</strong>${escapeHtml(finding.recommendation)}</span></div>` : ""}
            ${evidence.length ? `<details class="evidence-details">
              <summary>查看证据 · ${evidence.length} 条</summary>
              <ul class="evidence-list">${evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>
            </details>` : ""}
            <div class="finding-foot">
              <span class="count-hint">${escapeHtml(countText)}</span>
              <span class="confidence">${finding.id ? `#${escapeHtml(finding.id)}` : ""}</span>
            </div>
          </div>
        </div>
      </article>
    `;
  }

  // ---------------------------------------------------------------------
  // Mods tab
  // ---------------------------------------------------------------------
  function renderMods() {
    if (!state.report) return;
    const mods = state.report.mods || [];
    const query = state.modSearch.trim().toLowerCase();
    const filtered = mods.filter((mod) => {
      if (state.modStatus !== "all" && mod.status !== state.modStatus) return false;
      if (!query) return true;
      const haystack = [
        mod.id, mod.name, mod.version, mod.file, mod.loader_label,
        ...(mod.mc_constraints || []), ...(mod.requires || []), ...(mod.provides || []),
        mod.description,
      ].join(" ").toLowerCase();
      return haystack.includes(query);
    });
    elements.modResultCount.textContent = `${filtered.length} 个 MOD`;
    elements.modsEmpty.hidden = filtered.length > 0;
    elements.modsTableBody.innerHTML = filtered.map((mod) => {
      const status = mod.status || "active";
      const loaders = (mod.loaders && mod.loaders.length) ? mod.loaders : [mod.loader || "unknown"];
      const loaderLabels = loaders.map(loaderLabel).join(" + ");
      const mc = (mod.mc_constraints && mod.mc_constraints.length)
        ? mod.mc_constraints.join(" / ")
        : "未声明";
      const issueTitle = (mod.issues || []).join("\n");
      const fileTitle = mod.parent_file ? `${mod.file} → ${mod.parent_file}` : mod.file;
      return `
        <tr>
          <td>
            <div class="mod-cell">
              <span class="mod-avatar">${escapeHtml((mod.name || mod.id || "?").slice(0, 1).toUpperCase())}</span>
              <div class="mod-info">
                <strong title="${escapeAttr(mod.name || mod.id)}">${escapeHtml(mod.name || mod.id)}
                  ${(mod.issues || []).length ? `<span class="row-warning" title="${escapeAttr(issueTitle)}"></span>` : ""}
                </strong>
                <span>${escapeHtml(mod.id)} · ${escapeHtml(mod.file)}</span>
              </div>
            </div>
          </td>
          <td><span class="version-cell">${escapeHtml(mod.version || "—")}</span></td>
          <td><span class="loader-badge ${loaderClass(mod.loader)}">${escapeHtml(loaderLabels || "未知")}</span></td>
          <td><span class="mc-cell" title="${escapeAttr(mc)}">${escapeHtml(mc)}</span></td>
          <td><span class="java-cell" title="${mod.class_major ? `class version ${mod.class_major}` : "未读取" }">${mod.java_required ? `Java ${mod.java_required}+` : "—"}</span></td>
          <td><span class="status-badge ${escapeAttr(status)}">${escapeHtml(STATUS_LABEL[status] || status)}</span></td>
        </tr>
      `;
    }).join("");
    elements.modStatusFilter.querySelectorAll("button").forEach((button) => {
      button.classList.toggle("active", button.dataset.status === state.modStatus);
    });
  }

  // ---------------------------------------------------------------------
  // Details tab
  // ---------------------------------------------------------------------
  function renderDetails(report) {
    const target = report.target || {};
    const options = report.options || {};
    const stats = report.stats || {};
    const loaderRows = (target.loaders || []).length
      ? target.loaders.map((item) => `<span class="meta-chip"><strong>${escapeHtml(item.label)}</strong> ${item.count}</span>`).join("")
      : `<span class="meta-chip">未知</span>`;
    const detected = (target.detected_mc_versions || []).length
      ? target.detected_mc_versions.map((item) => `<span class="meta-chip"><strong>${escapeHtml(item.version)}</strong> × ${item.count}</span>`).join("")
      : `<span class="meta-chip">没有精确版本声明</span>`;
    const constraintRows = (target.mc_constraints || []).length
      ? target.mc_constraints.map((item) => `<span class="meta-chip">${escapeHtml(item.constraint)} <strong>× ${item.count}</strong></span>`).join("")
      : "";
    const side = options.side === "server" ? "服务端" : "客户端";
    elements.detailsGrid.innerHTML = `
      <div class="details-card">
        <div class="card-heading"><div><span class="section-kicker">TARGET</span><h3>扫描目标</h3></div></div>
        <div class="detail-kv">
          <div class="detail-row"><span>来源</span><strong title="${escapeAttr(report.source?.label || "")}">${escapeHtml(report.source?.label || "—")}</strong></div>
          <div class="detail-row"><span>加载器</span><strong>${escapeHtml(target.loader_label || loaderLabel(target.loader))}</strong></div>
          <div class="detail-row"><span>Minecraft</span><strong>${escapeHtml(options.mc_version || "未指定")}</strong></div>
          <div class="detail-row"><span>运行端</span><strong>${side}</strong></div>
          <div class="detail-row"><span>Java</span><strong>${options.java_version ? `Java ${options.java_version}` : "未指定"}</strong></div>
          <div class="detail-row"><span>深度扫描</span><strong>${options.deep_scan ? "开启" : "关闭"}</strong></div>
        </div>
      </div>
      <div class="details-card">
        <div class="card-heading"><div><span class="section-kicker">VERSION SIGNALS</span><h3>版本线索</h3></div></div>
        <span class="muted">加载器分布</span>
        <div class="detail-kv" style="margin:7px 0 13px"><div class="mods-chips">${loaderRows}</div></div>
        <span class="muted">检测到的精确游戏版本</span>
        <div class="detail-kv" style="margin:7px 0 13px"><div class="mods-chips">${detected}</div></div>
        ${constraintRows ? `<span class="muted">常见约束</span><div class="detail-kv" style="margin-top:7px"><div class="mods-chips">${constraintRows}</div></div>` : ""}
        <div class="insight-note">${target.common_mc_range && target.common_mc_range !== "*"
          ? `所有已声明约束的公共区间：<strong>${escapeHtml(target.common_mc_range)}</strong>`
          : "未指定 Minecraft 版本时，只检查 MOD 声明的版本范围是否有公共交集。"}</div>
      </div>
      <div class="details-card">
        <div class="card-heading"><div><span class="section-kicker">AT A GLANCE</span><h3>统计信息</h3></div></div>
        <div class="detail-kv">
          <div class="detail-row"><span>有效 JAR</span><strong>${stats.valid_jars ?? 0} / ${stats.scanned_files ?? 0}</strong></div>
          <div class="detail-row"><span>启用 MOD</span><strong>${stats.active_mods ?? 0}</strong></div>
          <div class="detail-row"><span>已禁用</span><strong>${stats.disabled_mods ?? 0}</strong></div>
          <div class="detail-row"><span>内置 JAR</span><strong>${stats.nested_mods ?? 0}</strong></div>
          <div class="detail-row"><span>类文件</span><strong>${stats.class_count ?? 0}</strong></div>
          <div class="detail-row"><span>扫描耗时</span><strong>${fmtDuration(report.duration_ms)}</strong></div>
        </div>
      </div>
    `;
    // The dynamically inserted chips inherit the meta-chip layout.
    $$(".mods-chips", elements.detailsGrid).forEach((chips) => {
      chips.classList.add("mods-chips");
    });
    const jars = report.jars || [];
    elements.jarCountLabel.textContent = `${jars.length} 个文件`;
    elements.jarsTableBody.innerHTML = jars.map((jar) => {
      let status = "已识别";
      let statusClass = "active";
      if (jar.disabled) { status = "已禁用"; statusClass = "disabled"; }
      else if (!jar.valid) { status = "无法读取"; statusClass = "disabled"; }
      else if (!jar.recognized) { status = "未识别"; statusClass = "nested"; }
      const recognized = [
        ...(jar.mods || []),
        ...(jar.nested_mods || []).map((name) => `${name}（内置）`),
      ].join("、") || "—";
      const info = (jar.issues || []).length ? `<span class="issue-count" title="${escapeAttr((jar.issues || []).join("\n"))}">${jar.issues.length} 条提示</span>` : "";
      return `
        <tr>
          <td><div class="jar-file"><strong title="${escapeAttr(jar.file)}">${escapeHtml(jar.file)}</strong><small>${escapeHtml(jar.sha256 ? jar.sha256.slice(0, 12) : "—")} · ${fmtBytes(jar.size)}</small></div></td>
          <td><span>${escapeHtml(recognized)}</span>${info ? `<br>${info}` : ""}</td>
          <td><span class="java-cell">${jar.class_count ?? 0}</span></td>
          <td><span class="java-cell">${jar.java_required ? `Java ${jar.java_required}+` : "—"}</span></td>
          <td><span class="status-badge ${statusClass}">${escapeHtml(status)}</span></td>
        </tr>
      `;
    }).join("");
    elements.caveatsList.innerHTML = (report.caveats || []).map((caveat) => `<li>${escapeHtml(caveat)}</li>`).join("");
  }

  // ---------------------------------------------------------------------
  // Tabs and filters
  // ---------------------------------------------------------------------
  function setTab(tab) {
    state.activeTab = tab;
    $$(".tab").forEach((button) => {
      const selected = button.dataset.tab === tab;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    $("#panelFindings").hidden = tab !== "findings";
    $("#panelMods").hidden = tab !== "mods";
    $("#panelDetails").hidden = tab !== "details";
    if (tab === "mods") renderMods();
  }

  function initTabs() {
    $$(".tab").forEach((button) => {
      button.addEventListener("click", () => setTab(button.dataset.tab));
      button.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const tabs = $$(".tab");
        const index = tabs.indexOf(button);
        const next = event.key === "ArrowRight"
          ? tabs[(index + 1) % tabs.length]
          : tabs[(index - 1 + tabs.length) % tabs.length];
        next.focus();
        setTab(next.dataset.tab);
      });
    });
  }

  function initFilters() {
    elements.severityFilters.addEventListener("click", (event) => {
      const button = event.target.closest("[data-severity-filter]");
      if (!button) return;
      state.severity = button.dataset.severityFilter;
      renderFilters(state.report);
      renderFindings();
    });
    elements.categoryFilters.addEventListener("click", (event) => {
      const button = event.target.closest("[data-category-filter]");
      if (!button) return;
      state.category = button.dataset.categoryFilter;
      renderFilters(state.report);
      renderFindings();
    });
    let findTimer = null;
    elements.findingSearch.addEventListener("input", () => {
      state.findingSearch = elements.findingSearch.value;
      window.clearTimeout(findTimer);
      findTimer = window.setTimeout(renderFindings, 100);
    });
    elements.clearFiltersBtn.addEventListener("click", () => {
      state.severity = "all";
      state.category = "all";
      state.findingSearch = "";
      elements.findingSearch.value = "";
      renderFilters(state.report);
      renderFindings();
    });
    let modTimer = null;
    elements.modSearch.addEventListener("input", () => {
      state.modSearch = elements.modSearch.value;
      window.clearTimeout(modTimer);
      modTimer = window.setTimeout(renderMods, 100);
    });
    elements.modStatusFilter.addEventListener("click", (event) => {
      const button = event.target.closest("[data-status]");
      if (!button) return;
      state.modStatus = button.dataset.status;
      renderMods();
    });
  }

  // ---------------------------------------------------------------------
  // Exports
  // ---------------------------------------------------------------------
  function download(filename, content, mime = "text/plain;charset=utf-8") {
    const blob = new Blob([content], { type: mime });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function exportJson() {
    if (!state.report) return;
    download(
      `modscope-report-${fmtLocalTime()}.json`,
      JSON.stringify(state.report, null, 2),
      "application/json;charset=utf-8",
    );
    toastExport("JSON 报告已导出");
  }

  function markdownReport(report) {
    const summary = report.summary || {};
    const target = report.target || {};
    const stats = report.stats || {};
    const options = report.options || {};
    const lines = [
      "# ModScope · 我的世界 MOD 冲突报告",
      "",
      `> 生成时间：${report.generated_at || ""}`,
      `> 扫描来源：\`${report.source?.label || ""}\``,
      `> 健康评分：**${summary.health_score || 0} / 100** · ${summary.grade_label || ""}`,
      "",
      "## 扫描概况",
      "",
      `- MOD 文件：${stats.jar_count || 0} 个（有效 ${stats.valid_jars || 0}）`,
      `- 启用 MOD：${stats.active_mods || 0}；已禁用：${stats.disabled_mods || 0}；内置 JAR：${stats.nested_mods || 0}`,
      `- 加载器：${target.loader_label || "未知"}；运行端：${options.side === "server" ? "服务端" : "客户端"}`,
      `- Minecraft：${options.mc_version || "未指定（仅检查约束交集）"}；Java：${options.java_version || "未指定"}`,
      `- 结果：严重 ${summary.errors || 0} · 警告 ${summary.warnings || 0} · 提示 ${summary.notices || 0}`,
      "",
      "## 诊断结果",
      "",
    ];
    if (!(report.findings || []).length) {
      lines.push("没有发现需要处理的冲突。", "");
    }
    (report.findings || []).forEach((finding, index) => {
      lines.push(`### ${index + 1}. [${severityLabel(finding.severity)}][${categoryLabel(finding.category)}] ${finding.title}`);
      lines.push("");
      if (finding.summary) { lines.push(finding.summary, ""); }
      if (finding.detail) { lines.push(finding.detail, ""); }
      if ((finding.evidence || []).length) {
        lines.push("**证据**", "");
        for (const item of finding.evidence) lines.push(`- ${item}`);
        lines.push("");
      }
      if (finding.recommendation) lines.push(`**建议：** ${finding.recommendation}`, "");
    });
    lines.push("## 已扫描 MOD", "");
    lines.push("| 状态 | MOD | ID | 版本 | 加载器 | 文件 |", "| --- | --- | --- | --- | --- | --- |");
    for (const mod of report.mods || []) {
      const cells = [
        STATUS_LABEL[mod.status] || mod.status,
        mod.name, mod.id, mod.version || "-", mod.loader_label, mod.file,
      ].map((cell) => String(cell ?? "").replace(/\|/g, "\\|").replace(/\n/g, " "));
      lines.push(`| ${cells.join(" | ")} |`);
    }
    lines.push("", "## 检查边界", "");
    for (const caveat of report.caveats || []) lines.push(`- ${caveat}`);
    lines.push("", "---", "", "由 ModScope 本地生成。静态检查不能替代实际启动日志。", "");
    return lines.join("\n");
  }

  function exportMarkdown() {
    if (!state.report) return;
    download(
      `modscope-report-${fmtLocalTime()}.md`,
      markdownReport(state.report),
      "text/markdown;charset=utf-8",
    );
    toastExport("Markdown 报告已导出");
  }

  function toastExport(message) {
    showToast(message, "文件保存在浏览器默认下载位置。");
    closeExportMenu();
  }

  function preparePrintReport() {
    if (!state.report) return false;
    const report = state.report;
    const summary = report.summary || {};
    const stats = report.stats || {};
    const target = report.target || {};
    const options = report.options || {};
    const findings = report.findings || [];
    const mods = report.mods || [];
    const findingHtml = findings.length ? findings.map((finding) => `
      <div class="print-finding ${escapeAttr(finding.severity)}">
        <div class="print-sev">${escapeHtml(severityLabel(finding.severity))} · ${escapeHtml(categoryLabel(finding.category))}</div>
        <h3>${escapeHtml(finding.title)}</h3>
        <p>${escapeHtml(finding.summary)}</p>
        ${(finding.evidence || []).length ? `<ul>${finding.evidence.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>` : ""}
        ${finding.recommendation ? `<p><strong>建议：</strong>${escapeHtml(finding.recommendation)}</p>` : ""}
      </div>
    `).join("") : "<p>没有发现需要处理的冲突。</p>";
    const modRows = mods.map((mod) => `
      <tr>
        <td>${escapeHtml(STATUS_LABEL[mod.status] || mod.status)}</td>
        <td>${escapeHtml(mod.name || mod.id)}</td>
        <td>${escapeHtml(mod.id)}</td>
        <td>${escapeHtml(mod.version || "—")}</td>
        <td>${escapeHtml(mod.loader_label || "—")}</td>
        <td>${escapeHtml(mod.file || "—")}</td>
      </tr>
    `).join("");
    const caveatHtml = (report.caveats || []).map((caveat) => `<p class="print-caveat">— ${escapeHtml(caveat)}</p>`).join("");
    elements.printReport.innerHTML = `
      <h1>MOD 冲突扫描报告</h1>
      <div class="print-meta">ModScope v1.0 · 生成于 ${escapeHtml(fmtDate(report.generated_at))} · ${escapeHtml(report.source?.label || "")}</div>
      <div class="print-stats">
        <div class="print-stat"><strong>${summary.health_score || 0}/100</strong><span>${escapeHtml(summary.grade_label || "")}</span></div>
        <div class="print-stat"><strong>${summary.errors || 0}</strong><span>严重</span></div>
        <div class="print-stat"><strong>${summary.warnings || 0}</strong><span>警告</span></div>
        <div class="print-stat"><strong>${summary.notices || 0}</strong><span>提示</span></div>
        <div class="print-stat"><strong>${stats.active_mods || 0}</strong><span>启用 MOD</span></div>
      </div>
      <p>加载器：${escapeHtml(target.loader_label || "未知")}　Minecraft：${escapeHtml(options.mc_version || "未指定")}　运行端：${options.side === "server" ? "服务端" : "客户端"}　Java：${escapeHtml(options.java_version || "未指定")}</p>
      <h2>诊断结果</h2>
      ${findingHtml}
      <h2>已扫描 MOD</h2>
      <table><thead><tr><th>状态</th><th>MOD</th><th>ID</th><th>版本</th><th>加载器</th><th>文件</th></tr></thead><tbody>${modRows}</tbody></table>
      <h2>检查边界</h2>
      ${caveatHtml}
    `;
    elements.printReport.setAttribute("aria-hidden", "false");
    return true;
  }

  function openPrintReport() {
    if (!preparePrintReport()) return;
    closeExportMenu();
    window.setTimeout(() => window.print(), 40);
  }

  function toggleExportMenu(force) {
    const shouldOpen = typeof force === "boolean" ? force : elements.exportMenu.hidden;
    elements.exportMenu.hidden = !shouldOpen;
    elements.exportBtn.setAttribute("aria-expanded", String(shouldOpen));
    if (shouldOpen) {
      const first = elements.exportMenu.querySelector("button");
      if (first) first.focus();
    }
  }

  function closeExportMenu() {
    elements.exportMenu.hidden = true;
    elements.exportBtn.setAttribute("aria-expanded", "false");
  }

  function initExportMenu() {
    elements.exportBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleExportMenu();
    });
    elements.exportMenu.addEventListener("click", (event) => {
      const button = event.target.closest("[data-export]");
      if (!button) return;
      const type = button.dataset.export;
      if (type === "json") exportJson();
      else if (type === "markdown") exportMarkdown();
      else if (type === "print") openPrintReport();
    });
    document.addEventListener("click", (event) => {
      if (!elements.exportMenu.hidden && !elements.exportBtn.contains(event.target) && !elements.exportMenu.contains(event.target)) {
        closeExportMenu();
      }
    });
    elements.exportMenu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      closeExportMenu();
      elements.exportBtn.focus();
    });
  }

  async function copySummary() {
    if (!state.report) return;
    const summary = state.report.summary || {};
    const lines = [
      `ModScope · MOD 冲突扫描`,
      `评分：${summary.health_score || 0}/100（${summary.grade_label || ""}）`,
      `${summary.headline || ""}`,
    ];
    for (const finding of (state.report.findings || []).slice(0, 8)) {
      lines.push(`- [${severityLabel(finding.severity)}] ${finding.title}`);
    }
    if ((state.report.findings || []).length > 8) lines.push(`- 另有 ${state.report.findings.length - 8} 条问题`);
    const text = lines.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      showToast("结论已复制", "可直接粘贴到聊天窗口或工单。");
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      try {
        document.execCommand("copy");
        showToast("结论已复制");
      } catch (error) {
        showToast("复制失败", "浏览器拒绝了剪贴板访问。", "error");
      }
      area.remove();
    }
  }

  // ---------------------------------------------------------------------
  // Navigation / native picker
  // ---------------------------------------------------------------------
  async function chooseNativeFolder() {
    if (state.pickerBusy) return;
    state.pickerBusy = true;
    const original = elements.pickFolderBtn.textContent;
    elements.pickFolderBtn.disabled = true;
    elements.pickFolderBtn.textContent = "打开中…";
    try {
      const result = await api("/api/pick-folder", { method: "POST", json: {} });
      if (result.path) {
        elements.pathInput.value = result.path;
        elements.pathInput.dispatchEvent(new Event("input"));
        updateStartButton();
        elements.pathInput.focus();
        showToast("已填入本机路径", "点击“扫描路径”或“开始分析”开始。");
      }
    } catch (error) {
      showToast("无法打开系统选择器", `${error.message} 你也可以直接粘贴文件夹路径。`, "error");
    } finally {
      state.pickerBusy = false;
      elements.pickFolderBtn.disabled = false;
      elements.pickFolderBtn.textContent = original;
      updateStartButton();
    }
  }

  function resetToLanding() {
    setView("landing");
    document.title = "ModScope · 我的世界 MOD 冲突查找器";
    window.setTimeout(() => {
      const scanner = $("#scanner");
      if (scanner) scanner.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 20);
  }

  // ---------------------------------------------------------------------
  // Event wiring
  // ---------------------------------------------------------------------
  function initEvents() {
    initTheme();
    initDropzone();
    initTabs();
    initFilters();
    initExportMenu();
    restoreOptions();
    updateStartButton();

    elements.themeToggle.addEventListener("click", toggleTheme);
    $$("[data-open-help]").forEach((button) => button.addEventListener("click", openHelp));
    elements.closeHelp.addEventListener("click", closeHelp);
    elements.helpDialog.addEventListener("click", (event) => {
      if (event.target === elements.helpDialog) closeHelp();
    });

    elements.sideSegmented.addEventListener("click", (event) => {
      const button = event.target.closest("[data-side]");
      if (!button) return;
      $$("button", elements.sideSegmented).forEach((item) => item.classList.toggle("active", item === button));
      saveOptions();
    });
    elements.mcVersion.addEventListener("change", saveOptions);
    elements.loaderSelect.addEventListener("change", saveOptions);
    elements.javaVersion.addEventListener("change", saveOptions);
    elements.deepScan.addEventListener("change", saveOptions);
    elements.includeDisabled.addEventListener("change", saveOptions);

    elements.pathInput.addEventListener("input", updateStartButton);
    elements.pathInput.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      startScan("path");
    });
    elements.pickFolderBtn.addEventListener("click", chooseNativeFolder);
    elements.scanPathBtn.addEventListener("click", () => startScan("path"));
    elements.startScanBtn.addEventListener("click", () => startScan());
    elements.cancelScanBtn.addEventListener("click", cancelScan);
    elements.rescanBtn.addEventListener("click", resetToLanding);
    elements.copySummaryBtn.addEventListener("click", copySummary);
    elements.printBtn.addEventListener("click", () => {
      setTab("findings");
      openPrintReport();
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !elements.exportMenu.hidden) {
        closeExportMenu();
        elements.exportBtn.focus();
      }
    });

    window.addEventListener("beforeprint", () => {
      if (state.report) preparePrintReport();
    });

    // Keep the report filters and search state tidy when leaving a report.
    window.addEventListener("pagehide", () => {
      // Uploaded temp files are cleaned by the server TTL; no unload request
      // is sent because custom headers cannot be attached to sendBeacon.
    });
  }

  initEvents();
})();
