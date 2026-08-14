"use strict";

const state = {
  token: localStorage.getItem("piudaCaregiverToken") || "",
  setupRequired: false,
  demoMode: false,
  kiosk: new URLSearchParams(window.location.search).get("kiosk") === "1",
  autoSpeak: localStorage.getItem("piudaAutoSpeak") !== "false",
  lastReply: "",
  userRefreshing: false,
  caregiverRefreshing: false,
  sensorRefreshing: false,
  demoRefreshing: false,
  lastAlertId: null,
  alertAudioContext: null,
  userName: "사용자",
  wellnessActivation: "",
  wellnessTimer: null
};
let installPromptEvent = null;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

function networkErrorMessage() {
  return "Pi 서버에 연결하지 못했습니다. Raspberry Pi와 Wi-Fi 연결을 확인해 주세요.";
}

window.addEventListener("beforeinstallprompt", event => {
  event.preventDefault();
  installPromptEvent = event;
  document.querySelector("[data-install-prompt]")?.classList.remove("hidden");
});

function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]);
}

async function api(path, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.body && typeof options.body !== "string") {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let response;
  try {
    response = await fetch(`/api/v1${path}`, { cache: "no-store", ...options, headers });
  } catch {
    throw new Error(networkErrorMessage());
  }
  const data = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data?.message || data?.error || `요청 실패 (${response.status})`);
    error.status = response.status;
    error.data = data;
    throw error;
  }
  return data;
}

function toast(message) {
  const element = $("#toast");
  if (!element) return;
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 2600);
}

function timeOnly(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value).slice(0, 5) : date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
}

function shortDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

async function checkConnection() {
  const element = $("#connection");
  try {
    const health = await api("/health");
    document.body.classList.toggle("demo-mode", Boolean(health.demo_mode));
    element?.classList.add("online");
    element?.classList.remove("offline");
    if (element) element.lastChild.textContent = "연결됨";
  } catch {
    element?.classList.add("offline");
    element?.classList.remove("online");
    if (element) element.lastChild.textContent = "연결 끊김";
  }
}

function riskSentence(risk) {
  if (!risk.factors?.length) return "현재 확인된 위험 신호가 없습니다.";
  if (risk.level === "emergency") return "움직이지 말고 안전한 곳에서 보호자의 확인을 기다려 주세요.";
  return risk.factors.slice(0, 2).map(item => item.label).join(", ") + " 상태를 확인해 주세요.";
}

function caregiverRiskSentence(risk) {
  if (!risk.factors?.length) return "현재 확인된 위험 요인이 없습니다.";
  if (risk.level === "emergency") return "즉시 확인이 필요합니다.";
  return risk.factors.slice(0, 2).map(item => item.label).join(", ") + " 항목을 확인해 주세요.";
}

async function initUser() {
  const tick = () => {
    const current = new Date();
    $("#clock").textContent = current.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
    $("#todayLabel").textContent = current.toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "long" });
  };
  tick();
  setInterval(tick, 30000);

  await refreshUserSnapshot(true);
  setInterval(() => {
    if (!document.hidden) refreshUserSnapshot();
  }, 2000);
  window.addEventListener("focus", () => refreshUserSnapshot());
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refreshUserSnapshot();
  });

  $("#taskList").addEventListener("click", async event => {
    const button = event.target.closest("[data-complete-task]");
    if (!button) return;
    button.disabled = true;
    try {
      const result = await api(`/tasks/${button.dataset.completeTask}/complete`, { method: "POST", body: {} });
      toast("완료로 기록했어요.");
      speak("완료로 기록했어요.");
      await refreshUserSnapshot();
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  });

  $("#assistantForm").addEventListener("submit", askAssistant);
  $$("[data-quick-prompt]").forEach(button => button.addEventListener("click", () => {
    $("#assistantInput").value = button.dataset.quickPrompt;
    $("#assistantForm").requestSubmit();
  }));
  $("#ttsToggle").addEventListener("click", toggleTts);
  $("#replayButton").addEventListener("click", () => speak(state.lastReply, true));
  $("#caregiverAlertButton").addEventListener("click", sendCaregiverAlert);
  $$('[data-wellness-response]').forEach(button => button.addEventListener("click", () => respondWellness(button.dataset.wellnessResponse)));
  updateTtsControls();
  setupVoiceInput();
}

