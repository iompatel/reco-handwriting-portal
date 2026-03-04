const state = {
  user: null,
  historyRows: [],
  historyTotal: 0,
  historyFilterDebounceId: null,
  historyRequestSeq: 0,
  sourceOptionsLoaded: false,
};

const refs = {
  userName: document.getElementById("user-name"),
  userRole: document.getElementById("user-role"),
  historyHomeLink: document.getElementById("history-home-link"),
  profileToggle: document.getElementById("profile-toggle"),
  profileToggleAvatar: document.getElementById("profile-toggle-avatar"),
  profileMenu: document.getElementById("profile-menu"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileRole: document.getElementById("profile-role"),
  profileAvatarPreview: document.getElementById("profile-avatar-preview"),
  profileAvatarInput: document.getElementById("profile-avatar-input"),
  profileLogoutBtn: document.getElementById("profile-logout-btn"),
  historyAdminCols: Array.from(document.querySelectorAll(".admin-history-col")),
  historyBody: document.getElementById("history-body"),
  refreshHistory: document.getElementById("refresh-history"),
  adminHistoryToolbar: document.getElementById("admin-history-toolbar"),
  historySearchInput: document.getElementById("history-search-input"),
  historySourceFilter: document.getElementById("history-source-filter"),
  historyFilterClear: document.getElementById("history-filter-clear"),
  historyFilterCount: document.getElementById("history-filter-count"),
  historyViewModal: document.getElementById("history-view-modal"),
  historyViewClose: document.getElementById("history-view-close"),
  historyViewAdminOnly: Array.from(document.querySelectorAll(".history-view-admin-only")),
  historyViewFile: document.getElementById("history-view-file"),
  historyViewUserId: document.getElementById("history-view-user-id"),
  historyViewName: document.getElementById("history-view-name"),
  historyViewEmail: document.getElementById("history-view-email"),
  historyViewSource: document.getElementById("history-view-source"),
  historyViewConfidence: document.getElementById("history-view-confidence"),
  historyViewTime: document.getElementById("history-view-time"),
  historyViewUpdated: document.getElementById("history-view-updated"),
  historyViewPrediction: document.getElementById("history-view-prediction"),
  historyViewImage: document.getElementById("history-view-image"),
  historyViewImageNote: document.getElementById("history-view-image-note"),
  status: document.getElementById("history-status"),
};

async function apiFetch(url, options = {}) {
  const response = await fetch(url, options);
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }

  if (!response.ok || data.ok === false) {
    const message = data.error || `Request failed (${response.status})`;
    throw new Error(message);
  }
  return data;
}

function setStatus(message, isError = false) {
  if (!refs.status) return;
  refs.status.textContent = message;
  refs.status.style.color = isError ? "#b42318" : "#1f4364";
}

function isHistoryViewOpen() {
  return Boolean(refs.historyViewModal) && !refs.historyViewModal.classList.contains("hidden");
}

function setHistoryViewOpen(isOpen) {
  if (!refs.historyViewModal) return;
  refs.historyViewModal.classList.toggle("hidden", !isOpen);
  document.body.classList.toggle("modal-open", isOpen);
}

function closeHistoryView() {
  setHistoryViewOpen(false);
}

