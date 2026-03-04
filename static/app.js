const state = {
  files: [],
  results: [],
  adminUsers: [],
  adminActivity: [],
  user: null,
  ocrCapabilities: null,
};

const DEFAULTS = {
  decodeMode: "beam",
  beamWidth: 8,
  topK: 3,
};
const RESULT_CACHE_KEY = "reco_latest_result_v1";

const refs = {
  userName: document.getElementById("user-name"),
  userRole: document.getElementById("user-role"),
  profileToggle: document.getElementById("profile-toggle"),
  profileToggleAvatar: document.getElementById("profile-toggle-avatar"),
  topHistoryLink: document.getElementById("top-history-link"),
  profileMenu: document.getElementById("profile-menu"),
  profileName: document.getElementById("profile-name"),
  profileEmail: document.getElementById("profile-email"),
  profileRole: document.getElementById("profile-role"),
  profileAvatarPreview: document.getElementById("profile-avatar-preview"),
  profileAvatarInput: document.getElementById("profile-avatar-input"),
  profileLogoutBtn: document.getElementById("profile-logout-btn"),
  imageInput: document.getElementById("image-input"),
  browseBtn: document.getElementById("browse-btn"),
  dropZone: document.getElementById("drop-zone"),
  previewGrid: document.getElementById("preview-grid"),
  imageEditorModal: document.getElementById("image-editor-modal"),
  editorCanvas: document.getElementById("editor-canvas"),
  editorClose: document.getElementById("editor-close"),
  editorRotateLeft: document.getElementById("editor-rotate-left"),
  editorRotateRight: document.getElementById("editor-rotate-right"),
  editorResetCrop: document.getElementById("editor-reset-crop"),
  editorSave: document.getElementById("editor-save"),
  fileCount: document.getElementById("file-count"),
  runBtn: document.getElementById("run-btn"),
  clearBtn: document.getElementById("clear-btn"),
  status: document.getElementById("status"),
  outputText: document.getElementById("output-text"),
  copyOutput: document.getElementById("copy-output"),
  results: document.getElementById("results"),
  resultsList: document.getElementById("results-list"),
  summary: document.getElementById("summary"),
  speakAll: document.getElementById("speak-all"),
  stopSpeech: document.getElementById("stop-speech"),
  recognitionSection: document.getElementById("recognition-section"),
  grayscale: document.getElementById("grayscale"),
  denoise: document.getElementById("denoise"),
  adaptiveThreshold: document.getElementById("adaptive-threshold"),
  invertColors: document.getElementById("invert-colors"),
  contrastBoost: document.getElementById("contrast-boost"),
  contrastBoostValue: document.getElementById("contrast-boost-value"),
  handwritingBoost: document.getElementById("handwriting-boost"),
  studentNotebookMode: document.getElementById("student-notebook-mode"),
  removeNotebookLines: document.getElementById("remove-notebook-lines"),
  smartTextCleanup: document.getElementById("smart-text-cleanup"),
  ocrEngine: document.getElementById("ocr-engine"),
  ocrLanguages: document.getElementById("ocr-languages"),
  ocrLanguagesHelp: document.getElementById("ocr-languages-help"),
  speechRate: document.getElementById("speech-rate"),
  speechRateValue: document.getElementById("speech-rate-value"),
  historyPanel: document.getElementById("history-panel"),
  historyBody: document.getElementById("history-body"),
  refreshHistory: document.getElementById("refresh-history"),
  adminSection: document.getElementById("admin-section"),
  downloadAdminExport: document.getElementById("download-admin-export"),
  refreshAdmin: document.getElementById("refresh-admin"),
  adminLiveDatetime: document.getElementById("admin-live-datetime"),
  adminLiveLocation: document.getElementById("admin-live-location"),
  statActiveUsers: document.getElementById("stat-active-users"),
  statTotalUsers: document.getElementById("stat-total-users"),
  statTotalRecords: document.getElementById("stat-total-records"),
  adminUserForm: document.getElementById("admin-user-form"),
  adminFullName: document.getElementById("admin-full-name"),
  adminUsername: document.getElementById("admin-username"),
  adminPassword: document.getElementById("admin-password"),
  adminPasswordToggle: document.getElementById("admin-password-toggle"),
  adminRole: document.getElementById("admin-role"),
  adminUserSearch: document.getElementById("admin-user-search"),
  adminUserRoleFilter: document.getElementById("admin-user-role-filter"),
  adminUserFilterClear: document.getElementById("admin-user-filter-clear"),
  adminUsersCount: document.getElementById("admin-users-count"),
  adminUsersBody: document.getElementById("admin-users-body"),
  refreshAdminActivity: document.getElementById("refresh-admin-activity"),
  adminActivityCount: document.getElementById("admin-activity-count"),
  adminActivityBody: document.getElementById("admin-activity-body"),
  loader: document.getElementById("global-loader"),
  loaderMessage: document.getElementById("global-loader-message"),
};

const editorState = {
  fileIndex: -1,
  sourceFile: null,
  image: null,
  display: null,
  selection: null,
  dragging: false,
};

let loaderCount = 0;
const adminLiveState = {
  clockTimer: null,
  locationResolved: false,
};

function isEditorOpen() {
  return Boolean(refs.imageEditorModal) && !refs.imageEditorModal.classList.contains("hidden");
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function mimeToExtension(mimeType) {
  if (mimeType === "image/jpeg") return "jpg";
  if (mimeType === "image/webp") return "webp";
  return "png";
}

function replaceFileExtension(filename, ext) {
  const safeName = String(filename || "upload");
  const baseName = safeName.replace(/\.[^.]+$/, "");
  return `${baseName}.${ext}`;
}

function getOutputMimeType(file) {
  if (!file?.type) return "image/png";
  if (file.type === "image/jpg") return "image/jpeg";
  if (file.type === "image/jpeg" || file.type === "image/png" || file.type === "image/webp") {
    return file.type;
  }
  return "image/png";
}

function loadImageFromBlob(blob) {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Could not read image."));
    };
    image.src = objectUrl;
  });
}