async function refreshUserSnapshot(showError = false) {
  if (state.userRefreshing) return;
  state.userRefreshing = true;
  try {
    const [profile, tasks, risk] = await Promise.all([api("/profile"), api("/tasks/today"), api("/risk/current")]);
    state.userName = profile.user_name || "사용자";
    $("#userName").textContent = state.userName;
    renderUserTasks(tasks);
    renderUserRisk(risk);
  } catch (error) {
    if (showError) {
      $("#taskList").innerHTML = '<div class="empty-state">Pi 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.</div>';
      toast(error.message);
    }
  } finally {
    state.userRefreshing = false;
  }
}

function renderUserTasks(result) {
  $("#taskSummary").textContent = `${result.summary.completed} / ${result.summary.total} 완료`;
  const progress = result.summary.total ? result.summary.completed / result.summary.total * 100 : 0;
  $("#taskProgress").style.width = `${progress}%`;
  if (!result.items.length) {
    $("#taskList").innerHTML = '<div class="empty-state">오늘 등록된 일정이 없어요.</div>';
    return;
  }
  $("#taskList").innerHTML = result.items.map(item => {
    const complete = item.status === "completed";
    const missed = item.status === "missed";
    const icons = { meal: "식", medication: "약", cleaning: "집", sleep: "잠", outing: "밖", hospital: "병", other: "걷" };
    return `<article class="task-card category-${escapeHTML(item.category)} ${complete ? "completed" : ""} ${missed ? "missed" : ""}">
      <div class="task-icon" aria-hidden="true">${complete ? "✓" : icons[item.category] || "•"}</div>
      <div class="task-time">${escapeHTML(item.scheduled_time)}</div>
      <div><h3>${escapeHTML(item.title)}</h3><p>${escapeHTML(item.instructions || (missed ? "예정 시간이 지났어요. 지금 완료할 수 있어요." : "완료하면 버튼을 눌러 주세요."))}</p></div>
      ${complete ? '<span class="pill">✓ 완료</span>' : `<button class="button ${missed ? "secondary" : "primary"}" data-complete-task="${item.id}">완료했어요</button>`}
    </article>`;
  }).join("");
}

function renderUserRisk(risk) {
  const card = $("#statusCard");
  card.className = `status-card status-${risk.level}`;
  $("#riskLabel").textContent = risk.level_label;
  $("#riskScore").textContent = risk.score;
  $("#riskMessage").textContent = risk.user_message || riskSentence(risk);
  syncWellnessPrompt(risk);
}

function syncWellnessPrompt(risk) {
  const dialog = $("#wellnessDialog");
  if (!dialog) return;
  if (risk.scenario_key !== "inactivity_check") {
    clearInterval(state.wellnessTimer);
    state.wellnessTimer = null;
    state.wellnessActivation = "";
    if (dialog.open) dialog.close();
    return;
  }
  if (state.wellnessActivation === risk.assessed_at) return;
  state.wellnessActivation = risk.assessed_at;
  if (!dialog.open) dialog.showModal();
  speak("지금 문제가 있나요? 괜찮으시면 괜찮아요 버튼을 눌러 주세요.");
  const started = new Date(risk.assessed_at).valueOf();
  clearInterval(state.wellnessTimer);
  const update = () => {
    const seconds = Math.max(0, 30 - Math.floor((Date.now() - started) / 1000));
    $("#wellnessCountdown").textContent = seconds
      ? `${seconds}초 동안 응답을 기다릴게요.`
      : "보호자에게 확인을 요청하고 있어요.";
    if (!seconds) respondWellness("timeout");
  };
  update();
  state.wellnessTimer = setInterval(update, 1000);
}