function formatDate(value) {
  if (!value) return "-";
  const dt = new Date(value.replace(" ", "T") + "Z");
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function initialsFromName(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (parts.length === 0) return "U";
  return parts.map((part) => part[0].toUpperCase()).join("");
}

function avatarPlaceholder(name) {
  const initials = initialsFromName(name);
  const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='144' height='144'>
    <defs>
      <linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>
        <stop offset='0%' stop-color='#3f7ec4'/>
        <stop offset='100%' stop-color='#1f4f84'/>
      </linearGradient>
    </defs>
    <rect width='144' height='144' rx='72' fill='url(#g)'/>
    <text x='72' y='86' font-size='52' text-anchor='middle' fill='white' font-family='Arial, sans-serif'>${initials}</text>
  </svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function updateAvatarPreview(avatarUrl, displayName) {
  const src = avatarUrl || avatarPlaceholder(displayName || "User");
  if (refs.profileAvatarPreview) refs.profileAvatarPreview.src = src;
  if (refs.profileToggleAvatar) refs.profileToggleAvatar.src = src;
}

function setProfileMenuOpen(isOpen) {
  if (!refs.profileMenu || !refs.profileToggle) return;
  refs.profileMenu.classList.toggle("hidden", !isOpen);
  refs.profileToggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
}

function closeProfileMenu() {
  setProfileMenuOpen(false);
}

function toggleProfileMenu() {
  if (!refs.profileMenu) return;
  const shouldOpen = refs.profileMenu.classList.contains("hidden");
  setProfileMenuOpen(shouldOpen);
}

async function logoutAndRedirect() {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}

async function uploadProfileAvatar(file) {
  if (!file) return;

  const form = new FormData();
  form.append("avatar", file);
  setStatus("Uploading profile image...");

  try {
    const response = await fetch("/api/profile/avatar", {
      method: "POST",
      body: form,
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Failed to upload profile image.");
    }

    if (state.user) {
      state.user.avatar_url = data.avatar_url || "";
    }
    updateAvatarPreview(data.avatar_url || "", refs.userName?.textContent || "User");
    setStatus("Profile image updated.");
  } catch (error) {
    setStatus(error.message || "Profile image upload failed.", true);
  } finally {
    if (refs.profileAvatarInput) {
      refs.profileAvatarInput.value = "";
    }
  }
}

function extractFilename(contentDisposition, fallback) {
  const header = contentDisposition || "";
  const filenameMatch = header.match(/filename=\"?([^\";]+)\"?/i);
  return filenameMatch?.[1] || fallback;
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function downloadHistoryPdf(row) {
  if (!row?.id) {
    setStatus("Invalid history record.", true);
    return;
  }

  setStatus(`Preparing PDF for ${row.file_name || "record"}...`);
  try {
    const response = await fetch(`/api/history/${row.id}/export/pdf`);
    if (!response.ok) {
      let errorMessage = `PDF export failed (${response.status})`;
      try {
        const data = await response.json();
        if (data?.error) errorMessage = data.error;
      } catch {
        // ignore json parse errors
      }
      throw new Error(errorMessage);
    }

    const blob = await response.blob();
    const filename = extractFilename(response.headers.get("content-disposition"), `history_record_${row.id}.pdf`);
    triggerDownload(blob, filename);
    setStatus("History PDF downloaded.");
  } catch (error) {
    setStatus(error.message || "PDF export failed.", true);
  }
}

function renderHistoryView(record) {
  if (!record) return;
  const isAdmin = state.user?.role === "admin";

  refs.historyViewAdminOnly.forEach((item) => {
    item.classList.toggle("hidden", !isAdmin);
  });

  if (refs.historyViewFile) refs.historyViewFile.textContent = record.file_name || "-";
  if (refs.historyViewUserId) refs.historyViewUserId.textContent = String(record.user_id ?? "-");
  if (refs.historyViewName) refs.historyViewName.textContent = record.full_name || record.username || "-";
  if (refs.historyViewEmail) refs.historyViewEmail.textContent = record.username || "-";
  if (refs.historyViewSource) refs.historyViewSource.textContent = String(record.source || "-").toUpperCase();
  if (refs.historyViewConfidence) {
    refs.historyViewConfidence.textContent = `${Math.round((record.confidence || 0) * 100)}%`;
  }
  if (refs.historyViewTime) refs.historyViewTime.textContent = formatDate(record.created_at);
  if (refs.historyViewUpdated) refs.historyViewUpdated.textContent = formatDate(record.updated_at);
  if (refs.historyViewPrediction) refs.historyViewPrediction.value = record.prediction || "";

  if (refs.historyViewImage) {
    if (record.image_url) {
      refs.historyViewImage.src = `${record.image_url}?v=${Date.now()}`;
      refs.historyViewImage.classList.remove("hidden");
      refs.historyViewImageNote?.classList.add("hidden");
    } else {
      refs.historyViewImage.removeAttribute("src");
      refs.historyViewImage.classList.add("hidden");
      refs.historyViewImageNote?.classList.remove("hidden");
    }
  }
}

async function viewHistoryRecord(row) {
  if (!row?.id) {
    setStatus("Invalid history record.", true);
    return;
  }
  setStatus("Loading record details...");
  try {
    const data = await apiFetch(`/api/history/${row.id}`);
    renderHistoryView(data.record || row);
    setHistoryViewOpen(true);
    setStatus("Record loaded.");
  } catch (error) {
    setStatus(error.message || "Failed to load record details.", true);
  }
}

function normalizeFilterText(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function setHighlightedText(cell, value, query) {
  if (!cell) return;
  const text = String(value ?? "");
  const needle = String(query || "").trim();
  cell.textContent = "";

  if (!needle) {
    cell.textContent = text;
    return;
  }

  const pattern = escapeRegExp(needle);
  if (!pattern) {
    cell.textContent = text;
    return;
  }

  const regex = new RegExp(pattern, "ig");
  let lastIndex = 0;
  let hasMatch = false;
  let match;

  while ((match = regex.exec(text)) !== null) {
    const start = match.index;
    const matchedText = match[0] || "";
    if (start > lastIndex) {
      cell.appendChild(document.createTextNode(text.slice(lastIndex, start)));
    }

    const mark = document.createElement("mark");
    mark.className = "history-search-highlight";
    mark.textContent = matchedText;
    cell.appendChild(mark);

    hasMatch = true;
    lastIndex = start + matchedText.length;

    if (matchedText.length === 0) {
      regex.lastIndex += 1;
    }
  }

  if (!hasMatch) {
    cell.textContent = text;
    return;
  }

  if (lastIndex < text.length) {
    cell.appendChild(document.createTextNode(text.slice(lastIndex)));
  }
}

function updateSourceFilterOptions(rows) {
  if (!refs.historySourceFilter) return;
  const selected = String(refs.historySourceFilter.value || "all");
  const values = Array.from(
    new Set(
      (rows || [])
        .map((row) => String(row.source || "").trim().toLowerCase())
        .filter(Boolean)
    )
  ).sort();

  refs.historySourceFilter.innerHTML = "";
  const allOption = document.createElement("option");
  allOption.value = "all";
  allOption.textContent = "All Sources";
  refs.historySourceFilter.appendChild(allOption);

  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value.toUpperCase();
    refs.historySourceFilter.appendChild(option);
  });

  if (selected === "all" || values.includes(selected)) {
    refs.historySourceFilter.value = selected;
  } else {
    refs.historySourceFilter.value = "all";
  }
}

function updateHistoryFilterCount(filtered, total) {
  if (!refs.historyFilterCount) return;
  const safeFiltered = Number(filtered || 0);
  const safeTotal = Number(total || 0);
  refs.historyFilterCount.textContent =
    safeFiltered === safeTotal
      ? `${safeFiltered} record${safeFiltered === 1 ? "" : "s"}`
      : `${safeFiltered} of ${safeTotal} records`;
}

function buildHistoryEndpoint() {
  const isAdmin = state.user?.role === "admin";
  const params = new URLSearchParams();

  if (isAdmin) {
    params.set("scope", "all");
    const query = String(refs.historySearchInput?.value || "").trim();
    const source = normalizeFilterText(refs.historySourceFilter?.value || "all");
    if (query) params.set("search", query);
    if (source && source !== "all") params.set("source", source);
  }

  const queryString = params.toString();
  return queryString ? `/api/history?${queryString}` : "/api/history";
}

function renderHistoryRows(rows) {
  if (!refs.historyBody) return;
  const isAdmin = state.user?.role === "admin";
  const activeQuery = isAdmin ? String(refs.historySearchInput?.value || "").trim() : "";
  refs.historyBody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = isAdmin ? 8 : 4;
    td.textContent = "No history yet.";
    tr.appendChild(td);
    refs.historyBody.appendChild(tr);
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    if (isAdmin && activeQuery) {
      tr.classList.add("history-search-row-highlight");
    }

    const file = document.createElement("td");
    setHighlightedText(file, row.file_name || "-", activeQuery);

    const source = document.createElement("td");
    setHighlightedText(source, String(row.source || "-").toUpperCase(), activeQuery);

    const conf = document.createElement("td");
    conf.textContent = `${Math.round((row.confidence || 0) * 100)}%`;

    const time = document.createElement("td");
    setHighlightedText(time, formatDate(row.created_at), activeQuery);

    const actions = document.createElement("td");
    actions.className = "table-actions";

    const viewBtn = document.createElement("button");
    viewBtn.className = "btn ghost";
    viewBtn.textContent = "View";
    viewBtn.addEventListener("click", async () => {
      await viewHistoryRecord(row);
    });
    actions.append(viewBtn);

    const pdfBtn = document.createElement("button");
    pdfBtn.className = "btn ghost";
    pdfBtn.textContent = "Download PDF";
    pdfBtn.addEventListener("click", async () => {
      await downloadHistoryPdf(row);
    });
    actions.append(pdfBtn);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn ghost";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async () => {
      if (!confirm("Delete this history record?")) return;
      try {
        await apiFetch(`/api/history/${row.id}`, { method: "DELETE" });
        await loadHistory();
      } catch (error) {
        setStatus(error.message, true);
      }
    });
    actions.append(deleteBtn);

    if (isAdmin) {
      const userId = document.createElement("td");
      setHighlightedText(userId, String(row.user_id ?? "-"), activeQuery);
      const name = document.createElement("td");
      setHighlightedText(name, row.full_name || row.username || "-", activeQuery);
      const email = document.createElement("td");
      setHighlightedText(email, row.username || "-", activeQuery);
      tr.append(userId, name, email, file, source, conf, time, actions);
    } else {
      tr.append(file, conf, time, actions);
    }
    refs.historyBody.appendChild(tr);
  });
}

