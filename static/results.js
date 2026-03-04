const RESULT_CACHE_KEY = "reco_latest_result_v1";

const state = {
  user: null,
  results: [],
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
  status: document.getElementById("results-status"),
  outputText: document.getElementById("output-text"),
  copyOutput: document.getElementById("copy-output"),
  results: document.getElementById("results"),
  resultsList: document.getElementById("results-list"),
  summary: document.getElementById("summary"),
  speakAll: document.getElementById("speak-all"),
  stopSpeech: document.getElementById("stop-speech"),
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

function hasSpeechSupport() {
  return "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function confidencePercent(conf) {
  return Math.max(0, Math.min(100, Math.round((conf || 0) * 100)));
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
    const response = await fetch("/api/profile/avatar", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Failed to upload profile image.");
    }

    if (state.user) state.user.avatar_url = data.avatar_url || "";
    updateAvatarPreview(data.avatar_url || "", refs.userName?.textContent || "User");
    setStatus("Profile image updated.");
  } catch (error) {
    setStatus(error.message || "Profile image upload failed.", true);
  } finally {
    if (refs.profileAvatarInput) refs.profileAvatarInput.value = "";
  }
}

function buildOutputText() {
  if (state.results.length === 0) return "";
  if (state.results.length === 1) return (state.results[0].prediction || "(blank)").trim() || "(blank)";
  return state.results
    .map((item) => `${item.file}: ${(item.prediction || "(blank)").trim() || "(blank)"}`)
    .join("\n");
}

function updateOutputWindow() {
  const text = buildOutputText();
  refs.outputText.value = text;
  refs.copyOutput.disabled = text.length === 0;
}

function speakAll() {
  if (!hasSpeechSupport()) {
    setStatus("Speech synthesis is not supported in this browser.", true);
    return;
  }
  const text = refs.outputText.value.trim();
  if (!text) {
    setStatus("No output text to speak.", true);
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function stopSpeech() {
  if (hasSpeechSupport()) {
    window.speechSynthesis.cancel();
    setStatus("Speech stopped.");
  }
}

function speak(text) {
  if (!hasSpeechSupport()) {
    setStatus("Speech synthesis is not supported in this browser.", true);
    return;
  }
  if (!text || !text.trim()) {
    setStatus("No text available to speak.", true);
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function renderResults(data) {
  state.results = data.results || [];
  refs.resultsList.innerHTML = "";

  state.results.forEach((item) => {
    const card = document.createElement("article");
    card.className = "result-item";

    const top = document.createElement("div");
    top.className = "result-top";

    const left = document.createElement("div");
    const fileLabel = document.createElement("div");
    fileLabel.className = "filename";
    fileLabel.textContent = item.file || "unknown";

    const predictionLabel = document.createElement("div");
    predictionLabel.className = "prediction";
    predictionLabel.textContent = item.prediction || "(blank)";
    left.append(fileLabel, predictionLabel);

    const confPercent = confidencePercent(item.confidence || 0);
    const right = document.createElement("div");
    right.className = "conf";

    const confText = document.createElement("small");
    confText.textContent = `Confidence ${confPercent}%`;

    const meter = document.createElement("div");
    meter.className = "meter";
    const meterFill = document.createElement("span");
    meterFill.style.width = `${confPercent}%`;
    meter.appendChild(meterFill);
    right.append(confText, meter);

    top.append(left, right);

    const alternatives = document.createElement("p");
    alternatives.className = "alt";
    const altText = (item.alternatives || [])
      .map((alt) => `${alt.text} (${Math.round((alt.confidence || 0) * 100)}%)`)
      .join("  |  ");
    alternatives.textContent = altText ? `Alternatives: ${altText}` : "Alternatives: none";

    const actions = document.createElement("div");
    actions.className = "result-actions";

    const speakBtn = document.createElement("button");
    speakBtn.type = "button";
    speakBtn.className = "btn ghost";
    speakBtn.textContent = "Speak";
    speakBtn.addEventListener("click", () => speak(item.prediction || ""));

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn ghost";
    copyBtn.textContent = "Copy";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(item.prediction || "");
        setStatus(`Copied prediction from ${item.file}`);
      } catch {
        setStatus("Clipboard permission denied.", true);
      }
    });

    actions.append(speakBtn, copyBtn);
    card.append(top, alternatives, actions);
    refs.resultsList.appendChild(card);
  });

  const meta = data.meta || {};
  const langsUsed = Array.isArray(meta.ocr_languages_used) ? meta.ocr_languages_used.join("+") : "";
  refs.summary.textContent = `Processed ${meta.count || state.results.length} image(s) | Avg confidence: ${Math.round(
    (meta.avg_confidence || 0) * 100
  )}% | OCR: ${meta.ocr_engine || "auto"} | Lang: ${langsUsed || meta.ocr_languages_requested || "auto"} | Device: ${
    meta.device || "auto"
  }`;

  updateOutputWindow();
  refs.results.classList.remove("hidden");
}

function loadCachedResults() {
  try {
    const raw = sessionStorage.getItem(RESULT_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed?.payload || null;
  } catch {
    return null;
  }
}

async function bootstrapUser() {
  try {
    const data = await apiFetch("/api/me");
    state.user = data.user;
    const displayName = state.user.full_name || state.user.username;
    const displayEmail = state.user.username || "-";
    const displayRole = (state.user.role || "user").toUpperCase();
    refs.userName.textContent = displayName;
    refs.userRole.textContent = displayRole;
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

  refs.copyOutput.addEventListener("click", async () => {
    const text = refs.outputText.value;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Output text copied.");
    } catch {
      setStatus("Clipboard permission denied.", true);
    }
  });

  refs.speakAll.addEventListener("click", speakAll);
  refs.stopSpeech.addEventListener("click", stopSpeech);
}

async function init() {
  attachEvents();
  await bootstrapUser();
  const cached = loadCachedResults();
  if (!cached || !Array.isArray(cached.results) || cached.results.length === 0) {
    setStatus("No recent output found. Please run Recognize Text first.", true);
    return;
  }
  renderResults(cached);
  setStatus("Recognition output loaded.");
}

init();