async function respondWellness(answer) {
  if (!state.wellnessActivation) return;
  state.wellnessActivation = "";
  clearInterval(state.wellnessTimer);
  state.wellnessTimer = null;
  $$('[data-wellness-response]').forEach(button => { button.disabled = true; });
  try {
    await api("/wellness-check", { method: "POST", body: { answer } });
    $("#wellnessDialog").close();
    const message = answer === "ok"
      ? "‘괜찮아요’ 응답을 기록했어요."
      : "보호자에게 확인을 요청했어요.";
    toast(message);
    speak(message);
    await refreshUserSnapshot();
  } catch (error) {
    toast(error.message);
  } finally {
    $$('[data-wellness-response]').forEach(button => { button.disabled = false; });
  }
}

async function sendCaregiverAlert() {
  const button = $("#caregiverAlertButton");
  if (!button || button.disabled) return;
  button.disabled = true;
  button.textContent = "알림 보내는 중…";
  try {
    const result = await api("/caregiver-alert", { method: "POST" });
    const message = result.created
      ? "보호자에게 확인 알림을 보냈어요."
      : "보호자에게 이미 알림을 보냈어요.";
    button.textContent = "알림 전송 완료";
    toast(message);
    speak(message);
  } catch (error) {
    toast(error.message);
  } finally {
    window.setTimeout(() => {
      button.disabled = false;
      button.textContent = "보호자에게 알림 보내기";
    }, 2500);
  }
}

async function askAssistant(event) {
  event.preventDefault();
  const input = $("#assistantInput");
  const message = input.value.trim();
  if (!message) return;
  const submit = event.currentTarget.querySelector('[type="submit"]');
  const question = $("#assistantQuestion");
  question.textContent = message;
  question.classList.remove("hidden");
  submit.disabled = true;
  $("#assistantReply").textContent = "답변을 준비하고 있어요…";
  try {
    const result = await api("/feedback", { method: "POST", body: { message } });
    $("#assistantReply").textContent = result.reply;
    state.lastReply = result.reply;
    $("#replayButton").classList.remove("hidden");
    input.value = "";
    if (result.speak) speak(result.reply);
  } catch (error) {
    $("#assistantReply").textContent = "지금은 답하기 어렵습니다. 잠시 후 다시 말씀해 주세요.";
    toast(error.message);
  } finally {
    submit.disabled = false;
  }
}

function updateTtsControls() {
  const button = $("#ttsToggle");
  if (!button) return;
  button.textContent = state.autoSpeak ? "소리 켜짐" : "소리 꺼짐";
  button.setAttribute("aria-pressed", String(state.autoSpeak));
}

function toggleTts() {
  state.autoSpeak = !state.autoSpeak;
  localStorage.setItem("piudaAutoSpeak", String(state.autoSpeak));
  if (!state.autoSpeak && "speechSynthesis" in window) window.speechSynthesis.cancel();
  updateTtsControls();
  toast(state.autoSpeak ? "답변을 음성으로 안내합니다." : "음성 안내를 껐습니다.");
}

async function speak(text, force = false) {
  if (!text || (!force && !state.autoSpeak)) return;
  if (state.kiosk) {
    try {
      await api("/tts", { method: "POST", body: { text } });
      return;
    } catch {
      toast("Pi 음성 출력을 확인해 주세요.");
    }
  }
  if (!("speechSynthesis" in window)) {
    toast("이 브라우저에서는 음성 출력을 지원하지 않습니다.");
    return;
  }
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ko-KR";
  utterance.rate = .9;
  utterance.pitch = 1;
  const voices = window.speechSynthesis.getVoices();
  utterance.voice = voices.find(voice => voice.lang?.toLowerCase().startsWith("ko")) || null;
  utterance.onerror = () => toast("음성 안내를 재생하지 못했습니다.");
  window.setTimeout(() => {
    window.speechSynthesis.speak(utterance);
    window.speechSynthesis.resume();
  }, 80);
}

