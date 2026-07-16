const state = {
  limit: 50,
  offset: 0,
  total: 0,
  requestCount: 0,
  bulkJobId: null,
  bulkPollTimer: null,
};

const fieldLabels = {
  name: "기업명",
  industry: "업종",
  item: "세부품목",
  representative: "대표자",
  founded_year: "설립연도",
  employee_count: "직원 수",
  phone: "전화번호",
  address: "주소",
  email: "이메일",
  homepage: "홈페이지",
  business_area: "사업영역",
  summary: "기업 소개",
  description: "상세 설명",
  product_service: "제품·서비스",
  interest_category: "관심분야",
};

const statusLabels = { new: "신규", changed: "변경", unchanged: "기존" };

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function valueHtml(value, className = "") {
  if (value === null || value === undefined || value === "") {
    return `<span class="muted-value">—</span>`;
  }
  return `<span class="${className}">${escapeHtml(value)}</span>`;
}

function formatNumber(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString("ko-KR") : "0";
}

function formatDate(value, includeTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const options = includeTime
    ? { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }
    : { year: "numeric", month: "2-digit", day: "2-digit" };
  return new Intl.DateTimeFormat("ko-KR", options).format(date);
}

function setNetworkBusy(busy) {
  state.requestCount += busy ? 1 : -1;
  state.requestCount = Math.max(0, state.requestCount);
  $("#loadingBar").classList.toggle("active", state.requestCount > 0);
}

async function fetchJSON(url, options = {}) {
  setNetworkBusy(true);
  try {
    const response = await fetch(url, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let payload = null;
    try { payload = await response.json(); } catch (_) { /* 응답 본문 없음 */ }
    if (!response.ok) {
      const detail = payload?.detail;
      throw new Error(typeof detail === "string" ? detail : `요청에 실패했습니다. (${response.status})`);
    }
    return payload;
  } finally {
    setNetworkBusy(false);
  }
}

function toast(title, message = "", type = "success", duration = 5200) {
  const region = $("#toastRegion");
  const element = document.createElement("div");
  element.className = `toast ${type}`;
  element.innerHTML = `<strong>${escapeHtml(title)}</strong>${message ? escapeHtml(message) : ""}`;
  region.appendChild(element);
  setTimeout(() => element.remove(), duration);
}

function getFilters() {
  return {
    q: $("#filterQ").value.trim(),
    industry: $("#filterIndustry").value,
    representative: $("#filterRepresentative").value.trim(),
    address: $("#filterAddress").value.trim(),
    founded_from: $("#filterFoundedFrom").value,
    founded_to: $("#filterFoundedTo").value,
    employees_min: $("#filterEmployeesMin").value,
    employees_max: $("#filterEmployeesMax").value,
    status: $("#filterStatus").value,
    changed_only: $("#filterChangedOnly").checked ? "true" : "",
    sort: $("#filterSort").value,
  };
}

function queryString(values) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== "" && value !== null && value !== undefined && value !== false) {
      params.set(key, value);
    }
  });
  return params.toString();
}

async function checkHealth() {
  const pill = $("#connectionPill");
  try {
    await fetchJSON("/api/health");
    pill.classList.add("online");
    pill.querySelector("span").textContent = "로컬 서버 연결됨";
  } catch (error) {
    pill.classList.remove("online");
    pill.querySelector("span").textContent = "서버 연결 오류";
  }
}

async function loadStats() {
  const data = await fetchJSON("/api/stats");
  $("#statTotal").textContent = formatNumber(data.total);
  $("#statChanged").textContent = formatNumber(data.with_change_history);
  $("#statNew").textContent = formatNumber(data.current_new);

  if (data.last_run) {
    $("#statLastSync").textContent = formatDate(data.last_run.completed_at || data.last_run.started_at, false);
    const status = data.last_run.status === "completed" ? "완료" : data.last_run.status === "failed" ? "실패" : "진행 중";
    $("#statLastSyncDetail").textContent = `${status} · ${formatNumber(data.last_run.synchronized_count)}개 동기화`;
  } else {
    $("#statLastSync").textContent = "—";
    $("#statLastSyncDetail").textContent = "아직 동기화하지 않았습니다";
  }

  const industrySelect = $("#filterIndustry");
  const selected = industrySelect.value;
  industrySelect.innerHTML = '<option value="">전체 업종</option>' + data.industries
    .map((item) => `<option value="${escapeHtml(item.industry)}">${escapeHtml(item.industry)} (${formatNumber(item.count)})</option>`)
    .join("");
  industrySelect.value = selected;
}