async function loadImageFromFile(file) {
  return loadImageFromBlob(file);
}

function canvasToBlob(canvas, mimeType) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          reject(new Error("Image processing failed."));
          return;
        }
        resolve(blob);
      },
      mimeType,
      0.95
    );
  });
}

async function canvasToFile(canvas, sourceFile) {
  const mimeType = getOutputMimeType(sourceFile);
  const blob = await canvasToBlob(canvas, mimeType);
  const finalMime = blob.type || mimeType;
  const extension = mimeToExtension(finalMime);
  const fileName = replaceFileExtension(sourceFile?.name || "upload.png", extension);
  return new File([blob], fileName, { type: finalMime, lastModified: Date.now() });
}

function createRotatedCanvas(image, angleDegrees) {
  const normalized = ((angleDegrees % 360) + 360) % 360;
  const radians = (normalized * Math.PI) / 180;
  const outputCanvas = document.createElement("canvas");
  const swapSides = normalized === 90 || normalized === 270;
  outputCanvas.width = swapSides ? image.height : image.width;
  outputCanvas.height = swapSides ? image.width : image.height;
  const context = outputCanvas.getContext("2d");
  if (!context) {
    throw new Error("Canvas context not available.");
  }
  context.translate(outputCanvas.width / 2, outputCanvas.height / 2);
  context.rotate(radians);
  context.drawImage(image, -image.width / 2, -image.height / 2);
  return outputCanvas;
}

function normalizeSelectionRect(selection) {
  if (!selection) return null;
  const x = Math.min(selection.x1, selection.x2);
  const y = Math.min(selection.y1, selection.y2);
  const width = Math.abs(selection.x2 - selection.x1);
  const height = Math.abs(selection.y2 - selection.y1);
  return { x, y, width, height };
}

function clampPointToImage(point) {
  if (!editorState.display) return point;
  return {
    x: clamp(point.x, editorState.display.offsetX, editorState.display.offsetX + editorState.display.drawW),
    y: clamp(point.y, editorState.display.offsetY, editorState.display.offsetY + editorState.display.drawH),
  };
}

function getCanvasPointer(event) {
  if (!refs.editorCanvas) return null;
  const rect = refs.editorCanvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const scaleX = refs.editorCanvas.width / rect.width;
  const scaleY = refs.editorCanvas.height / rect.height;
  return {
    x: (event.clientX - rect.left) * scaleX,
    y: (event.clientY - rect.top) * scaleY,
  };
}

function drawEditorCanvas() {
  if (!refs.editorCanvas) return;
  const context = refs.editorCanvas.getContext("2d");
  if (!context) return;

  const wrapWidth = Math.max(340, Math.floor(refs.editorCanvas.parentElement?.clientWidth || 900));
  const wrapHeight = Math.max(280, Math.min(window.innerHeight - 260, 620));
  refs.editorCanvas.width = wrapWidth;
  refs.editorCanvas.height = wrapHeight;

  context.clearRect(0, 0, refs.editorCanvas.width, refs.editorCanvas.height);
  context.fillStyle = "#f5f9ff";
  context.fillRect(0, 0, refs.editorCanvas.width, refs.editorCanvas.height);

  if (!editorState.image) {
    editorState.display = null;
    return;
  }

  const padding = 8;
  const availableWidth = refs.editorCanvas.width - padding * 2;
  const availableHeight = refs.editorCanvas.height - padding * 2;
  const scale = Math.min(availableWidth / editorState.image.width, availableHeight / editorState.image.height, 1);
  const drawW = Math.max(1, Math.round(editorState.image.width * scale));
  const drawH = Math.max(1, Math.round(editorState.image.height * scale));
  const offsetX = Math.round((refs.editorCanvas.width - drawW) / 2);
  const offsetY = Math.round((refs.editorCanvas.height - drawH) / 2);

  editorState.display = {
    offsetX,
    offsetY,
    drawW,
    drawH,
    scaleX: editorState.image.width / drawW,
    scaleY: editorState.image.height / drawH,
  };

  context.fillStyle = "#fff";
  context.fillRect(offsetX, offsetY, drawW, drawH);
  context.drawImage(editorState.image, offsetX, offsetY, drawW, drawH);

  const selection = normalizeSelectionRect(editorState.selection);
  if (!selection || selection.width < 2 || selection.height < 2) return;

  const clampedStart = clampPointToImage({ x: selection.x, y: selection.y });
  const clampedEnd = clampPointToImage({ x: selection.x + selection.width, y: selection.y + selection.height });
  const safeSelection = normalizeSelectionRect({
    x1: clampedStart.x,
    y1: clampedStart.y,
    x2: clampedEnd.x,
    y2: clampedEnd.y,
  });
  if (!safeSelection || safeSelection.width < 2 || safeSelection.height < 2) return;

  context.save();
  context.fillStyle = "rgba(18, 36, 56, 0.42)";
  context.fillRect(offsetX, offsetY, drawW, drawH);
  // Keep selected crop region visually transparent by re-drawing original pixels
  // instead of clearing to canvas background (which appears white).
  const srcX = Math.round((safeSelection.x - offsetX) * editorState.display.scaleX);
  const srcY = Math.round((safeSelection.y - offsetY) * editorState.display.scaleY);
  const srcW = Math.max(1, Math.round(safeSelection.width * editorState.display.scaleX));
  const srcH = Math.max(1, Math.round(safeSelection.height * editorState.display.scaleY));
  context.drawImage(
    editorState.image,
    srcX,
    srcY,
    srcW,
    srcH,
    safeSelection.x,
    safeSelection.y,
    safeSelection.width,
    safeSelection.height
  );
  context.strokeStyle = "#0b67d0";
  context.lineWidth = 2;
  context.strokeRect(
    safeSelection.x + 0.5,
    safeSelection.y + 0.5,
    Math.max(1, safeSelection.width - 1),
    Math.max(1, safeSelection.height - 1)
  );
  context.restore();
}