function setupVoiceInput() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const button = $("#voiceButton");
  if (!Recognition || !button) {
    if (button) {
      button.disabled = true;
      button.textContent = "마이크 없음";
      button.title = "이 브라우저에서 음성 인식을 사용할 수 없습니다.";
    }
    return;
  }
  const recognition = new Recognition();
  recognition.lang = "ko-KR";
  recognition.interimResults = false;
  recognition.onstart = () => { button.textContent = "듣는 중…"; button.disabled = true; };
  recognition.onend = () => { button.textContent = "● 말하기"; button.disabled = false; };
  recognition.onerror = () => toast("음성을 듣지 못했어요. 다시 시도해 주세요.");
  recognition.onresult = event => { $("#assistantInput").value = event.results[0][0].transcript; };
  button.addEventListener("click", () => recognition.start());
}

async function initCaregiver() {
  $("#authForm").addEventListener("submit", submitAuth);
  $("#logoutButton").addEventListener("click", logout);
  $("#refreshButton").addEventListener("click", loadDashboard);
  $("#localAlertAcknowledge").addEventListener("click", acknowledgeLocalAlert);
  $("#routineForm").addEventListener("submit", submitRoutine);
  $("#sensorForm").addEventListener("submit", submitSensor);
  $$('[data-dialog]').forEach(button => button.addEventListener("click", () => $("#" + button.dataset.dialog).showModal()));
  $$('[data-close]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#alertList").addEventListener("click", acknowledgeAlert);
  document.addEventListener("pointerdown", prepareAlertAudio, { once: true });
  try {
    const onboard = await api("/onboarding");
    state.setupRequired = onboard.setup_required;
    state.demoMode = onboard.demo_mode;
    configureAuthPanel();
    if (!state.setupRequired && state.token) await loadDashboard();
  } catch (error) {
    $("#authError").textContent = error.message;
  }
  setInterval(() => {
    if (state.token && !document.hidden) loadDashboard();
  }, 2000);
  setInterval(() => {
    if (state.token && !document.hidden) refreshSensors();
  }, 1000);
  window.addEventListener("focus", () => {
    if (state.token) loadDashboard();
  });
  document.addEventListener("visibilitychange", () => {
    if (state.token && !document.hidden) loadDashboard();
  });
}

function configureAuthPanel() {
  if (state.demoMode) {
    $("#authTitle").textContent = "보호자 로그인";
    $("#authDescription").textContent = "보호자 PIN을 입력해 주세요.";
    return;
  }
  if (state.setupRequired) {
    $("#authTitle").textContent = "처음 보호자 설정";
    $("#authDescription").textContent = "보호자 이름과 PIN을 정해 주세요.";
    $("#caregiverName").classList.remove("hidden");
    $("#nameLabel").classList.remove("hidden");
    $("#pinInput").autocomplete = "new-password";
  }
}

async function submitAuth(event) {
  event.preventDefault();
  prepareAlertAudio();
  $("#authError").textContent = "";
  const body = { pin: $("#pinInput").value, device_name: navigator.userAgent.includes("iPhone") ? "iPhone 웹" : "보호자 웹" };
  if (state.setupRequired) body.name = $("#caregiverName").value;
  try {
    const result = await api(state.setupRequired ? "/auth/setup" : "/auth/login", { method: "POST", body });
    state.token = result.token;
    localStorage.setItem("piudaCaregiverToken", state.token);
    await loadDashboard();
  } catch (error) {
    $("#authError").textContent = error.data?.error === "invalid_pin" ? "PIN이 일치하지 않습니다." : error.message;
  }
}

async function logout() {
  try { await api("/auth/logout", { method: "POST" }); } catch { /* local cleanup still applies */ }
  state.token = "";
  state.lastAlertId = null;
  localStorage.removeItem("piudaCaregiverToken");
  $("#localAlertDialog").close();
  $("#dashboard").classList.add("hidden");
  $("#authPanel").classList.remove("hidden");
  $("#logoutButton").classList.add("hidden");
  $("#pinInput").value = "";
}

async function loadDashboard() {
  if (state.caregiverRefreshing || !state.token) return;
  state.caregiverRefreshing = true;
  try {
    const result = await api("/dashboard");
    $("#authPanel").classList.add("hidden");
    $("#dashboard").classList.remove("hidden");
    $("#logoutButton").classList.remove("hidden");
    state.userName = result.profile?.user_name || "사용자";
    $("#careUserName").textContent = state.userName;
    $("#careDate").textContent = new Date().toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "long" });
    $("#lastRefresh").textContent = new Date().toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
    renderCareRisk(result.risk);
    renderCareTasks(result.tasks);
    renderAlerts(result.alerts);
    renderEvents(result.sensor_events);
    notifyNewCaregiverAlert(result.alerts);
    await refreshSensors();
  } catch (error) {
    if (error.status === 401) {
      state.token = "";
      localStorage.removeItem("piudaCaregiverToken");
      $("#authPanel").classList.remove("hidden");
      $("#dashboard").classList.add("hidden");
    } else toast(error.message);
  } finally {
    state.caregiverRefreshing = false;
  }
}