function updateHistoryColumnVisibility() {
  const isAdmin = state.user?.role === "admin";
  refs.historyAdminCols.forEach((column) => {
    column.classList.toggle("hidden", !isAdmin);
  });
  if (refs.adminHistoryToolbar) {
    refs.adminHistoryToolbar.classList.toggle("hidden", !isAdmin);
  }
}

function queueHistoryReload(delayMs = 280) {
  if (state.historyFilterDebounceId) {
    window.clearTimeout(state.historyFilterDebounceId);
  }
  state.historyFilterDebounceId = window.setTimeout(() => {
    state.historyFilterDebounceId = null;
    loadHistory({ silentStatus: true });
  }, delayMs);
}

async function loadHistory(options = {}) {
  const { silentStatus = false, refreshSources = false } = options;
  const isAdmin = state.user?.role === "admin";
  const requestSeq = ++state.historyRequestSeq;
  if (!silentStatus) setStatus("Loading history...");
  try {
    const endpoint = buildHistoryEndpoint();
    const data = await apiFetch(endpoint);
    if (requestSeq !== state.historyRequestSeq) {
      return;
    }
    state.historyRows = Array.isArray(data.history) ? data.history : [];
    state.historyTotal = Number(data.total || state.historyRows.length);

    renderHistoryRows(state.historyRows);

    if (isAdmin) {
      const query = String(refs.historySearchInput?.value || "").trim();
      const source = normalizeFilterText(refs.historySourceFilter?.value || "all");
      if (refreshSources || !state.sourceOptionsLoaded || (!query && source === "all")) {
        updateSourceFilterOptions(state.historyRows);
        state.sourceOptionsLoaded = true;
      }
      updateHistoryFilterCount(state.historyRows.length, state.historyTotal);
    }

    setStatus("History loaded.");
  } catch (error) {
    setStatus(error.message || "Failed to load history.", true);
  }
}