function closeImageEditor() {
  if (!refs.imageEditorModal) return;
  refs.imageEditorModal.classList.add("hidden");
  document.body.classList.remove("modal-open");
  editorState.fileIndex = -1;
  editorState.sourceFile = null;
  editorState.image = null;
  editorState.display = null;
  editorState.selection = null;
  editorState.dragging = false;
}

async function openImageEditor(fileIndex) {
  const file = state.files[fileIndex];
  if (!file || !refs.imageEditorModal) return;
  try {
    const image = await loadImageFromFile(file);
    editorState.fileIndex = fileIndex;
    editorState.sourceFile = file;
    editorState.image = image;
    editorState.selection = null;
    editorState.dragging = false;
    refs.imageEditorModal.classList.remove("hidden");
    document.body.classList.add("modal-open");
    drawEditorCanvas();
    setStatus("Editor opened. Drag to crop, rotate if needed, then Save.");
  } catch (error) {
    setStatus(error.message || "Could not open image editor.", true);
  }
}

async function rotateEditorImage(angleDegrees) {
  if (!editorState.image || !editorState.sourceFile) return;
  try {
    const rotatedCanvas = createRotatedCanvas(editorState.image, angleDegrees);
    const blob = await canvasToBlob(rotatedCanvas, getOutputMimeType(editorState.sourceFile));
    editorState.image = await loadImageFromBlob(blob);
    editorState.selection = null;
    drawEditorCanvas();
  } catch (error) {
    setStatus(error.message || "Rotate failed.", true);
  }
}

function clearEditorSelection() {
  editorState.selection = null;
  drawEditorCanvas();
}

function getSelectedCropInSourcePixels() {
  const selection = normalizeSelectionRect(editorState.selection);
  if (!selection || !editorState.display) return null;
  if (selection.width < 6 || selection.height < 6) return null;

  const x1 = clamp(selection.x, editorState.display.offsetX, editorState.display.offsetX + editorState.display.drawW);
  const y1 = clamp(selection.y, editorState.display.offsetY, editorState.display.offsetY + editorState.display.drawH);
  const x2 = clamp(
    selection.x + selection.width,
    editorState.display.offsetX,
    editorState.display.offsetX + editorState.display.drawW
  );
  const y2 = clamp(
    selection.y + selection.height,
    editorState.display.offsetY,
    editorState.display.offsetY + editorState.display.drawH
  );

  const width = x2 - x1;
  const height = y2 - y1;
  if (width < 6 || height < 6) return null;

  return {
    x: Math.max(0, Math.round((x1 - editorState.display.offsetX) * editorState.display.scaleX)),
    y: Math.max(0, Math.round((y1 - editorState.display.offsetY) * editorState.display.scaleY)),
    width: Math.max(1, Math.round(width * editorState.display.scaleX)),
    height: Math.max(1, Math.round(height * editorState.display.scaleY)),
  };
}

async function saveEditorChanges() {
  if (editorState.fileIndex < 0 || !editorState.image || !editorState.sourceFile) return;

  try {
    const sourceCanvas = document.createElement("canvas");
    sourceCanvas.width = editorState.image.width;
    sourceCanvas.height = editorState.image.height;
    const sourceContext = sourceCanvas.getContext("2d");
    if (!sourceContext) {
      throw new Error("Canvas context not available.");
    }
    sourceContext.drawImage(editorState.image, 0, 0);

    const crop = getSelectedCropInSourcePixels();
    let outputCanvas = sourceCanvas;
    if (crop) {
      const cropX = clamp(crop.x, 0, sourceCanvas.width - 1);
      const cropY = clamp(crop.y, 0, sourceCanvas.height - 1);
      const cropWidth = clamp(crop.width, 1, sourceCanvas.width - cropX);
      const cropHeight = clamp(crop.height, 1, sourceCanvas.height - cropY);
      const croppedCanvas = document.createElement("canvas");
      croppedCanvas.width = cropWidth;
      croppedCanvas.height = cropHeight;
      const cropContext = croppedCanvas.getContext("2d");
      if (!cropContext) {
        throw new Error("Canvas context not available.");
      }
      cropContext.drawImage(sourceCanvas, cropX, cropY, cropWidth, cropHeight, 0, 0, cropWidth, cropHeight);
      outputCanvas = croppedCanvas;
    }

    const updatedFile = await canvasToFile(outputCanvas, editorState.sourceFile);
    state.files[editorState.fileIndex] = updatedFile;
    closeImageEditor();
    renderPreview();
    setStatus(crop ? "Image cropped and saved." : "Image saved.");
  } catch (error) {
    setStatus(error.message || "Failed to save image changes.", true);
  }
}

async function rotateFileAtIndex(fileIndex, angleDegrees) {
  const file = state.files[fileIndex];
  if (!file) return;
  try {
    const image = await loadImageFromFile(file);
    const rotatedCanvas = createRotatedCanvas(image, angleDegrees);
    const updatedFile = await canvasToFile(rotatedCanvas, file);
    state.files[fileIndex] = updatedFile;
    renderPreview();
    setStatus("Image rotated.");
  } catch (error) {
    setStatus(error.message || "Failed to rotate image.", true);
  }
}