async function refreshSensors() {
  if (state.sensorRefreshing || !state.token || document.hidden) return;
  state.sensorRefreshing = true;
  try {
    const sensors = await api("/sensors");
    renderSensors(sensors.items);
  } catch {
    // 인증 만료와 연결 오류는 2초 주기의 전체 대시보드 갱신에서 한 번만 처리합니다.
  } finally {
    state.sensorRefreshing = false;
  }
}

function prepareAlertAudio() {
  if (state.alertAudioContext) {
    state.alertAudioContext.resume().catch(() => {});
    return;
  }
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext) return;
  state.alertAudioContext = new AudioContext();
  state.alertAudioContext.resume().catch(() => {});
}

function playAlertTone() {
  const context = state.alertAudioContext;
  if (!context) return;
  context.resume().then(() => {
    const start = context.currentTime;
    const gain = context.createGain();
    gain.gain.setValueAtTime(0.0001, start);
    gain.gain.exponentialRampToValueAtTime(0.18, start + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.8);
    gain.connect(context.destination);
    [659, 880].forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      oscillator.type = "sine";
      oscillator.frequency.value = frequency;
      oscillator.connect(gain);
      oscillator.start(start + index * 0.2);
      oscillator.stop(start + 0.55 + index * 0.2);
    });
  }).catch(() => {});
  navigator.vibrate?.([180, 90, 180]);
}

function notifyNewCaregiverAlert(alerts) {
  const newestId = alerts.reduce((maximum, item) => Math.max(maximum, Number(item.id) || 0), 0);
  const fresh = alerts
    .filter(item => !item.acknowledged_at && (state.lastAlertId === null || Number(item.id) > state.lastAlertId))
    .sort((left, right) => Number(right.id) - Number(left.id))[0];
  state.lastAlertId = Math.max(state.lastAlertId ?? 0, newestId);
  if (!fresh) return;
  const dialog = $("#localAlertDialog");
  dialog.dataset.alertId = fresh.id;
  dialog.className = `local-alert-dialog level-${fresh.level}`;
  $("#localAlertTitle").textContent = fresh.title;
  $("#localAlertMessage").textContent = fresh.message;
  $("#localAlertTime").textContent = shortDateTime(fresh.created_at);
  if (!dialog.open) dialog.showModal();
  playAlertTone();
}