function changeCell(item, field, content, extraClass = "") {
  const change = item.last_sync_status === "changed" ? item.last_changes?.[field] : null;
  const classes = [extraClass, change ? "changed-cell" : ""].filter(Boolean).join(" ");
  const title = change
    ? `${fieldLabels[field] || field} 변경\n이전: ${change.old || "(빈 값)"}\n현재: ${change.new || "(빈 값)"}`
    : "";
  return `<td class="${classes}"${title ? ` title="${escapeHtml(title)}"` : ""}>${content}</td>`;
}

function renderRows(items) {
  const body = $("#companyTableBody");
  const empty = $("#emptyState");
  if (!items.length) {
    body.innerHTML = "";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;
  body.innerHTML = items.map((item) => {
    const status = item.last_sync_status || "unchanged";
    const detailText = item.item || item.business_area || "";
    return `<tr data-id="${escapeHtml(item.source_id)}">
      <td><span class="status-badge status-${escapeHtml(status)}">${statusLabels[status] || status}</span></td>
      ${changeCell(item, "name", `<span class="company-name">${escapeHtml(item.name)}${detailText ? `<small title="${escapeHtml(detailText)}">${escapeHtml(detailText)}</small>` : ""}</span>`)}
      ${changeCell(item, "industry", valueHtml(item.industry))}
      ${changeCell(item, "representative", valueHtml(item.representative))}
      ${changeCell(item, "founded_year", valueHtml(item.founded_year))}
      ${changeCell(item, "employee_count", item.employee_count === null || item.employee_count === undefined ? valueHtml(null) : `${formatNumber(item.employee_count)}명`)}
      ${changeCell(item, "phone", valueHtml(item.phone))}
      ${changeCell(item, "address", valueHtml(item.address, "ellipsis"), "ellipsis")}
      ${changeCell(item, "email", valueHtml(item.email, "ellipsis"), "ellipsis")}
      <td><button class="detail-button" type="button" data-detail-id="${escapeHtml(item.source_id)}" aria-label="${escapeHtml(item.name)} 상세정보">›</button></td>
    </tr>`;
  }).join("");
}

function updatePagination() {
  const pagination = $("#pagination");
  const pageCount = Math.max(1, Math.ceil(state.total / state.limit));
  const currentPage = Math.floor(state.offset / state.limit) + 1;
  pagination.hidden = state.total <= state.limit;
  $("#pageInfo").textContent = `${currentPage} / ${pageCount}`;
  $("#prevPageButton").disabled = state.offset <= 0;
  $("#nextPageButton").disabled = state.offset + state.limit >= state.total;
}

async function loadCompanies() {
  const params = { ...getFilters(), limit: state.limit, offset: state.offset };
  try {
    const data = await fetchJSON(`/api/companies?${queryString(params)}`);
    state.total = data.total;
    $("#resultCount").textContent = formatNumber(data.total);
    renderRows(data.items);
    updatePagination();
  } catch (error) {
    toast("기업 목록을 불러오지 못했습니다", error.message, "error");
  }
}

async function synchronizeSource(event) {
  event.preventDefault();
  const query = $("#sourceQuery").value.trim();
  if (!query) {
    toast("기업명을 입력해 주세요", "원본 사이트 전체 조회는 서버 부하를 막기 위해 기본 차단되어 있습니다.", "warning");
    $("#sourceQuery").focus();
    return;
  }

  const button = $("#sourceSearchButton");
  button.disabled = true;
  button.classList.add("is-loading");
  try {
    const result = await fetchJSON("/api/source/search", {
      method: "POST",
      body: JSON.stringify({ query, include_details: true }),
    });
    $("#filterQ").value = query;
    state.offset = 0;
    const summary = `신규 ${formatNumber(result.new_count)}개 · 변경 ${formatNumber(result.changed_count)}개 · 기존 ${formatNumber(result.unchanged_count)}개`;
    toast(`${formatNumber(result.synchronized_count)}개 기업 동기화 완료`, summary, "success", 6500);
    result.warnings?.forEach((warning) => toast("일부 결과 안내", warning, "warning", 9000));
    await Promise.all([loadStats(), loadCompanies()]);
    $("#resultTitle").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    toast("원본 검색에 실패했습니다", error.message, "error", 9000);
  } finally {
    button.disabled = false;
    button.classList.remove("is-loading");
  }
}

function detailField(label, value, className = "") {
  return `<div class="detail-field ${className}"><span>${escapeHtml(label)}</span><p>${valueHtml(value)}</p></div>`;
}

function statusBadge(status) {
  return `<span class="status-badge status-${escapeHtml(status || "unchanged")}">${statusLabels[status] || status || "기존"}</span>`;
}

function renderHistory(history) {
  if (!history.length) return '<div class="history-empty">아직 저장된 변경 이력이 없습니다.</div>';
  return `<div class="history-list">${history.map((item) => {
    const created = item.event_type === "created";
    const field = created ? "기업 최초 저장" : (fieldLabels[item.field_name] || item.field_name || "정보 변경");
    const values = created
      ? `검색어 “${escapeHtml(item.query || "") || "—"}”로 처음 저장했습니다.`
      : `<del>${escapeHtml(item.old_value || "(빈 값)")}</del> &nbsp;→&nbsp; <ins>${escapeHtml(item.new_value || "(빈 값)")}</ins>`;
    return `<div class="history-item">
      <span class="history-time">${escapeHtml(formatDate(item.changed_at))}</span>
      <span class="history-field">${escapeHtml(field)}</span>
      <span class="history-values">${values}</span>
    </div>`;
  }).join("")}</div>`;
}

async function openDetail(sourceId) {
  const dialog = $("#detailDialog");
  const content = $("#detailContent");
  content.innerHTML = '<div class="detail-loading">기업 정보와 변경 이력을 불러오는 중입니다…</div>';
  if (typeof dialog.showModal === "function") dialog.showModal();
  try {
    const data = await fetchJSON(`/api/companies/${encodeURIComponent(sourceId)}`);
    const item = data.company;
    content.innerHTML = `
      <div class="detail-head">
        ${statusBadge(item.last_sync_status)}
        <h2>${escapeHtml(item.name)}</h2>
        <p>${escapeHtml(item.industry || "업종 미등록")}${item.item ? ` · ${escapeHtml(item.item)}` : ""}</p>
        <div class="detail-links">
          ${item.homepage ? `<a href="${escapeHtml(normalizeUrl(item.homepage))}" target="_blank" rel="noreferrer">기업 홈페이지 ↗</a>` : ""}
          ${item.source_url ? `<a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">하이서울 원본 ↗</a>` : ""}
        </div>
      </div>
      <div class="detail-grid">
        ${detailField("대표자", item.representative)}
        ${detailField("설립연도", item.founded_year)}
        ${detailField("직원 수", item.employee_count === null || item.employee_count === undefined ? null : `${formatNumber(item.employee_count)}명`)}
        ${detailField("전화번호", item.phone)}
        ${detailField("이메일", item.email, "wide")}
        ${detailField("주소", item.address, "full")}
        ${detailField("사업영역", item.business_area, "wide")}
        ${detailField("관심분야", item.interest_category)}
        ${detailField("기업 소개", item.summary, "full")}
        ${item.description ? detailField("상세 설명", item.description, "full") : ""}
        ${item.product_service ? detailField("제품·서비스", item.product_service, "full") : ""}
      </div>
      <section class="history-section">
        <h3>변경 이력</h3>
        ${renderHistory(data.history)}
      </section>`;
  } catch (error) {
    content.innerHTML = `<div class="detail-loading">${escapeHtml(error.message)}</div>`;
  }
}

function normalizeUrl(url) {
  const value = String(url || "").trim();
  if (!value) return "#";
  return /^https?:\/\//i.test(value) ? value : `https://${value}`;
}

function resetFilters() {
  $("#filterForm").reset();
  state.offset = 0;
  loadCompanies();
}

function exportExcel(scope) {
  const params = { ...getFilters(), scope };
  window.location.href = `/api/export?${queryString(params)}`;
  toast("엑셀 파일을 생성하고 있습니다", scope === "changed" ? "변경 이력 시트도 함께 포함됩니다." : "현재 필터 조건이 적용됩니다.", "success", 3500);
}

function bulkInputCount() {
  const names = $("#bulkNames").value
    .split(/\r?\n/)
    .map((value) => value.trim())
    .filter(Boolean);
  return new Set(names).size;
}

function updateBulkInputCount() {
  $("#bulkInputCount").textContent = `${formatNumber(bulkInputCount())}개`;
}

function setBulkControlsDisabled(disabled) {
  $("#bulkStartButton").disabled = disabled;
  $("#bulkStartButton").classList.toggle("is-loading", disabled);
  $("#bulkNames").disabled = disabled || $("#bulkSyncAll").checked;
  $("#bulkSyncAll").disabled = disabled;
  $("#loadDefaultNamesButton").disabled = disabled;
}

async function loadDefaultNames(silent = false) {
  try {
    const data = await fetchJSON("/api/bulk/default-names");
    $("#bulkNames").value = data.text || "";
    $("#bulkSyncAll").checked = false;
    $("#bulkNames").disabled = false;
    updateBulkInputCount();
    if (!silent) {
      toast("제공된 기업 목록을 불러왔습니다", `${formatNumber(data.count)}개 기업명이 입력되었습니다.`, "success");
    }
  } catch (error) {
    toast("기본 목록을 불러오지 못했습니다", error.message, "error");
  }
}

function renderBulkJob(job) {
  const panel = $("#bulkProgress");
  const total = Number(job.progress_total || 0);
  const current = Number(job.progress_current || 0);
  const percent = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0;
  const statusLabels = {
    queued: "작업 대기 중",
    running: "상세정보 수집 중",
    saving: "SQLite 저장 중",
    completed: "대량추가 완료",
    failed: "대량추가 실패",
  };

  panel.hidden = false;
  panel.classList.toggle("is-running", ["queued", "running", "saving"].includes(job.status));
  panel.classList.toggle("is-completed", job.status === "completed");
  panel.classList.toggle("is-failed", job.status === "failed");
  $("#bulkStatusLabel").textContent = statusLabels[job.status] || "대량추가";
  $("#bulkProgressMessage").textContent = job.message || "처리 중입니다.";
  $("#bulkProgressPercent").textContent = total > 0 ? `${percent}%` : "…";
  $("#bulkProgressBar").style.width = total > 0 ? `${percent}%` : "42%";
  $("#bulkProgressBar").classList.toggle("indeterminate", total === 0 && job.status !== "failed");
  $("#bulkRequestedCount").textContent = formatNumber(job.requested_count);
  $("#bulkMatchedCount").textContent = formatNumber(job.matched_count || (job.status === "running" ? total : 0));
  $("#bulkUnmatchedCount").textContent = formatNumber(job.unmatched_count);
  $("#bulkNewCount").textContent = formatNumber(job.new_count);

  const finished = ["completed", "failed"].includes(job.status);
  setBulkControlsDisabled(!finished);
  $("#bulkResultActions").hidden = job.status !== "completed";
  $("#downloadUnmatchedButton").hidden = !job.unmatched_count;
}

async function pollBulkJob(jobId, announceCompletion = true) {
  if (state.bulkPollTimer) clearTimeout(state.bulkPollTimer);
  try {
    const job = await fetchJSON(`/api/bulk/${encodeURIComponent(jobId)}`);
    state.bulkJobId = jobId;
    renderBulkJob(job);
    if (["queued", "running", "saving"].includes(job.status)) {
      state.bulkPollTimer = setTimeout(() => pollBulkJob(jobId, announceCompletion), 1400);
      return;
    }
    localStorage.removeItem("hiseoulBulkJobId");
    if (job.status === "completed") {
      $("#filterForm").reset();
      state.offset = 0;
      await Promise.all([loadStats(), loadCompanies()]);
      if (announceCompletion) {
        toast(
          "대량추가가 완료되었습니다",
          `매칭 ${formatNumber(job.matched_count)}개 · 신규 ${formatNumber(job.new_count)}개 · 기존 ${formatNumber(job.unchanged_count)}개`,
          "success",
          9000,
        );
        (job.warnings || []).slice(0, 3).forEach((warning) => toast("수집 안내", warning, "warning", 9000));
      }
    } else {
      toast("대량추가에 실패했습니다", job.error || job.message, "error", 10000);
    }
  } catch (error) {
    localStorage.removeItem("hiseoulBulkJobId");
    setBulkControlsDisabled(false);
    toast("대량추가 상태를 확인하지 못했습니다", error.message, "error");
  }
}

async function startBulkImport() {
  const text = $("#bulkNames").value;
  const syncAll = $("#bulkSyncAll").checked;
  if (!syncAll && bulkInputCount() === 0) {
    toast("기업명 목록이 비어 있습니다", "엑셀의 기업명 열을 붙여넣거나 제공 목록을 불러오세요.", "warning");
    $("#bulkNames").focus();
    return;
  }
  setBulkControlsDisabled(true);
  renderBulkJob({
    status: "queued",
    message: "대량추가 작업을 서버에 등록하고 있습니다.",
    requested_count: syncAll ? 0 : bulkInputCount(),
    progress_current: 0,
    progress_total: 0,
  });
  try {
    const job = await fetchJSON("/api/bulk/start", {
      method: "POST",
      body: JSON.stringify({ company_names_text: text, sync_all: syncAll }),
    });
    state.bulkJobId = job.job_id;
    localStorage.setItem("hiseoulBulkJobId", job.job_id);
    renderBulkJob(job);
    toast("대량추가를 시작했습니다", "창을 닫지 않아도 서버에서 계속 처리되며 진행률이 자동 갱신됩니다.", "success", 6500);
    pollBulkJob(job.job_id);
  } catch (error) {
    setBulkControlsDisabled(false);
    renderBulkJob({ status: "failed", message: error.message });
    toast("대량추가를 시작하지 못했습니다", error.message, "error");
  }
}

async function openDatabaseFolder() {
  try {
    const result = await fetchJSON("/api/database/open-folder", { method: "POST", body: "{}" });
    toast("DB 폴더를 열었습니다", result.database || result.path, "success");
  } catch (error) {
    toast("DB 폴더를 열지 못했습니다", error.message, "error");
  }
}

function bindEvents() {
  $("#sourceSearchForm").addEventListener("submit", synchronizeSource);
  $("#bulkNames").addEventListener("input", updateBulkInputCount);
  $("#loadDefaultNamesButton").addEventListener("click", () => loadDefaultNames(false));
  $("#bulkStartButton").addEventListener("click", startBulkImport);
  $("#bulkSyncAll").addEventListener("change", (event) => {
    $("#bulkNames").disabled = event.target.checked;
  });
  $("#bulkExcelButton").addEventListener("click", () => exportExcel("all"));
  $("#downloadUnmatchedButton").addEventListener("click", () => {
    if (state.bulkJobId) window.location.href = `/api/bulk/${encodeURIComponent(state.bulkJobId)}/unmatched`;
  });
  $("#databaseDownloadButton").addEventListener("click", () => {
    window.location.href = "/api/database/download";
    toast("SQLite DB를 준비하고 있습니다", "현재 데이터가 반영된 DB 파일을 다운로드합니다.", "success", 3500);
  });
  $("#databaseFolderButton").addEventListener("click", openDatabaseFolder);
  $("#filterForm").addEventListener("submit", (event) => {
    event.preventDefault();
    state.offset = 0;
    loadCompanies();
  });
  $("#filterResetButton").addEventListener("click", resetFilters);
  $("#exportAllButton").addEventListener("click", () => exportExcel("all"));
  $("#exportChangedButton").addEventListener("click", () => exportExcel("changed"));
  $("#prevPageButton").addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - state.limit);
    loadCompanies();
  });
  $("#nextPageButton").addEventListener("click", () => {
    if (state.offset + state.limit < state.total) state.offset += state.limit;
    loadCompanies();
  });
  $("#companyTableBody").addEventListener("click", (event) => {
    const button = event.target.closest("[data-detail-id]");
    if (button) openDetail(button.dataset.detailId);
  });
  $("#dialogCloseButton").addEventListener("click", () => $("#detailDialog").close());
  $("#detailDialog").addEventListener("click", (event) => {
    if (event.target === event.currentTarget) event.currentTarget.close();
  });
}

async function initialize() {
  bindEvents();
  updateBulkInputCount();
  await Promise.allSettled([checkHealth(), loadStats(), loadCompanies(), loadDefaultNames(true)]);
  const savedBulkJob = localStorage.getItem("hiseoulBulkJobId");
  if (savedBulkJob) pollBulkJob(savedBulkJob, false);
}

document.addEventListener("DOMContentLoaded", initialize);
