const API_BASE = "/api/v1";

const state = {
  token: localStorage.getItem("smart_qa_token") || "",
  sessionId: "",
  user: JSON.parse(localStorage.getItem("smart_qa_user") || "null"),
};

const ACTIVE_DOCUMENT_STATUSES = new Set(["PENDING", "PARSING", "CHUNKING", "INDEXING", "RUNNING", "RETRYING"]);
let documentPollTimer = null;

const $ = (selector) => document.querySelector(selector);

function setText(selector, value) {
  const el = $(selector);
  if (el) el.textContent = value;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

function setLoading(button, loadingText) {
  const original = button.textContent;
  button.disabled = true;
  button.textContent = loadingText;
  return () => {
    button.disabled = false;
    button.textContent = original;
  };
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();

  if (!response.ok) {
    const message = typeof payload === "object" ? payload.message || response.statusText : payload;
    throw new Error(message || `HTTP ${response.status}`);
  }

  if (payload && typeof payload === "object" && payload.code !== undefined && payload.code !== 0) {
    throw new Error(payload.message || "请求失败");
  }

  return payload;
}

function syncAuthView() {
  const loggedIn = Boolean(state.token);
  $("#login-panel").classList.toggle("hidden", loggedIn);
  $("#app-panel").classList.toggle("hidden", !loggedIn);
  $("#logout-btn").classList.toggle("hidden", !loggedIn);
  setText("#account-name", loggedIn ? state.user?.username || "admin" : "未登录");
  setText("#tenant-id", loggedIn ? state.user?.tenant_id || "-" : "未登录");
}

function addMessage(role, content) {
  const messages = $("#messages");
  const item = document.createElement("div");
  item.className = `message ${role}`;
  item.textContent = content;
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
  return item;
}

function renderSources(sources) {
  const box = $("#sources");
  box.innerHTML = "";
  if (!Array.isArray(sources) || sources.length === 0) {
    box.className = "sources empty-state";
    box.textContent = "暂无来源";
    return;
  }

  box.className = "sources";
  sources.forEach((source, index) => {
    const item = document.createElement("article");
    item.className = "source-item";
    const sourceName = source.source || source.file_name || source.filename || source.metadata?.source || `来源 ${index + 1}`;
    const score = source.score !== undefined ? `score: ${Number(source.score).toFixed(4)}` : "";
    const content = source.content || source.text || source.page_content || JSON.stringify(source, null, 2);
    item.innerHTML = `
      <h3>${escapeHtml(sourceName)}</h3>
      <p>${escapeHtml(score)}</p>
      <p>${escapeHtml(String(content).slice(0, 500))}</p>
    `;
    box.appendChild(item);
  });
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function refreshHealth() {
  try {
    const payload = await api("/health");
    setText("#health-status", payload.data?.status || "UP");
  } catch (error) {
    setText("#health-status", "异常");
    showToast(error.message);
  }
}

async function refreshKnowledgeInfo() {
  const payload = await api("/knowledge/info");
  const data = payload.data || {};
  setText("#collection-name", data.name || "-");
  setText("#points-count", String(data.points_count ?? "-"));
  setText("#collection-status", data.status || "-");
}

function formatFileSize(size) {
  if (!Number.isFinite(Number(size)) || Number(size) <= 0) return "-";
  const bytes = Number(size);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
}

function renderDocuments(items, total) {
  const body = $("#document-list");
  body.innerHTML = "";
  setText("#document-summary", total > 0 ? `共 ${total} 个文档` : "暂无文档");

  if (!Array.isArray(items) || items.length === 0) {
    body.innerHTML = '<tr><td class="table-empty" colspan="6">暂无文档</td></tr>';
    stopDocumentPolling();
    return;
  }

  items.forEach((item) => {
    const row = document.createElement("tr");
    const status = item.status || "UNKNOWN";
    const error = item.error_message ? `<p class="document-error">${escapeHtml(item.error_message)}</p>` : "";
    row.innerHTML = `
      <td><strong>${escapeHtml(item.file_name || "未命名文档")}</strong>${error}</td>
      <td><span class="status-badge status-${escapeHtml(status.toLowerCase())}">${escapeHtml(status)}</span></td>
      <td>${Number(item.chunk_count || 0)}</td>
      <td>${formatFileSize(item.file_size)}</td>
      <td>${formatTime(item.update_time)}</td>
      <td><button class="danger-btn" data-document-id="${escapeHtml(item.id)}" type="button">删除</button></td>
    `;
    body.appendChild(row);
  });

  const hasActiveTask = items.some((item) => ACTIVE_DOCUMENT_STATUSES.has(item.status));
  if (hasActiveTask) {
    startDocumentPolling();
  } else {
    stopDocumentPolling();
  }
}

async function refreshDocuments() {
  const payload = await api("/knowledge/documents?page=1&page_size=50");
  const data = payload.data || {};
  renderDocuments(data.items || [], Number(data.total || 0));
}

function startDocumentPolling() {
  if (documentPollTimer) return;
  documentPollTimer = window.setInterval(() => {
    refreshDocuments().catch(() => {});
  }, 3000);
}

function stopDocumentPolling() {
  if (!documentPollTimer) return;
  window.clearInterval(documentPollTimer);
  documentPollTimer = null;
}

async function deleteDocument(event) {
  const button = event.target.closest("[data-document-id]");
  if (!button) return;
  if (!window.confirm("删除后将同时移除原文件、向量切片和检索索引，确认继续？")) return;

  const restore = setLoading(button, "删除中");
  try {
    await api(`/knowledge/documents/${encodeURIComponent(button.dataset.documentId)}`, { method: "DELETE" });
    await Promise.all([refreshDocuments(), refreshKnowledgeInfo()]);
    showToast("文档已删除");
  } catch (error) {
    showToast(error.message);
  } finally {
    restore();
  }
}

async function backfillLegacyDocuments(event) {
  if (!window.confirm("将为当前租户未关联文档的历史向量补齐元数据，不会重新生成向量。确认继续？")) return;
  const restore = setLoading(event.currentTarget, "导入中");
  try {
    const payload = await api("/knowledge/backfill-legacy", { method: "POST" });
    const data = payload.data || {};
    await Promise.all([refreshDocuments(), refreshKnowledgeInfo()]);
    showToast(`已导入 ${data.documents_created || 0} 个文档、${data.chunks_backfilled || 0} 个切片`);
  } catch (error) {
    showToast(error.message);
  } finally {
    restore();
  }
}

async function login(event) {
  event.preventDefault();
  const restore = setLoading(event.submitter, "登录中");
  try {
    const payload = await api("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("#username").value.trim(),
        password: $("#password").value,
      }),
    });

    state.token = payload.data.access_token;
    state.user = {
      username: $("#username").value.trim(),
      tenant_id: payload.data.tenant_id,
      user_id: payload.data.user_id,
    };
    localStorage.setItem("smart_qa_token", state.token);
    localStorage.setItem("smart_qa_user", JSON.stringify(state.user));
    syncAuthView();
    await Promise.all([refreshHealth(), refreshKnowledgeInfo(), refreshDocuments()]);
    showToast("登录成功");
  } catch (error) {
    showToast(error.message);
  } finally {
    restore();
  }
}