async function acknowledgeLocalAlert() {
  const dialog = $("#localAlertDialog");
  const alertId = dialog.dataset.alertId;
  if (!alertId) return;
  const button = $("#localAlertAcknowledge");
  button.disabled = true;
  try {
    await api(`/alerts/${alertId}/ack`, { method: "POST" });
    dialog.close();
    await loadDashboard();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

function renderCareRisk(risk) {
  $("#careRiskScore").textContent = risk.score;
  $("#careRiskLabel").textContent = risk.level_label;
  $("#careRiskText").textContent = caregiverRiskSentence(risk);
  $("#riskRing").style.setProperty("--risk-angle", `${risk.score * 3.6}deg`);
  $("#riskFactors").innerHTML = risk.factors.length ? risk.factors.map(item => `<div class="factor-item"><div>${escapeHTML(item.label)}<br><span>${escapeHTML(item.evidence || "기록된 세부 정보 없음")}</span></div><strong>-${item.points}</strong></div>`).join("") : '<div class="empty-state">현재 확인된 위험 요인이 없습니다.</div>';
}

function renderCareTasks(tasks) {
  const completed = tasks.filter(item => item.status === "completed").length;
  $("#completionMetric").textContent = `${completed} / ${tasks.length}`;
  $("#completionText").textContent = tasks.length ? `${Math.round(completed / tasks.length * 100)}% 완료` : "등록된 일정 없음";
  $("#careTaskList").innerHTML = tasks.length ? tasks.map(item => `<div class="compact-item"><div><strong>${escapeHTML(item.scheduled_time)} · ${escapeHTML(item.title)}</strong><small>${escapeHTML(item.instructions || item.category)}</small></div><span class="pill ${item.status}">${({pending:"예정",completed:"완료",missed:"미수행",skipped:"건너뜀"})[item.status]}</span></div>`).join("") : '<div class="empty-state">오늘 일정이 없습니다.</div>';
}

function renderAlerts(alerts) {
  const open = alerts.filter(item => !item.acknowledged_at);
  $("#alertMetric").textContent = open.length;
  $("#alertList").innerHTML = alerts.length ? alerts.map(item => `<div class="compact-item"><div><strong>${escapeHTML(item.title)}</strong><small>${escapeHTML(item.message)} · ${shortDateTime(item.created_at)}</small></div>${item.acknowledged_at ? '<span class="pill">확인함</span>' : `<button class="text-button" data-ack-alert="${item.id}">처리 확인</button>`}</div>`).join("") : '<div class="empty-state">최근 알림이 없습니다.</div>';
}

function renderSensors(items) {
  $("#sensorList").innerHTML = items.length ? items.map(item => {
    const lastSeen = item.last_seen_at ? new Date(item.last_seen_at) : null;
    const createdAt = item.created_at ? new Date(item.created_at) : null;
    const hasLastSeen = Boolean(lastSeen && !Number.isNaN(lastSeen.valueOf()));
    const reference = hasLastSeen
      ? lastSeen
      : createdAt && !Number.isNaN(createdAt.valueOf()) ? createdAt : null;
    const needsCheck = reference ? Date.now() - reference.valueOf() >= 30 * 60 * 1000 : true;
    const waiting = !hasLastSeen && !needsCheck;
    const detail = hasLastSeen
      ? `마지막 신호 ${shortDateTime(item.last_seen_at)}`
      : waiting ? "첫 신호 대기 중" : "등록 후 30분 이상 신호 없음";
    const status = needsCheck ? "점검 필요" : waiting ? "신호 대기" : "연결";
    const csiLabels = {
      unavailable: "CSI 신호 대기",
      calibrating: "CSI 기준선 교정 중",
      stable: "CSI 안정",
      motion: "CSI 움직임",
      strong_change: "CSI 강한 변동"
    };
    const hasModule = Boolean(item.received_at);
    const receivedAt = item.received_at ? new Date(item.received_at) : null;
    const liveTime = receivedAt && !Number.isNaN(receivedAt.valueOf())
      ? receivedAt.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })
      : "-";
    const temperature = item.has_ir_sensor
      ? `<div><span>주변 온도</span><strong>${item.ambient_c == null ? "-" : `${Number(item.ambient_c).toFixed(1)}℃`}</strong></div><div><span>표면 온도</span><strong>${item.object_c == null ? "-" : `${Number(item.object_c).toFixed(1)}℃`}</strong></div>`
      : '<div><span>온도 센서</span><strong>미장착</strong></div>';
    const moduleDetails = hasModule ? `<div class="sensor-values">
      <div><span>PIR</span><strong>${item.pir_state ? "움직임" : "대기"}</strong></div>
      <div><span>Wi-Fi CSI</span><strong>${escapeHTML(csiLabels[item.csi_status] || "신호 확인")}</strong></div>
      ${temperature}
      <div><span>CSI 수신률</span><strong>${Number(item.csi_packet_rate || 0).toFixed(1)} pkt/s</strong></div>
      <div class="sensor-live-value" data-sensor-peak-delta="${Number(item.csi_peak_delta || 0).toFixed(2)}">
        <span class="sensor-live-label"><i aria-hidden="true"></i>Peak Delta · LIVE</span>
        <strong>${Number(item.csi_peak_delta || 0).toFixed(2)}</strong>
        <small>1초 갱신 · ${escapeHTML(liveTime)}</small>
      </div>
    </div>` : '<div class="sensor-awaiting">ESP32 통합 측정값을 기다리고 있습니다.</div>';
    return `<article class="sensor-module-card ${needsCheck ? "needs-check" : ""}">
      <div class="sensor-module-head"><div><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.location)} · ${detail}</small></div><span class="pill ${needsCheck ? "danger" : ""}">${status}</span></div>
      ${moduleDetails}
      ${item.has_ir_sensor ? '<p class="sensor-caveat">표면 온도는 비접촉 참고값이며 체온 진단값이 아닙니다.</p>' : ''}
    </article>`;
  }).join("") : '<div class="empty-state">등록된 센서가 없습니다.</div>';
}