function handleEditorPointerDown(event) {
  if (!isEditorOpen() || !editorState.display) return;
  const pointer = getCanvasPointer(event);
  if (!pointer) return;
  const insideX =
    pointer.x >= editorState.display.offsetX && pointer.x <= editorState.display.offsetX + editorState.display.drawW;
  const insideY =
    pointer.y >= editorState.display.offsetY && pointer.y <= editorState.display.offsetY + editorState.display.drawH;
  if (!insideX || !insideY) return;

  const clampedPoint = clampPointToImage(pointer);
  editorState.dragging = true;
  editorState.selection = {
    x1: clampedPoint.x,
    y1: clampedPoint.y,
    x2: clampedPoint.x,
    y2: clampedPoint.y,
  };
  refs.editorCanvas?.setPointerCapture?.(event.pointerId);
  drawEditorCanvas();
  event.preventDefault();
}

function handleEditorPointerMove(event) {
  if (!editorState.dragging || !editorState.selection) return;
  const pointer = getCanvasPointer(event);
  if (!pointer) return;
  const clampedPoint = clampPointToImage(pointer);
  editorState.selection.x2 = clampedPoint.x;
  editorState.selection.y2 = clampedPoint.y;
  drawEditorCanvas();
  event.preventDefault();
}

function handleEditorPointerUp(event) {
  if (!editorState.dragging) return;
  editorState.dragging = false;
  refs.editorCanvas?.releasePointerCapture?.(event.pointerId);
  const selection = normalizeSelectionRect(editorState.selection);
  if (!selection || selection.width < 4 || selection.height < 4) {
    editorState.selection = null;
    drawEditorCanvas();
  }
}

function hasSpeechSupport() {
  return "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function showLoader(message = "Loading...") {
  if (!refs.loader) return;
  loaderCount += 1;
  if (refs.loaderMessage) refs.loaderMessage.textContent = message;
  refs.loader.classList.remove("hidden");
}

function hideLoader() {
  if (!refs.loader) return;
  loaderCount = Math.max(0, loaderCount - 1);
  if (loaderCount === 0) {
    refs.loader.classList.add("hidden");
    if (refs.loaderMessage) refs.loaderMessage.textContent = "Loading...";
  }
}

function setStatus(message, isError = false) {
  refs.status.textContent = message;
  refs.status.style.color = isError ? "#b42318" : "#1f4364";
}

function setAdminLocationStatus(message, isError = false) {
  if (!refs.adminLiveLocation) return;
  refs.adminLiveLocation.textContent = message;
  refs.adminLiveLocation.classList.toggle("location-error", isError);
}

function updateAdminDateTime() {
  if (!refs.adminLiveDatetime) return;
  const now = new Date();
  refs.adminLiveDatetime.textContent = now.toLocaleString(undefined, {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function startAdminDateTimeClock() {
  if (!refs.adminLiveDatetime) return;
  if (adminLiveState.clockTimer) {
    clearInterval(adminLiveState.clockTimer);
    adminLiveState.clockTimer = null;
  }
  updateAdminDateTime();
  adminLiveState.clockTimer = window.setInterval(updateAdminDateTime, 1000);
}

function stopAdminDateTimeClock() {
  if (!adminLiveState.clockTimer) return;
  clearInterval(adminLiveState.clockTimer);
  adminLiveState.clockTimer = null;
}

function formatCityCountry(city, country) {
  const cityPart = String(city || "").trim();
  const countryPart = String(country || "").trim();
  if (cityPart && countryPart) return `${cityPart}, ${countryPart}`;
  if (cityPart) return cityPart;
  if (countryPart) return countryPart;
  return "";
}

function pickCityName(address = {}) {
  return (
    address.city ||
    address.town ||
    address.village ||
    address.hamlet ||
    address.municipality ||
    address.suburb ||
    address.state_district ||
    address.state ||
    ""
  );
}

async function fetchLocationFromCoordinates(latitude, longitude) {
  const params = new URLSearchParams({
    format: "jsonv2",
    lat: String(latitude),
    lon: String(longitude),
  });
  const response = await fetch(`https://nominatim.openstreetmap.org/reverse?${params.toString()}`, {
    headers: {
      Accept: "application/json",
    },
  });
  if (!response.ok) {
    throw new Error(`Reverse geocode failed (${response.status})`);
  }
  const data = await response.json();
  const city = pickCityName(data.address || {});
  const country = data.address?.country || "";
  const label = formatCityCountry(city, country);
  if (label) return label;
  if (data.display_name) {
    return String(data.display_name)
      .split(",")
      .slice(0, 2)
      .join(",")
      .trim();
  }
  throw new Error("City not found");
}

async function fetchLocationFromIp() {
  const response = await fetch("https://ipapi.co/json/");
  if (!response.ok) {
    throw new Error(`IP location failed (${response.status})`);
  }
  const data = await response.json();
  const city = data.city || data.region || "";
  const country = data.country_name || data.country || "";
  const label = formatCityCountry(city, country);
  if (!label) {
    throw new Error("IP city unavailable");
  }
  return label;
}

async function resolveAdminLocation() {
  if (!refs.adminLiveLocation || adminLiveState.locationResolved) return;
  setAdminLocationStatus("Fetching city...");

  const finalize = (label, isError = false) => {
    adminLiveState.locationResolved = true;
    setAdminLocationStatus(label, isError);
  };

  const resolveByIpFallback = async () => {
    try {
      const ipLabel = await fetchLocationFromIp();
      finalize(ipLabel, false);
    } catch {
      finalize("Location unavailable", true);
    }
  };

  if (!("geolocation" in navigator)) {
    await resolveByIpFallback();
    return;
  }

  await new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      async (position) => {
        try {
          const label = await fetchLocationFromCoordinates(position.coords.latitude, position.coords.longitude);
          finalize(label, false);
        } catch {
          await resolveByIpFallback();
        } finally {
          resolve();
        }
      },
      async () => {
        await resolveByIpFallback();
        resolve();
      },
      {
        enableHighAccuracy: false,
        timeout: 12000,
        maximumAge: 300000,
      }
    );
  });
}