function logout() {
  state.token = "";
  state.user = null;
  state.sessionId = "";
  localStorage.removeItem("smart_qa_token");
  localStorage.removeItem("smart_qa_user");
  syncAuthView();
}

async function uploadDocuments(event) {
  event.preventDefault();
  const files = Array.from($("#file-input").files || []);
  if (files.length === 0) {
    showToast("请选择文件");
    return;
  }

  const restore = setLoading(event.submitter, "上传中");
  const log = $("#upload-log");
  log.innerHTML = "";

  try {
    for (const file of files) {
      const formData = new FormData();
      formData.append("file", file);
      try {
        const payload = await api("/knowledge/upload", {
          method: "POST",
          body: formData,
        });
        appendLog(`${file.name}: ${payload.message || "任务已提交"}`, "success");
      } catch (error) {
        appendLog(`${file.name}: ${error.message}`, "error");
      }
    }
    await Promise.all([refreshKnowledgeInfo(), refreshDocuments()]);
    startDocumentPolling();
  } finally {
    restore();
  }
}

function appendLog(message, type) {
  const item = document.createElement("div");
  item.className = `log-item ${type}`;
  item.textContent = message;
  $("#upload-log").appendChild(item);
}

async function askQuestion(event) {
  event.preventDefault();
  const question = $("#question").value.trim();
  if (!question) return;

  addMessage("user", question);
  $("#question").value = "";
  const assistant = addMessage("assistant", "思考中...");
  renderSources([]);
  const restore = setLoading(event.submitter, "发送中");

  try {
    const payload = await api("/knowledge/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        stream: false,
        top_k: Number($("#top-k").value || 5),
      }),
    });

    const data = payload.data || {};
    assistant.textContent = data.answer || "无回答";
    renderSources(data.sources || []);
  } catch (error) {
    assistant.className = "message error";
    assistant.textContent = error.message;
  } finally {
    restore();
  }
}

function newSession() {
  state.sessionId = "";
  $("#messages").innerHTML = "";
  renderSources([]);
  setText("#session-label", "新会话");
}

function switchView(event) {
  const button = event.target.closest(".nav-item");
  if (!button) return;
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  button.classList.add("active");
  $(`#${button.dataset.view}`).classList.add("active");
  if (button.dataset.view === "knowledge-view" && state.token) {
    refreshDocuments().catch((error) => showToast(error.message));
  }
}

function bindEvents() {
  $("#login-form").addEventListener("submit", login);
  $("#logout-btn").addEventListener("click", logout);
  $("#health-btn").addEventListener("click", refreshHealth);
  $("#knowledge-info-btn").addEventListener("click", refreshKnowledgeInfo);
  $("#documents-refresh-btn").addEventListener("click", () => refreshDocuments().catch((error) => showToast(error.message)));
  $("#backfill-legacy-btn").addEventListener("click", backfillLegacyDocuments);
  $("#document-list").addEventListener("click", deleteDocument);
  $("#upload-form").addEventListener("submit", uploadDocuments);
  $("#question-form").addEventListener("submit", askQuestion);
  $("#new-session-btn").addEventListener("click", newSession);
  $(".nav").addEventListener("click", switchView);
}

bindEvents();
syncAuthView();
refreshHealth();
if (state.token) {
  refreshKnowledgeInfo();
  refreshDocuments().catch(() => {});
}