function renderEvents(items) {
  const labels = { pir_motion: "PIR 움직임", pir_idle: "PIR 대기 전환", csi_motion: "CSI 움직임", csi_fall: "CSI 강한 변화·PIR 동시 감지", heartbeat: "센서 상태 신호" };
  $("#eventList").innerHTML = items.length ? items.map(item => `<div class="timeline-item"><strong>${escapeHTML(labels[item.event_type] || item.event_type)} · ${escapeHTML(item.location)}</strong>${shortDateTime(item.occurred_at)}${item.confidence != null ? ` · 신뢰도 ${Math.round(item.confidence * 100)}%` : ""}</div>`).join("") : '<div class="empty-state">아직 수신한 센서 기록이 없습니다.</div>';
}

async function acknowledgeAlert(event) {
  const button = event.target.closest("[data-ack-alert]");
  if (!button) return;
  button.disabled = true;
  try { await api(`/alerts/${button.dataset.ackAlert}/ack`, { method: "POST" }); await loadDashboard(); }
  catch (error) { toast(error.message); button.disabled = false; }
}

async function submitRoutine(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    await api("/routines", { method: "POST", body: Object.fromEntries(form.entries()) });
    formElement.reset();
    $("#routineDialog").close();
    toast("반복 일정을 등록했습니다.");
    await loadDashboard();
  } catch (error) { toast(error.message); }
}

async function submitSensor(event) {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  try {
    const result = await api("/sensors", { method: "POST", body: Object.fromEntries(form.entries()) });
    const output = $("#sensorKeyResult");
    output.textContent = `DEVICE_UID=${result.device_uid}\nPIUDA_SENSOR_KEY=${result.api_key}\n\n이 키는 다시 표시되지 않습니다.`;
    output.classList.remove("hidden");
    toast("센서 키를 만들었습니다.");
    await loadDashboard();
  } catch (error) { toast(error.message); }
}

async function initDemo() {
  $$('[data-trigger-scenario]').forEach(button => button.addEventListener("click", triggerScenario));
  await loadDemoStatus(true);
  setInterval(() => {
    if (!document.hidden) loadDemoStatus();
  }, 2000);
  window.addEventListener("focus", () => loadDemoStatus());
}

async function loadDemoStatus(showError = false) {
  if (state.demoRefreshing) return;
  state.demoRefreshing = true;
  try {
    renderDemoStatus(await api("/demo/scenarios"));
  } catch (error) {
    if (showError) toast(error.message);
  } finally {
    state.demoRefreshing = false;
  }
}