function updateSliders() {
  refs.contrastBoostValue.textContent = Number(refs.contrastBoost.value).toFixed(1);
  refs.speechRateValue.textContent = Number(refs.speechRate.value).toFixed(2);
}

function updateButtons() {
  const hasFiles = state.files.length > 0;
  refs.runBtn.disabled = !hasFiles;
  refs.clearBtn.disabled = !hasFiles;
}

function cacheLatestResult(payload) {
  try {
    sessionStorage.setItem(
      RESULT_CACHE_KEY,
      JSON.stringify({
        cached_at: Date.now(),
        payload,
      })
    );
  } catch {
    // ignore storage errors
  }
}

function normalizeOcrLanguages(value) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[,\s;]+/g, "+")
    .replace(/\++/g, "+")
    .replace(/^\+|\+$/g, "");
  return normalized || "auto";
}

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

function openFilePicker() {
  try {
    refs.imageInput.value = "";
    if (typeof refs.imageInput.showPicker === "function") {
      refs.imageInput.showPicker();
    } else {
      refs.imageInput.click();
    }
  } catch {
    refs.imageInput.click();
  }
}

function addFiles(fileList) {
  const incoming = Array.from(fileList || []);
  const existing = new Set(state.files.map((file) => `${file.name}-${file.size}-${file.lastModified}`));
  for (const file of incoming) {
    const key = `${file.name}-${file.size}-${file.lastModified}`;
    if (!existing.has(key)) {
      state.files.push(file);
      existing.add(key);
    }
  }
  renderPreview();
}

function clearFiles() {
  if (isEditorOpen()) {
    closeImageEditor();
  }
  state.files = [];
  state.results = [];
  refs.imageInput.value = "";
  renderPreview();
  if (refs.results) refs.results.classList.add("hidden");
  if (refs.resultsList) refs.resultsList.innerHTML = "";
  if (refs.summary) refs.summary.textContent = "";
  if (refs.outputText) refs.outputText.value = "";
  if (refs.copyOutput) refs.copyOutput.disabled = true;
  setStatus("Selection cleared.");
}

function renderPreview() {
  refs.previewGrid.innerHTML = "";
  refs.fileCount.textContent =
    state.files.length === 0
      ? "No files selected"
      : `${state.files.length} image${state.files.length > 1 ? "s" : ""} selected`;

  state.files.forEach((file, fileIndex) => {
    const card = document.createElement("article");
    card.className = "preview-card";

    const img = document.createElement("img");
    img.alt = file.name;
    const objectUrl = URL.createObjectURL(file);
    img.src = objectUrl;
    const revokeObjectUrl = () => URL.revokeObjectURL(objectUrl);
    img.onload = revokeObjectUrl;
    img.onerror = revokeObjectUrl;

    const label = document.createElement("p");
    label.textContent = file.name;

    const actions = document.createElement("div");
    actions.className = "preview-actions";

    const rotateLeftBtn = document.createElement("button");
    rotateLeftBtn.type = "button";
    rotateLeftBtn.className = "btn ghost";
    rotateLeftBtn.textContent = "Rotate Left";
    rotateLeftBtn.addEventListener("click", async () => {
      await rotateFileAtIndex(fileIndex, -90);
    });

    const rotateRightBtn = document.createElement("button");
    rotateRightBtn.type = "button";
    rotateRightBtn.className = "btn ghost";
    rotateRightBtn.textContent = "Rotate Right";
    rotateRightBtn.addEventListener("click", async () => {
      await rotateFileAtIndex(fileIndex, 90);
    });

    const cropBtn = document.createElement("button");
    cropBtn.type = "button";
    cropBtn.className = "btn ghost";
    cropBtn.textContent = "Crop";
    cropBtn.addEventListener("click", async () => {
      await openImageEditor(fileIndex);
    });

    actions.append(rotateLeftBtn, rotateRightBtn, cropBtn);

    const meta = document.createElement("div");
    meta.className = "preview-meta";
    meta.append(label, actions);

    card.append(img, meta);
    refs.previewGrid.appendChild(card);
  });

  updateButtons();
}

