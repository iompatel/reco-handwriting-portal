const state = {
  user: null,
  reportRows: [],
};

const refs = {
  userName: document.getElementById("user-name"),
  userRole: document.getElementById("user-role"),
  profileToggle: document.getElementById("profile-toggle"),
  profileToggleAvatar: document.getElementById("profile-toggle-avatar"),
  profileMenu: document.getElementById("profile-menu"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileRole: document.getElementById("profile-role"),
  profileAvatarPreview: document.getElementById("profile-avatar-preview"),
  profileAvatarInput: document.getElementById("profile-avatar-input"),
  profileLogoutBtn: document.getElementById("profile-logout-btn"),
  refreshActivity: document.getElementById("refresh-activity"),
  activitySearchInput: document.getElementById("activity-search-input"),
  activityActionFilter: document.getElementById("activity-action-filter"),
  activityFilterClear: document.getElementById("activity-filter-clear"),
  activityCount: document.getElementById("activity-count"),
  activityStatus: document.getElementById("activity-status"),
  activityBody: document.getElementById("activity-body"),
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
  if (!refs.activityStatus) return;
  refs.activityStatus.textContent = message;
  refs.activityStatus.style.color = isError ? "#b42318" : "#1f4364";
}

function formatDate(value) {
  if (!value) return "-";
  const dt = new Date(value.replace(" ", "T") + "Z");
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
}

function normalizeText(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}

function initialsFromName(name) {
  const parts = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);
  if (!parts.length) return "U";
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
    if (refs.profileAvatarInput) refs.profileAvatarInput.value = "";
  }
}

function prettifyAction(action) {
  const normalized = String(action || "")
    .trim()
    .replace(/_/g, " ")
    .toLowerCase();
  if (!normalized) return "-";
  return normalized.replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function buildTargetLabel(row) {
  const targetType = String(row.target_type || "").trim();
  const targetId = String(row.target_id || "").trim();
  if (!targetType && !targetId) return "-";
  return `${targetType || "item"} ${targetId}`.trim();
}

function updateActionFilterOptions(rows) {
  if (!refs.activityActionFilter) return;
  const selected = normalizeText(refs.activityActionFilter.value || "all");
  const values = Array.from(
    new Set(
      (rows || [])
        .map((row) => normalizeText(row.action))
        .filter(Boolean)
    )
  ).sort();

  refs.activityActionFilter.innerHTML = "";
  const allOption = document.createElement("option");
  allOption.value = "all";
  allOption.textContent = "All Operations";
  refs.activityActionFilter.appendChild(allOption);

  values.forEach((value) => {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = prettifyAction(value);
    refs.activityActionFilter.appendChild(option);
  });

  refs.activityActionFilter.value = values.includes(selected) ? selected : "all";
}

function getFilteredReportRows() {
  const rows = Array.isArray(state.reportRows) ? state.reportRows : [];
  const query = normalizeText(refs.activitySearchInput?.value || "");
  const selectedAction = normalizeText(refs.activityActionFilter?.value || "all");

  return rows.filter((row) => {
    const rowAction = normalizeText(row.action || "");
    if (selectedAction !== "all" && rowAction !== selectedAction) {
      return false;
    }
    if (!query) {
      return true;
    }

    const adminName = (row.admin_full_name || "").trim();
    const adminEmail = row.admin_username || "-";
    const haystack = [
      row.created_at,
      adminName,
      adminEmail,
      row.action,
      row.target_type,
      row.target_id,
      row.details,
      row.ip_address,
    ]
      .map((value) => normalizeText(value))
      .join(" ");

    return haystack.includes(query);
  });
}

function renderReportRows() {
  if (!refs.activityBody) return;
  const filtered = getFilteredReportRows();
  const total = (state.reportRows || []).length;
  refs.activityBody.innerHTML = "";

  if (refs.activityCount) {
    refs.activityCount.textContent =
      filtered.length === total
        ? `${filtered.length} record${filtered.length === 1 ? "" : "s"}`
        : `${filtered.length} of ${total} records`;
  }

  if (!filtered.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.textContent = total ? "No records match current filter." : "No admin activity yet.";
    tr.appendChild(td);
    refs.activityBody.appendChild(tr);
    return;
  }

  filtered.forEach((row) => {
    const tr = document.createElement("tr");

    const time = document.createElement("td");
    time.textContent = formatDate(row.created_at);

    const admin = document.createElement("td");
    const adminName = (row.admin_full_name || "").trim();
    const adminEmail = row.admin_username || "-";
    admin.textContent = adminName ? `${adminName} (${adminEmail})` : adminEmail;

    const operation = document.createElement("td");
    operation.textContent = prettifyAction(row.action);

    const target = document.createElement("td");
    target.textContent = buildTargetLabel(row);

    const details = document.createElement("td");
    details.textContent = (row.details || "").trim() || "-";

    const ip = document.createElement("td");
    ip.textContent = (row.ip_address || "").trim() || "-";

    tr.append(time, admin, operation, target, details, ip);
    refs.activityBody.appendChild(tr);
  });
}

async function loadReport() {
  try {
    const data = await apiFetch("/api/admin/activity-report?limit=500");
    state.reportRows = Array.isArray(data.report) ? data.report : [];
    updateActionFilterOptions(state.reportRows);
    renderReportRows();
    setStatus("Activity report loaded.");
  } catch (error) {
    setStatus(error.message || "Failed to load activity report.", true);
  }
}

async function bootstrapUser() {
  try {
    const data = await apiFetch("/api/me");
    state.user = data.user;
    if (state.user.role !== "admin") {
      window.location.href = "/";
      return;
    }

    const displayName = state.user.full_name || state.user.username;
    const displayEmail = state.user.username || "-";
    const displayRole = (state.user.role || "user").toUpperCase();

    if (refs.userName) refs.userName.textContent = displayName;
    if (refs.userRole) refs.userRole.textContent = displayRole;
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
      closeProfileMenu();
    }
  });

  refs.refreshActivity?.addEventListener("click", loadReport);
  refs.activitySearchInput?.addEventListener("input", renderReportRows);
  refs.activityActionFilter?.addEventListener("change", renderReportRows);
  refs.activityFilterClear?.addEventListener("click", () => {
    if (refs.activitySearchInput) refs.activitySearchInput.value = "";
    if (refs.activityActionFilter) refs.activityActionFilter.value = "all";
    renderReportRows();
  });
}

async function init() {
  attachEvents();
  await bootstrapUser();
  await loadReport();
}

init();