async function bootstrapUser() {
  try {
    const data = await apiFetch("/api/me");
    state.user = data.user;
    const displayName = state.user.full_name || state.user.username;
    const displayEmail = state.user.username || "-";
    const displayRole = (state.user.role || "user").toUpperCase();

    if (refs.userName) refs.userName.textContent = displayName;
    if (refs.userRole) refs.userRole.textContent = displayRole;
    if (refs.historyHomeLink) {
      refs.historyHomeLink.textContent = state.user.role === "admin" ? "Admin Panel" : "Recognize Text";
    }
    updateHistoryColumnVisibility();
    if (refs.profileName) refs.profileName.textContent = displayName;
    if (refs.profileEmail) refs.profileEmail.textContent = displayEmail;
    if (refs.profileRole) refs.profileRole.textContent = displayRole;
    updateAvatarPreview(state.user.avatar_url || "", displayName);
    closeProfileMenu();
  } catch {
    window.location.href = "/login";
  }
}

function attachEvents() {
  refs.profileToggle?.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleProfileMenu();
  });

  refs.profileMenu?.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  refs.profileLogoutBtn?.addEventListener("click", logoutAndRedirect);

  refs.profileAvatarInput?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    await uploadProfileAvatar(file);
  });

  document.addEventListener("click", () => {
    closeProfileMenu();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (isHistoryViewOpen()) {
        closeHistoryView();
      }
      closeProfileMenu();
    }
  });

  refs.historyViewClose?.addEventListener("click", closeHistoryView);
  refs.historyViewModal?.addEventListener("click", (event) => {
    if (event.target === refs.historyViewModal) {
      closeHistoryView();
    }
  });

  refs.refreshHistory?.addEventListener("click", () => {
    loadHistory({ refreshSources: true });
  });
  refs.historySearchInput?.addEventListener("input", () => {
    queueHistoryReload();
  });
  refs.historySourceFilter?.addEventListener("change", () => {
    loadHistory({ silentStatus: true });
  });
  refs.historyFilterClear?.addEventListener("click", () => {
    if (refs.historySearchInput) refs.historySearchInput.value = "";
    if (refs.historySourceFilter) refs.historySourceFilter.value = "all";
    loadHistory({ refreshSources: true, silentStatus: true });
  });
}

async function init() {
  attachEvents();
  await bootstrapUser();
  await loadHistory();
}

init();