function confidencePercent(conf) {
  return Math.max(0, Math.min(100, Math.round((conf || 0) * 100)));
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
  utterance.rate = Number(refs.speechRate.value);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
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
  utterance.rate = Number(refs.speechRate.value);
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function stopSpeech() {
  if (hasSpeechSupport()) {
    window.speechSynthesis.cancel();
    setStatus("Speech stopped.");
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

async function downloadAdminExport() {
  if (state.user?.role !== "admin") {
    setStatus("Admin access required.", true);
    return;
  }

  setStatus("Preparing admin export...");
  try {
    const response = await fetch("/api/admin/export");

    if (!response.ok) {
      let errorMessage = `Export failed (${response.status})`;
      try {
        const data = await response.json();
        if (data?.error) errorMessage = data.error;
      } catch {
        // ignore JSON parsing failure
      }
      throw new Error(errorMessage);
    }

    const blob = await response.blob();
    const filename = extractFilename(response.headers.get("content-disposition"), "admin_export.zip");
    triggerDownload(blob, filename);

    setStatus("Admin export downloaded.");
    await loadAdminActivity();
  } catch (error) {
    setStatus(error.message || "Admin export failed.", true);
  }
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
        // ignore JSON parsing failure
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

function buildOutputText() {
  if (state.results.length === 0) return "";
  if (state.results.length === 1) return (state.results[0].prediction || "(blank)").trim() || "(blank)";
  return state.results
    .map((item) => `${item.file}: ${(item.prediction || "(blank)").trim() || "(blank)"}`)
    .join("\n");
}

function updateOutputWindow() {
  if (!refs.outputText || !refs.copyOutput) return;
  const text = buildOutputText();
  refs.outputText.value = text;
  refs.copyOutput.disabled = text.length === 0;
}

function renderResults(data) {
  if (!refs.resultsList || !refs.summary || !refs.results) return;
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

async function runRecognition() {
  if (state.files.length === 0) {
    setStatus("Please upload images first.", true);
    return;
  }

  const form = new FormData();
  state.files.forEach((file) => form.append("images", file));

  form.append("decode_mode", DEFAULTS.decodeMode);
  form.append("beam_width", String(DEFAULTS.beamWidth));
  form.append("top_k", String(DEFAULTS.topK));

  form.append("autocontrast", "true");
  form.append("grayscale", refs.grayscale.checked ? "true" : "false");
  form.append("denoise", refs.denoise.checked ? "true" : "false");
  form.append("adaptive_threshold", refs.adaptiveThreshold.checked ? "true" : "false");
  form.append("invert_colors", refs.invertColors.checked ? "true" : "false");
  form.append("contrast_boost", refs.contrastBoost.value);
  form.append("handwriting_boost", refs.handwritingBoost.checked ? "true" : "false");
  form.append("student_notebook_mode", refs.studentNotebookMode.checked ? "true" : "false");
  form.append("remove_notebook_lines", refs.removeNotebookLines.checked ? "true" : "false");
  form.append("smart_text_cleanup", refs.smartTextCleanup.checked ? "true" : "false");
  form.append("ocr_engine", refs.ocrEngine.value || "auto");
  form.append("ocr_languages", normalizeOcrLanguages(refs.ocrLanguages.value));

  refs.runBtn.disabled = true;
  setStatus("Running recognition...");
  showLoader("Running RNN recognition...");

  try {
    const response = await fetch("/api/predict", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Prediction failed");
    }

    if (state.user?.role !== "admin") {
      cacheLatestResult(data);
      window.location.href = "/results";
      return;
    }

    renderResults(data);
    const warnings = Array.isArray(data.meta?.warnings) ? data.meta.warnings : [];
    const missing = Array.isArray(data.meta?.unsupported_ocr_languages) ? data.meta.unsupported_ocr_languages : [];
    if (warnings.length || missing.length) {
      const parts = [];
      if (warnings.length) parts.push(warnings.join(" | "));
      if (missing.length) parts.push(`Missing languages: ${missing.join(", ")}`);
      setStatus(`Recognition complete. ${parts.join(" | ")}`, true);
    } else {
      setStatus("Recognition complete.");
    }
    if (state.user.role === "admin") {
      await loadAdminOverview();
    }
  } catch (error) {
    setStatus(error.message || "Prediction request failed.", true);
  } finally {
    hideLoader();
    refs.runBtn.disabled = false;
  }
}

function renderHistoryRows(rows) {
  if (!refs.historyBody) return;
  refs.historyBody.innerHTML = "";
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 4;
    td.textContent = "No history yet.";
    tr.appendChild(td);
    refs.historyBody.appendChild(tr);
    return;
  }

  rows.forEach((row) => {
    const tr = document.createElement("tr");

    const file = document.createElement("td");
    file.textContent =
      state.user?.role === "admin" && row.username
        ? `${row.file_name} (${row.username})`
        : row.file_name;

    const conf = document.createElement("td");
    conf.textContent = `${Math.round((row.confidence || 0) * 100)}%`;

    const time = document.createElement("td");
    time.textContent = formatDate(row.created_at);

    const actions = document.createElement("td");
    actions.className = "table-actions";

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
        if (state.user.role === "admin") {
          await loadAdminOverview();
        }
      } catch (error) {
        setStatus(error.message, true);
      }
    });

    if (state.user?.role === "admin") {
      const editBtn = document.createElement("button");
      editBtn.className = "btn ghost";
      editBtn.textContent = "Edit";
      editBtn.addEventListener("click", async () => {
        const nextPrediction = prompt("Edit prediction text:", row.prediction || "");
        if (nextPrediction === null) return;
        try {
          await apiFetch(`/api/history/${row.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prediction: nextPrediction }),
          });
          await loadHistory();
        } catch (error) {
          setStatus(error.message, true);
        }
      });
      actions.append(editBtn);
    }

    actions.append(deleteBtn);
    tr.append(file, conf, time, actions);
    refs.historyBody.appendChild(tr);
  });
}

async function loadHistory() {
  if (!refs.historyBody) return;
  try {
    const endpoint = state.user?.role === "admin" ? "/api/history?scope=all" : "/api/history";
    const data = await apiFetch(endpoint);
    renderHistoryRows(data.history || []);
  } catch (error) {
    setStatus(error.message, true);
  }
}

function getFilteredAdminUsers(users) {
  const searchTerm = String(refs.adminUserSearch?.value || "")
    .trim()
    .toLowerCase();
  const selectedRole = String(refs.adminUserRoleFilter?.value || "all").trim().toLowerCase();

  return users.filter((user) => {
    const roleMatch = selectedRole === "all" || String(user.role || "").toLowerCase() === selectedRole;
    if (!roleMatch) return false;
    if (!searchTerm) return true;

    const username = String(user.username || "").toLowerCase();
    const fullName = String(user.full_name || "").toLowerCase();
    return username.includes(searchTerm) || fullName.includes(searchTerm);
  });
}

function renderAdminUsers() {
  const users = Array.isArray(state.adminUsers) ? state.adminUsers : [];
  const filteredUsers = getFilteredAdminUsers(users);
  if (refs.adminUsersCount) {
    refs.adminUsersCount.textContent = `${filteredUsers.length} of ${users.length} users`;
  }

  refs.adminUsersBody.innerHTML = "";
  if (!users.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "No users.";
    tr.appendChild(td);
    refs.adminUsersBody.appendChild(tr);
    return;
  }

  if (!filteredUsers.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 5;
    td.textContent = "No users match current filter.";
    tr.appendChild(td);
    refs.adminUsersBody.appendChild(tr);
    return;
  }

  filteredUsers.forEach((user) => {
    const tr = document.createElement("tr");

    const username = document.createElement("td");
    username.textContent = user.username;

    const fullName = document.createElement("td");
    fullName.textContent = user.full_name || "-";

    const role = document.createElement("td");
    role.textContent = user.role;

    const lastLogin = document.createElement("td");
    lastLogin.textContent = formatDate(user.last_login);

    const actions = document.createElement("td");
    actions.className = "table-actions";

    if (user.role !== "admin") {
      const deleteBtn = document.createElement("button");
      deleteBtn.className = "btn ghost";
      deleteBtn.textContent = "Delete";
      deleteBtn.addEventListener("click", async () => {
        if (!confirm(`Delete user ${user.username}?`)) return;
        try {
          await apiFetch(`/api/admin/users/${user.id}`, { method: "DELETE" });
          await loadAdminUsers();
          await loadAdminOverview();
          await loadAdminActivity();
        } catch (error) {
          setStatus(error.message, true);
        }
      });
      actions.append(deleteBtn);
    }

    tr.append(username, fullName, role, lastLogin, actions);
    refs.adminUsersBody.appendChild(tr);
  });
}

function prettifyAdminAction(action) {
  const normalized = String(action || "")
    .trim()
    .replace(/_/g, " ")
    .toLowerCase();
  if (!normalized) return "-";
  return normalized.replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function renderAdminActivity(rows) {
  if (!refs.adminActivityBody) return;
  const safeRows = Array.isArray(rows) ? rows : [];
  refs.adminActivityBody.innerHTML = "";
  if (refs.adminActivityCount) {
    refs.adminActivityCount.textContent = `${safeRows.length} record${safeRows.length === 1 ? "" : "s"}`;
  }

  if (!safeRows.length) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 6;
    td.textContent = "No admin activity yet.";
    tr.appendChild(td);
    refs.adminActivityBody.appendChild(tr);
    return;
  }

  safeRows.forEach((row) => {
    const tr = document.createElement("tr");

    const time = document.createElement("td");
    time.textContent = formatDate(row.created_at);

    const admin = document.createElement("td");
    const adminName = (row.admin_full_name || "").trim();
    const adminEmail = row.admin_username || "-";
    admin.textContent = adminName ? `${adminName} (${adminEmail})` : adminEmail;

    const operation = document.createElement("td");
    operation.textContent = prettifyAdminAction(row.action);

    const target = document.createElement("td");
    const targetType = String(row.target_type || "").trim();
    const targetId = String(row.target_id || "").trim();
    target.textContent = targetType || targetId ? `${targetType || "item"} ${targetId}`.trim() : "-";

    const details = document.createElement("td");
    details.textContent = (row.details || "").trim() || "-";

    const ip = document.createElement("td");
    ip.textContent = (row.ip_address || "").trim() || "-";

    tr.append(time, admin, operation, target, details, ip);
    refs.adminActivityBody.appendChild(tr);
  });
}

async function loadAdminActivity() {
  if (state.user?.role !== "admin") return;
  if (!refs.adminActivityBody) return;
  try {
    const data = await apiFetch("/api/admin/activity-report?limit=200");
    state.adminActivity = data.report || [];
    renderAdminActivity(state.adminActivity);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadAdminOverview() {
  if (state.user.role !== "admin") return;
  try {
    const data = await apiFetch("/api/admin/overview");
    refs.statActiveUsers.textContent = String(data.stats?.active_users ?? 0);
    refs.statTotalUsers.textContent = String(data.stats?.total_users ?? 0);
    refs.statTotalRecords.textContent = String(data.stats?.total_records ?? 0);
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadAdminUsers() {
  if (state.user.role !== "admin") return;
  try {
    const data = await apiFetch("/api/admin/users");
    state.adminUsers = data.users || [];
    renderAdminUsers();
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function loadOcrCapabilities() {
  try {
    const data = await apiFetch("/api/ocr/capabilities");
    state.ocrCapabilities = data;

    const engines = Array.isArray(data.engines) ? data.engines : [];
    const languages = Array.isArray(data.available_languages) ? data.available_languages : [];
    const defaultLanguage = data.default_language || "eng";

    if (engines.length) {
      Array.from(refs.ocrEngine.options).forEach((option) => {
        option.disabled = !engines.includes(option.value);
      });
      if (!engines.includes(refs.ocrEngine.value)) {
        refs.ocrEngine.value = engines[0];
      }
    }

    if (!refs.ocrLanguages.value || refs.ocrLanguages.value.trim() === "auto") {
      refs.ocrLanguages.value = defaultLanguage || "auto";
    }

    if (languages.length) {
      refs.ocrLanguagesHelp.textContent = `Installed OCR languages: ${languages.join(", ")}`;
    } else {
      refs.ocrLanguagesHelp.textContent = "No OCR language packs detected on server.";
    }
  } catch (error) {
    refs.ocrLanguagesHelp.textContent = "Unable to load OCR language capabilities.";
    setStatus(error.message, true);
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

    if (state.user.role === "admin") {
      refs.adminSection.classList.remove("hidden");
      if (refs.recognitionSection) {
        refs.recognitionSection.classList.add("hidden");
      }
      refs.topHistoryLink?.classList.add("hidden");
      startAdminDateTimeClock();
      resolveAdminLocation();
    } else {
      refs.topHistoryLink?.classList.remove("hidden");
      stopAdminDateTimeClock();
    }
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
      if (isEditorOpen()) {
        closeImageEditor();
      }
      closeProfileMenu();
    }
  });

  refs.browseBtn.addEventListener("click", (event) => {
    event.preventDefault();
    openFilePicker();
  });

  refs.imageInput.addEventListener("change", (event) => addFiles(event.target.files));

  ["dragenter", "dragover"].forEach((eventName) => {
    refs.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      refs.dropZone.classList.add("drag");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    refs.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      refs.dropZone.classList.remove("drag");
    });
  });

  refs.dropZone.addEventListener("drop", (event) => addFiles(event.dataTransfer.files));
  refs.dropZone.addEventListener("click", (event) => {
    if (event.target.closest("#browse-btn")) return;
    openFilePicker();
  });

  refs.contrastBoost.addEventListener("input", updateSliders);
  refs.speechRate.addEventListener("input", updateSliders);
  refs.ocrLanguages.addEventListener("blur", () => {
    refs.ocrLanguages.value = normalizeOcrLanguages(refs.ocrLanguages.value);
  });

  refs.studentNotebookMode.addEventListener("change", () => {
    if (refs.studentNotebookMode.checked) {
      refs.removeNotebookLines.checked = true;
      refs.adaptiveThreshold.checked = true;
      refs.denoise.checked = true;
      refs.contrastBoost.value = String(Math.max(1.5, Number(refs.contrastBoost.value)));
      updateSliders();
    }
  });

  refs.runBtn.addEventListener("click", runRecognition);
  refs.clearBtn.addEventListener("click", clearFiles);

  refs.editorClose?.addEventListener("click", closeImageEditor);
  refs.editorRotateLeft?.addEventListener("click", async () => {
    await rotateEditorImage(-90);
  });
  refs.editorRotateRight?.addEventListener("click", async () => {
    await rotateEditorImage(90);
  });
  refs.editorResetCrop?.addEventListener("click", clearEditorSelection);
  refs.editorSave?.addEventListener("click", saveEditorChanges);
  refs.imageEditorModal?.addEventListener("click", (event) => {
    if (event.target === refs.imageEditorModal) {
      closeImageEditor();
    }
  });
  refs.editorCanvas?.addEventListener("pointerdown", handleEditorPointerDown);
  refs.editorCanvas?.addEventListener("pointermove", handleEditorPointerMove);
  refs.editorCanvas?.addEventListener("pointerup", handleEditorPointerUp);
  refs.editorCanvas?.addEventListener("pointercancel", handleEditorPointerUp);
  refs.editorCanvas?.addEventListener("pointerleave", handleEditorPointerUp);
  window.addEventListener("resize", () => {
    if (isEditorOpen()) {
      drawEditorCanvas();
    }
  });
  window.addEventListener("beforeunload", () => {
    stopAdminDateTimeClock();
  });

  refs.copyOutput?.addEventListener("click", async () => {
    const text = refs.outputText.value;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setStatus("Output text copied.");
    } catch {
      setStatus("Clipboard permission denied.", true);
    }
  });

  refs.speakAll?.addEventListener("click", speakAll);
  refs.stopSpeech?.addEventListener("click", stopSpeech);

  if (refs.adminPassword && refs.adminPasswordToggle) {
    refs.adminPasswordToggle.addEventListener("click", () => {
      const makeVisible = refs.adminPassword.type === "password";
      refs.adminPassword.type = makeVisible ? "text" : "password";
      refs.adminPasswordToggle.textContent = makeVisible ? "Hide" : "Show";
      refs.adminPasswordToggle.setAttribute("aria-pressed", makeVisible ? "true" : "false");
    });
  }

  refs.refreshHistory?.addEventListener("click", loadHistory);
  refs.downloadAdminExport.addEventListener("click", downloadAdminExport);
  refs.refreshAdmin.addEventListener("click", async () => {
    refs.refreshAdmin.disabled = true;
    try {
      await loadAdminOverview();
      await loadAdminUsers();
      await loadAdminActivity();
      setStatus("Admin data refreshed.");
    } finally {
      refs.refreshAdmin.disabled = false;
    }
  });

  refs.refreshAdminActivity?.addEventListener("click", async () => {
    refs.refreshAdminActivity.disabled = true;
    try {
      await loadAdminActivity();
      setStatus("Admin activity report refreshed.");
    } finally {
      refs.refreshAdminActivity.disabled = false;
    }
  });

  refs.adminUserSearch?.addEventListener("input", () => {
    renderAdminUsers();
  });
  refs.adminUserRoleFilter?.addEventListener("change", () => {
    renderAdminUsers();
  });
  refs.adminUserFilterClear?.addEventListener("click", () => {
    if (refs.adminUserSearch) refs.adminUserSearch.value = "";
    if (refs.adminUserRoleFilter) refs.adminUserRoleFilter.value = "all";
    renderAdminUsers();
  });

  refs.adminUserForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await apiFetch("/api/admin/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          full_name: refs.adminFullName.value,
          username: refs.adminUsername.value,
          password: refs.adminPassword.value,
          role: refs.adminRole.value,
        }),
      });

      refs.adminUserForm.reset();
      if (refs.adminPassword) refs.adminPassword.type = "password";
      if (refs.adminPasswordToggle) {
        refs.adminPasswordToggle.textContent = "Show";
        refs.adminPasswordToggle.setAttribute("aria-pressed", "false");
      }
      await loadAdminUsers();
      await loadAdminOverview();
      await loadAdminActivity();
      setStatus("User created.");
    } catch (error) {
      setStatus(error.message, true);
    }
  });
}

async function init() {
  attachEvents();
  updateSliders();
  updateButtons();
  await bootstrapUser();
  await loadOcrCapabilities();
  if (state.user?.role === "admin") {
    await loadAdminOverview();
    await loadAdminUsers();
    await loadAdminActivity();
  }
}

init();