function renderDemoStatus(result) {
  const active = result.active;
  const labels = { normal: "안심", caution: "살펴보기", danger: "주의", emergency: "긴급" };
  $("#demoActiveTitle").textContent = active.scenario_title;
  $("#demoActiveDescription").textContent = active.description;
  $("#demoRiskMetric").textContent = `${active.risk_score}점 · ${labels[active.risk_level] || active.risk_level}`;
  $("#demoTaskMetric").textContent = `${result.tasks.completed} / ${result.tasks.total}`;
  $("#demoAlertMetric").textContent = result.open_alerts;
  $("#demoActivatedAt").textContent = `실행 ${shortDateTime(active.activated_at)}`;
  $("#demoLive").className = `demo-live level-${active.risk_level}`;
  $$('[data-scenario-card]').forEach(card => {
    card.classList.toggle("active", card.dataset.scenarioCard === active.scenario_key);
  });
}

async function triggerScenario(event) {
  const button = event.currentTarget;
  const scenarioKey = button.dataset.triggerScenario;
  const originalText = button.textContent;
  $$('[data-trigger-scenario]').forEach(item => { item.disabled = true; });
  button.textContent = "장면 전환 중…";
  try {
    const result = await api(`/demo/scenarios/${scenarioKey}`, { method: "POST" });
    renderDemoStatus(result);
    toast(`${result.active.scenario_title} 장면을 실행했습니다.`);
    $("#demoLive").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    toast(error.message);
  } finally {
    $$('[data-trigger-scenario]').forEach(item => { item.disabled = false; });
    button.textContent = originalText;
  }
}

function isStandalone() {
  return window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
}

async function copyOrShare(url, title) {
  try {
    if (navigator.share) {
      await navigator.share({ title, text: "피우다 설치 주소", url });
      return;
    }
    await navigator.clipboard.writeText(url);
    toast("주소를 복사했습니다.");
  } catch (error) {
    if (error.name !== "AbortError") toast("주소를 길게 눌러 복사해 주세요.");
  }
}

function initInstall() {
  const origin = window.location.origin;
  const userUrl = `${origin}/`;
  const caregiverUrl = `${origin}/caregiver`;
  $("#userInstallUrl").textContent = userUrl;
  $("#caregiverInstallUrl").textContent = caregiverUrl;

  $$('[data-share]').forEach(button => button.addEventListener("click", () => {
    const caregiver = button.dataset.share === "caregiver";
    copyOrShare(caregiver ? caregiverUrl : userUrl, caregiver ? "피우다 보호자" : "피우다");
  }));

  const promptButton = $("[data-install-prompt]");
  if (installPromptEvent) promptButton?.classList.remove("hidden");
  promptButton?.addEventListener("click", async () => {
    if (!installPromptEvent) return;
    await installPromptEvent.prompt();
    await installPromptEvent.userChoice;
    installPromptEvent = null;
    promptButton.classList.add("hidden");
  });

  const status = $("#installStatus");
  if (isStandalone()) {
    status.textContent = "이미 홈 화면 앱으로 실행 중입니다.";
    status.classList.add("ready");
  } else if (/iPad|iPhone|iPod/.test(navigator.userAgent)) {
    status.textContent = "Safari 아래쪽 ‘⋯’ 또는 공유 버튼을 누른 뒤 ‘홈 화면에 추가’를 선택하세요.";
  } else {
    status.textContent = "아이폰에서 이 주소를 Safari로 열어 설치하세요.";
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  if (state.kiosk) document.body.classList.add("kiosk-mode");
  checkConnection();
  setInterval(checkConnection, 30000);
  if (document.body.dataset.page === "user") await initUser();
  if (document.body.dataset.page === "caregiver") await initCaregiver();
  if (document.body.dataset.page === "install") initInstall();
  if (document.body.dataset.page === "demo") await initDemo();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/service-worker.js", { updateViaCache: "none" })
      .then(registration => registration.update())
      .catch(() => {});
  }
});
