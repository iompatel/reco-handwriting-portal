const refs = {
  status: document.getElementById("auth-status"),
  loader: document.getElementById("global-loader"),
  loaderMessage: document.getElementById("global-loader-message"),
  loginForm: document.getElementById("login-form"),
  loginUsername: document.getElementById("login-username"),
  loginPassword: document.getElementById("login-password"),
  registerForm: document.getElementById("register-form"),
  regFullName: document.getElementById("reg-full-name"),
  regUsername: document.getElementById("reg-username"),
  regPassword: document.getElementById("reg-password"),
  regConfirm: document.getElementById("reg-confirm"),
  resetForm: document.getElementById("reset-form"),
  resetUsername: document.getElementById("reset-username"),
  resetNewPassword: document.getElementById("reset-new-password"),
  resetConfirm: document.getElementById("reset-confirm"),
  showRegister: document.getElementById("show-register"),
  showReset: document.getElementById("show-reset"),
  showLoginFromRegister: document.getElementById("show-login-from-register"),
  showLoginFromReset: document.getElementById("show-login-from-reset"),
  showRegisterFromReset: document.getElementById("show-register-from-reset"),
  googleLoginButton: document.getElementById("google-login-button"),
};

const firebaseWebConfig = window.firebaseWebConfig || {};
const firebaseGoogleEnabled = Boolean(window.firebaseGoogleEnabled);

let firebaseAuthClient = null;
let firebaseModules = null;
let loaderCount = 0;

function setStatus(message, type = "") {
  refs.status.textContent = message;
  refs.status.className = `status ${type}`.trim();
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

async function apiFetch(url, payload) {
  showLoader("RNN loading...");
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    let data = {};
    try {
      data = await response.json();
    } catch {
      data = {};
    }

    if (!response.ok || !data.ok) {
      throw new Error(data.error || "Request failed");
    }

    return data;
  } finally {
    hideLoader();
  }
}

function showAuthForm(view) {
  const forms = {
    login: refs.loginForm,
    register: refs.registerForm,
    reset: refs.resetForm,
  };
  Object.entries(forms).forEach(([name, form]) => {
    if (!form) return;
    form.classList.toggle("is-hidden", name !== view);
  });
}

function initPasswordToggles() {
  const toggleButtons = document.querySelectorAll("[data-toggle-password]");
  toggleButtons.forEach((button) => {
    const targetId = button.getAttribute("data-target");
    const input = document.getElementById(targetId || "");
    if (!input) return;

    button.addEventListener("click", () => {
      const makeVisible = input.type === "password";
      input.type = makeVisible ? "text" : "password";
      button.textContent = makeVisible ? "Hide" : "Show";
      button.setAttribute("aria-pressed", makeVisible ? "true" : "false");
    });
  });
}

function hasFirebaseWebConfig() {
  const requiredKeys = ["apiKey", "authDomain", "projectId", "appId"];
  return requiredKeys.every((key) => typeof firebaseWebConfig[key] === "string" && firebaseWebConfig[key].trim());
}

async function loadFirebaseModules() {
  if (firebaseModules) {
    return firebaseModules;
  }

  const [firebaseAppModule, firebaseAuthModule] = await Promise.all([
    import("https://www.gstatic.com/firebasejs/10.12.5/firebase-app.js"),
    import("https://www.gstatic.com/firebasejs/10.12.5/firebase-auth.js"),
  ]);

  firebaseModules = {
    initializeApp: firebaseAppModule.initializeApp,
    getAuth: firebaseAuthModule.getAuth,
    GoogleAuthProvider: firebaseAuthModule.GoogleAuthProvider,
    signInWithPopup: firebaseAuthModule.signInWithPopup,
  };
  return firebaseModules;
}

async function getFirebaseAuthClient() {
  if (!firebaseGoogleEnabled || !hasFirebaseWebConfig()) {
    throw new Error("Google login is not configured. Contact admin.");
  }
  if (!firebaseAuthClient) {
    const firebase = await loadFirebaseModules();
    const firebaseApp = firebase.initializeApp(firebaseWebConfig);
    firebaseAuthClient = firebase.getAuth(firebaseApp);
  }
  return firebaseAuthClient;
}

