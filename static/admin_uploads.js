const state = {
  user: null,
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
  uploadsBody: document.getElementById("uploads-body"),
  refreshUploads: document.getElementById("refresh-uploads"),
  status: document.getElementById("uploads-status"),
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

function renderUploads(rows) {
  if (!refs.uploadsBody) return;
  refs.uploadsBody.innerHTML = "";

  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "No uploads yet.";
    tr.appendChild(td);
    refs.uploadsBody.appendChild(tr);
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");

    const user = document.createElement("td");
    const name = (row.full_name || "").trim();
    const email = row.username || "-";
    user.textContent = name ? `${name} (${email})` : email;

    const file = document.createElement("td");
    file.textContent = row.file_name || "-";

    const conf = document.createElement("td");
    conf.textContent = `${Math.round((row.confidence || 0) * 100)}%`;

    const source = document.createElement("td");
    source.textContent = row.source || "-";

    const time = document.createElement("td");
    time.textContent = formatDate(row.created_at);

    tr.append(user, file, conf, source, time);
    refs.uploadsBody.appendChild(tr);
  });
}

async function loadUploads() {
  try {
    const data = await apiFetch("/api/admin/uploads?limit=300");
    renderUploads(data.uploads || []);
    setStatus("Uploads loaded.");
  } catch (error) {
    setStatus(error.message || "Failed to load uploads.", true);
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

  refs.refreshUploads?.addEventListener("click", loadUploads);
}

async function init() {
  attachEvents();
  await bootstrapUser();
  await loadUploads();
}

init();