function googleLoginErrorMessage(error) {
  const code = typeof error?.code === "string" ? error.code : "";
  if (code.endsWith("popup-closed-by-user")) {
    return "Google popup closed before sign-in completed.";
  }
  if (code.endsWith("popup-blocked")) {
    return "Popup blocked. Please allow popups and try again.";
  }
  if (code.endsWith("unauthorized-domain")) {
    const currentHost = window.location.hostname || "this host";
    if (currentHost === "127.0.0.1" || currentHost === "0.0.0.0") {
      return `Firebase blocked ${currentHost}. Open this app on localhost or add ${currentHost} in Firebase Authorized domains.`;
    }
    return `This domain (${currentHost}) is not authorized in Firebase settings.`;
  }
  if (typeof error?.message === "string" && error.message.trim()) {
    return error.message;
  }
  return "Google login failed. Please try again.";
}

function maybeRedirectForFirebaseLocalhost() {
  const localBlockedHosts = new Set(["127.0.0.1", "0.0.0.0"]);
  const currentHost = window.location.hostname || "";
  if (!localBlockedHosts.has(currentHost)) {
    return false;
  }

  const port = window.location.port ? `:${window.location.port}` : "";
  const redirectUrl = `${window.location.protocol}//localhost${port}${window.location.pathname}${window.location.search}${window.location.hash}`;
  setStatus("Redirecting to localhost for Google sign-in...");
  window.location.replace(redirectUrl);
  return true;
}

async function handleGoogleLogin() {
  if (maybeRedirectForFirebaseLocalhost()) {
    return;
  }

  setStatus("Opening Google sign-in...");
  try {
    const firebase = await loadFirebaseModules();
    const authClient = await getFirebaseAuthClient();
    const provider = new firebase.GoogleAuthProvider();
    provider.setCustomParameters({ prompt: "select_account" });

    const result = await firebase.signInWithPopup(authClient, provider);
    const idToken = await result.user.getIdToken();

    setStatus("Signing in with Google...");
    await apiFetch("/api/auth/firebase-login", { id_token: idToken });
    window.location.href = "/";
  } catch (error) {
    setStatus(googleLoginErrorMessage(error), "error");
  }
}

refs.showRegister?.addEventListener("click", () => {
  showAuthForm("register");
  setStatus("");
});

refs.showReset?.addEventListener("click", () => {
  showAuthForm("reset");
  setStatus("");
});

refs.showLoginFromRegister?.addEventListener("click", () => {
  showAuthForm("login");
  setStatus("");
});

refs.showLoginFromReset?.addEventListener("click", () => {
  showAuthForm("login");
  setStatus("");
});

refs.showRegisterFromReset?.addEventListener("click", () => {
  showAuthForm("register");
  setStatus("");
});

refs.googleLoginButton?.addEventListener("click", handleGoogleLogin);

refs.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Logging in...");
  try {
    await apiFetch("/api/auth/login", {
      username: refs.loginUsername.value,
      password: refs.loginPassword.value,
    });
    window.location.href = "/";
  } catch (error) {
    setStatus(error.message, "error");
  }
});

refs.registerForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (refs.regPassword.value !== refs.regConfirm.value) {
    setStatus("Passwords do not match.", "error");
    return;
  }

  setStatus("Creating account...");
  try {
    await apiFetch("/api/auth/register", {
      full_name: refs.regFullName.value,
      username: refs.regUsername.value,
      password: refs.regPassword.value,
    });

    refs.loginUsername.value = refs.regUsername.value;
    refs.loginPassword.value = "";
    refs.registerForm.reset();
    showAuthForm("login");
    setStatus("Account created. Please login.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

refs.resetForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (refs.resetNewPassword.value !== refs.resetConfirm.value) {
    setStatus("Reset passwords do not match.", "error");
    return;
  }

  setStatus("Resetting password...");
  try {
    await apiFetch("/api/auth/reset-password", {
      username: refs.resetUsername.value,
      new_password: refs.resetNewPassword.value,
    });

    refs.loginUsername.value = refs.resetUsername.value;
    refs.loginPassword.value = "";
    refs.resetForm.reset();
    showAuthForm("login");
    setStatus("Password reset successful. Please login.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

showAuthForm("login");
initPasswordToggles();
