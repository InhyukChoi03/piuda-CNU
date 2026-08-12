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
  demoRefreshing: false,
  lastAlertId: null,
  alertAudioContext: null,
  userName: "사용자",
  wellnessActivation: "",
  wellnessTimer: null,
  call: {
    id: "",
    role: "",
    peer: null,
    stream: null,
    pollTimer: null,
    lastSignalId: 0,
    pendingIce: [],
    phase: "idle",
    polling: false,
    cleaning: false,
    pollFailures: 0,
    answerTimer: null,
    ringingTimer: null,
    disconnectTimer: null
  }
};
let installPromptEvent = null;
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];

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
  const response = await fetch(`/api/v1${path}`, { cache: "no-store", ...options, headers });
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
  $("#caregiverCallButton").addEventListener("click", callCaregiver);
  $$('[data-wellness-response]').forEach(button => button.addEventListener("click", () => respondWellness(button.dataset.wellnessResponse)));
  $("#userCallEnd").addEventListener("click", () => endCall("ended"));
  $("#userCallDialog").addEventListener("cancel", event => event.preventDefault());
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

async function microphoneStream(role) {
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    throw new Error("음성 통화는 보안 통화 화면에서 사용할 수 있어요.");
  }
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      video: false
    });
  } catch (error) {
    const messages = {
      NotAllowedError: "마이크 권한이 꺼져 있어요. 브라우저 설정에서 피우다의 마이크를 허용해 주세요.",
      NotFoundError: role === "user"
        ? "사용자 기기의 마이크를 찾지 못했어요. Raspberry Pi에 USB 마이크나 헤드셋을 연결해 주세요."
        : "보호자 기기의 마이크를 찾지 못했어요. 기기의 마이크 설정을 확인해 주세요.",
      NotReadableError: "마이크를 다른 앱이 사용 중이에요. 다른 앱을 닫고 다시 시도해 주세요.",
      AbortError: "마이크 연결이 중단됐어요. 잠시 후 다시 시도해 주세요."
    };
    throw new Error(messages[error?.name] || "마이크를 열지 못했어요. 연결과 권한을 확인해 주세요.");
  }
}

function emptyCallState() {
  return {
    id: "", role: "", peer: null, stream: null, pollTimer: null,
    lastSignalId: 0, pendingIce: [], phase: "idle", polling: false,
    cleaning: false, pollFailures: 0, answerTimer: null, ringingTimer: null,
    disconnectTimer: null
  };
}

function callDialogFor(role) {
  return role === "user" ? $("#userCallDialog") : $("#caregiverCallDialog");
}

function callAudioFor(role) {
  return role === "user" ? $("#userRemoteAudio") : $("#caregiverRemoteAudio");
}

async function sendCallSignal(kind, signal, callId = state.call.id, role = state.call.role) {
  if (!callId || !role) return;
  await api(`/calls/${callId}/signals`, {
    method: "POST",
    body: { sender: role, kind, signal }
  });
}

function cleanupCallOnPageHide() {
  const current = state.call;
  // 수신 벨만 보던 보호자 탭이 닫혀도 사용자의 요청은 유지합니다.
  // 실제 로컬 peer를 만든 탭만 자신의 통화를 종료합니다.
  if (!current.id || !current.peer || current.cleaning) return;
  const callId = current.id;
  const headers = { Accept: "application/json", "Content-Type": "application/json" };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  fetch(`/api/v1/calls/${encodeURIComponent(callId)}/status`, {
    method: "POST",
    headers,
    body: JSON.stringify({ status: "ended" }),
    cache: "no-store",
    keepalive: true
  }).catch(() => {});
  // pagehide는 BFCache 진입에서도 발생하므로 반드시 표준 초기화를
  // 거쳐, 뒤로가기로 페이지가 복원되어도 다음 통화를 바로 시작할 수 있게 합니다.
  finishCallLocally("", callId);
}

window.addEventListener("pagehide", cleanupCallOnPageHide);

async function sendIceWithRetry(signal, callId, role, peer) {
  const retryDelays = [0, 250, 750];
  let lastError = null;
  for (const delay of retryDelays) {
    if (!callStillCurrent(callId, role, peer)) return;
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    if (!callStillCurrent(callId, role, peer)) return;
    try {
      await sendCallSignal("ice", signal, callId, role);
      return;
    } catch (error) {
      lastError = error;
      // 잘못된 페이로드나 이미 종료된 통화는 다시 보내도 회복되지 않습니다.
      if (error.status >= 400 && error.status < 500) break;
    }
  }
  if (callStillCurrent(callId, role, peer)) throw lastError || new Error("ICE 전송 실패");
}

function makePeer(role, stream, callId) {
  const peer = new RTCPeerConnection({ iceServers: [] });
  stream.getTracks().forEach(track => peer.addTrack(track, stream));
  peer.onicecandidate = event => {
    if (event.candidate && state.call.id === callId && state.call.peer === peer) {
      sendIceWithRetry(event.candidate.toJSON(), callId, role, peer).catch(() => {
        if (callStillCurrent(callId, role, peer)) {
          terminateCall("ended", "통화 연결 정보를 보내지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.", callId);
        }
      });
    }
  };
  peer.ontrack = event => {
    const audio = callAudioFor(role);
    if (!audio) return;
    audio.srcObject = event.streams[0];
    audio.play().catch(() => {});
  };
  peer.onconnectionstatechange = () => {
    if (state.call.id !== callId || state.call.peer !== peer || state.call.cleaning) return;
    if (peer.connectionState === "connected") {
      clearTimeout(state.call.disconnectTimer);
      state.call.disconnectTimer = null;
      setCallConnected(role);
    }
    if (peer.connectionState === "failed") {
      terminateCall("ended", "통화 연결에 실패했어요. 다시 시도해 주세요.");
    }
    if (peer.connectionState === "disconnected" && !state.call.disconnectTimer) {
      state.call.disconnectTimer = setTimeout(() => {
        if (state.call.id === callId && peer.connectionState === "disconnected") {
          terminateCall("ended", "통화 연결이 끊어졌어요.");
        }
      }, 5000);
    }
  };
  return peer;
}

function setCallConnected(role) {
  state.call.phase = "active";
  clearTimeout(state.call.answerTimer);
  clearTimeout(state.call.ringingTimer);
  state.call.answerTimer = null;
  state.call.ringingTimer = null;
  if (role === "user") {
    $("#userCallTitle").textContent = "보호자와 연결됐어요";
    $("#userCallStatus").textContent = "통화 중입니다.";
  } else {
    $("#caregiverCallTitle").textContent = `${state.userName}님과 통화 중`;
    $("#caregiverCallStatus").textContent = "통화 중입니다.";
    $("#caregiverCallEnd").classList.remove("hidden");
  }
  callDialogFor(role)?.classList.add("connected");
}

function callStillCurrent(callId, role, peer = null) {
  return state.call.id === callId
    && state.call.role === role
    && (!peer || state.call.peer === peer);
}

function fatalCallSignal(message, cause = null) {
  const error = new Error(message);
  error.callSignalFatal = true;
  if (cause) error.cause = cause;
  return error;
}

function remoteDescriptionFor(item, expectedKind, expectedSender) {
  if (item.sender !== expectedSender
      || item.payload?.type !== expectedKind
      || typeof item.payload?.sdp !== "string"
      || !item.payload.sdp.trim()) {
    throw fatalCallSignal("통화 연결 정보가 올바르지 않아 통화를 종료했어요. 다시 시도해 주세요.");
  }
  return item.payload;
}

async function setRemoteCallDescription(peer, description) {
  try {
    await peer.setRemoteDescription(description);
  } catch (error) {
    throw fatalCallSignal("통화 연결 정보를 적용하지 못해 통화를 종료했어요. 다시 시도해 주세요.", error);
  }
}

async function flushPendingIce(peer, callId, role) {
  if (!peer?.remoteDescription || !callStillCurrent(callId, role, peer)) return;
  const pending = state.call.pendingIce.splice(0);
  for (const candidate of pending) {
    if (!callStillCurrent(callId, role, peer)) return;
    await state.call.peer.addIceCandidate(candidate).catch(() => {});
  }
}

async function handleCallSignal(item, callId, role) {
  const peer = state.call.peer;
  if (!peer || !callStillCurrent(callId, role, peer)) return;
  if (item.kind === "ice") {
    if (peer.remoteDescription) await peer.addIceCandidate(item.payload).catch(() => {});
    else if (callStillCurrent(callId, role, peer)) state.call.pendingIce.push(item.payload);
    return;
  }
  if (item.kind === "offer" && role === "caregiver") {
    const offer = remoteDescriptionFor(item, "offer", "user");
    if (!peer.remoteDescription) {
      await setRemoteCallDescription(peer, offer);
      if (!callStillCurrent(callId, role, peer)) return;
      await flushPendingIce(peer, callId, role);
    } else if (peer.remoteDescription.type !== "offer") {
      throw fatalCallSignal("통화 요청 상태가 올바르지 않아 통화를 종료했어요.");
    }
    if (!callStillCurrent(callId, role, peer)) return;
    if (!peer.localDescription) {
      try {
        const answer = await peer.createAnswer();
        await peer.setLocalDescription(answer);
      } catch (error) {
        throw fatalCallSignal("통화 응답 정보를 만들지 못해 통화를 종료했어요.", error);
      }
    }
    if (!callStillCurrent(callId, role, peer)) return;
    if (peer.localDescription?.type !== "answer") {
      throw fatalCallSignal("통화 응답 상태가 올바르지 않아 통화를 종료했어요.");
    }
    // answer 전송이나 active 전환의 응답을 잃어도 같은 offer를
    // 다시 처리해 두 요청을 멱등적으로 재시도합니다.
    const answerPayload = typeof peer.localDescription.toJSON === "function"
      ? peer.localDescription.toJSON()
      : { type: peer.localDescription.type, sdp: peer.localDescription.sdp };
    await sendCallSignal("answer", answerPayload, callId, role);
    if (!callStillCurrent(callId, role, peer)) return;
    await api(`/calls/${callId}/status`, { method: "POST", body: { status: "active" } });
    return;
  }
  if (item.kind === "answer" && role === "user") {
    const answer = remoteDescriptionFor(item, "answer", "caregiver");
    if (!peer.remoteDescription) {
      await setRemoteCallDescription(peer, answer);
      if (!callStillCurrent(callId, role, peer)) return;
      await flushPendingIce(peer, callId, role);
    } else if (peer.remoteDescription.type !== "answer") {
      throw fatalCallSignal("통화 응답 상태가 올바르지 않아 통화를 종료했어요.");
    }
  }
}

async function pollCallSignals() {
  if (!state.call.id || !state.call.role || state.call.polling || state.call.cleaning) return;
  const callId = state.call.id;
  const role = state.call.role;
  state.call.polling = true;
  try {
    const result = await api(`/calls/${callId}/signals?after=${state.call.lastSignalId}&recipient=${role}`);
    if (state.call.id !== callId || state.call.role !== role) return;
    if (!result.call || result.call.id !== callId) {
      await terminateCall("ended", "통화 정보를 확인하지 못했어요. 다시 시도해 주세요.", callId);
      return;
    }
    if (!["ringing", "active"].includes(result.call.status)) {
      finishCallLocally("통화가 종료되었어요.", callId);
      return;
    }
    for (const item of result.items) {
      await handleCallSignal(item, callId, role);
      if (!callStillCurrent(callId, role)) return;
      state.call.lastSignalId = Math.max(state.call.lastSignalId, Number(item.id) || 0);
    }
    state.call.pollFailures = 0;
  } catch (error) {
    if (!callStillCurrent(callId, role)) return;
    state.call.pollFailures += 1;
    if (error.callSignalFatal) {
      await terminateCall("ended", error.message, callId);
    } else if (error.status === 404 || error.status === 409) {
      finishCallLocally("통화가 종료되었어요.", callId);
    } else if (state.call.pollFailures === 5) {
      toast("통화 연결이 불안정해요. 네트워크를 확인하고 있어요.");
    }
  } finally {
    if (callStillCurrent(callId, role)) {
      state.call.polling = false;
      if (state.call.phase !== "idle" && state.call.phase !== "ending") {
        clearTimeout(state.call.pollTimer);
        state.call.pollTimer = setTimeout(pollCallSignals, 600);
      }
    }
  }
}

function beginCallPolling() {
  clearTimeout(state.call.pollTimer);
  pollCallSignals();
}

function finishCallLocally(message = "", expectedCallId = "") {
  if (expectedCallId && state.call.id !== expectedCallId) return;
  const current = state.call;
  if (current.cleaning) return;
  current.cleaning = true;
  clearTimeout(current.pollTimer);
  clearTimeout(current.answerTimer);
  clearTimeout(current.ringingTimer);
  clearTimeout(current.disconnectTimer);
  if (current.peer) {
    current.peer.onicecandidate = null;
    current.peer.ontrack = null;
    current.peer.onconnectionstatechange = null;
    current.peer.close();
  }
  current.stream?.getTracks().forEach(track => track.stop());
  ["#userRemoteAudio", "#caregiverRemoteAudio"].forEach(selector => {
    const audio = $(selector);
    if (audio) audio.srcObject = null;
  });
  ["#userCallDialog", "#caregiverCallDialog"].forEach(selector => {
    const dialog = $(selector);
    if (dialog?.open) dialog.close();
    dialog?.classList.remove("connected");
  });
  $("#incomingCallActions")?.classList.remove("hidden");
  $("#caregiverCallEnd")?.classList.add("hidden");
  const answerButton = $("#caregiverCallAnswer");
  if (answerButton) {
    answerButton.disabled = false;
    answerButton.textContent = "통화 받기";
  }
  const declineButton = $("#caregiverCallDecline");
  if (declineButton) declineButton.disabled = false;
  state.call = emptyCallState();
  const userButton = $("#caregiverCallButton");
  if (userButton) {
    userButton.disabled = false;
    userButton.textContent = "보호자와 통화";
  }
  if (message) toast(message);
}

async function terminateCall(status = "ended", message = "", expectedCallId = state.call.id) {
  const callId = expectedCallId;
  if (callId && state.call.id !== callId) return;
  if (state.call.phase === "ending") return;
  state.call.phase = "ending";
  if (callId) {
    try { await api(`/calls/${callId}/status`, { method: "POST", body: { status } }); }
    catch { /* local cleanup still applies */ }
  }
  finishCallLocally(
    message || (status === "declined" ? "통화를 거절했어요." : "통화를 종료했어요."),
    callId
  );
}

async function endCall(status = "ended") {
  await terminateCall(status);
}

async function callCaregiver() {
  const button = $("#caregiverCallButton");
  if (!button || state.call.phase !== "idle") return;
  let acquiredStream = null;
  let createdCallId = "";
  state.call.phase = "acquiring";
  button.disabled = true;
  button.textContent = "마이크 준비 중…";
  try {
    acquiredStream = await microphoneStream("user");
    const result = await api("/caregiver-call", { method: "POST", body: { replace: true } });
    createdCallId = result.call.id;
    state.call.id = result.call.id;
    state.call.role = "user";
    state.call.stream = acquiredStream;
    state.call.phase = "connecting";
    state.call.peer = makePeer("user", acquiredStream, state.call.id);
    acquiredStream = null;
    const offer = await state.call.peer.createOffer();
    await state.call.peer.setLocalDescription(offer);
    await sendCallSignal("offer", state.call.peer.localDescription.toJSON());
    $("#userCallTitle").textContent = "보호자를 부르고 있어요";
    $("#userCallStatus").textContent = "스마트폰에서 통화를 받을 때까지 잠시 기다려 주세요.";
    $("#userCallDialog").showModal();
    button.textContent = "통화 요청 중";
    state.call.phase = "ringing";
    state.call.ringingTimer = setTimeout(() => {
      if (callStillCurrent(createdCallId, "user") && state.call.phase === "ringing") {
        terminateCall("missed", "보호자가 응답하지 않아 통화 요청을 마쳤어요. 잠시 후 다시 시도해 주세요.", createdCallId);
      }
    }, 120000);
    beginCallPolling();
    speak("보호자에게 통화를 요청했어요.");
  } catch (error) {
    acquiredStream?.getTracks().forEach(track => track.stop());
    const failedCallId = state.call.id || createdCallId;
    if (failedCallId) {
      try { await api(`/calls/${failedCallId}/status`, { method: "POST", body: { status: "ended" } }); }
      catch { /* the local UI must still recover */ }
    }
    finishCallLocally();
    toast(error.message);
  }
}

function showIncomingCall(call, userName) {
  if (!call || state.call.role === "user") return;
  state.userName = userName || "사용자";
  if (state.call.id && state.call.id !== call.id) finishCallLocally();
  if (!state.call.id) {
    state.call.id = call.id;
    state.call.role = "caregiver";
    state.call.phase = "ringing";
    $("#caregiverCallTitle").textContent = `${state.userName}님이 부르고 있어요`;
    $("#caregiverCallStatus").textContent = "통화 받기를 눌러 연결하세요.";
    playAlertTone();
  }
  if (state.call.id === call.id
      && state.call.phase !== "ending"
      && !$("#caregiverCallDialog").open) {
    $("#caregiverCallDialog").showModal();
  }
}

async function answerCaregiverCall() {
  if (!state.call.id || state.call.phase !== "ringing") return;
  const callId = state.call.id;
  let acquiredStream = null;
  state.call.phase = "answering";
  const button = $("#caregiverCallAnswer");
  button.disabled = true;
  button.textContent = "마이크 연결 중…";
  try {
    acquiredStream = await microphoneStream("caregiver");
    if (!callStillCurrent(callId, "caregiver")) {
      acquiredStream.getTracks().forEach(track => track.stop());
      return;
    }
    state.call.stream = acquiredStream;
    state.call.peer = makePeer("caregiver", acquiredStream, callId);
    acquiredStream = null;
    const peer = state.call.peer;
    state.call.phase = "connecting";
    $("#incomingCallActions").classList.add("hidden");
    $("#caregiverCallEnd").classList.remove("hidden");
    $("#caregiverCallStatus").textContent = "사용자 화면과 연결하고 있어요.";
    state.call.answerTimer = setTimeout(() => {
      if (callStillCurrent(callId, "caregiver", peer) && state.call.phase === "connecting") {
        terminateCall("ended", "통화 연결 정보가 도착하지 않았어요. 사용자 화면에서 다시 통화를 눌러 주세요.", callId);
      }
    }, 12000);
    beginCallPolling();
  } catch (error) {
    acquiredStream?.getTracks().forEach(track => track.stop());
    if (!callStillCurrent(callId, "caregiver")) return;
    state.call.peer?.close();
    state.call.stream?.getTracks().forEach(track => track.stop());
    state.call.peer = null;
    state.call.stream = null;
    toast(error.message);
    state.call.phase = "ringing";
    button.disabled = false;
    button.textContent = "통화 받기";
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
  $("#caregiverCallAnswer").addEventListener("click", answerCaregiverCall);
  $("#caregiverCallDecline").addEventListener("click", () => endCall("declined"));
  $("#caregiverCallEnd").addEventListener("click", () => endCall("ended"));
  $("#caregiverCallDialog").addEventListener("cancel", event => event.preventDefault());
  $("#routineForm").addEventListener("submit", submitRoutine);
  $("#sensorForm").addEventListener("submit", submitSensor);
  $$('[data-dialog]').forEach(button => button.addEventListener("click", () => $("#" + button.dataset.dialog).showModal()));
  $$('[data-close]').forEach(button => button.addEventListener("click", () => button.closest("dialog").close()));
  $("#alertList").addEventListener("click", acknowledgeAlert);
  document.addEventListener("pointerdown", prepareAlertAudio, { once: true });
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    $("#callSecurityNotice").classList.remove("hidden");
    $("#secureCallLink").href = `https://${window.location.hostname}:8443/caregiver`;
  }
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
  if (state.call.id) await endCall("ended");
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
    const [result, sensors] = await Promise.all([api("/dashboard"), api("/sensors")]);
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
    renderSensors(sensors.items);
    renderEvents(result.sensor_events);
    notifyNewCaregiverAlert(result.alerts);
    if (result.call?.status === "ringing" && result.call.offer_ready) {
      showIncomingCall(result.call, result.profile?.user_name);
    } else if (result.call?.status === "active") {
      const isThisTabsCall = state.call.role === "caregiver"
        && state.call.id === result.call.id
        && Boolean(state.call.peer);
      if (!isThisTabsCall && state.call.role === "caregiver" && state.call.id) {
        finishCallLocally("다른 기기에서 통화를 받았어요.", state.call.id);
      }
    } else if (state.call.role === "caregiver" && state.call.id) {
      finishCallLocally("통화 요청이 종료되었어요.", state.call.id);
    }
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
  if (state.lastAlertId === null) {
    state.lastAlertId = newestId;
    return;
  }
  const fresh = alerts
    .filter(item => !item.acknowledged_at && item.title !== "보호자 통화 요청" && Number(item.id) > state.lastAlertId)
    .sort((left, right) => Number(right.id) - Number(left.id))[0];
  state.lastAlertId = Math.max(state.lastAlertId, newestId);
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
    return `<div class="compact-item"><div><strong>${escapeHTML(item.name)}</strong><small>${escapeHTML(item.location)} · ${detail}</small></div><span class="pill ${needsCheck ? "danger" : ""}">${status}</span></div>`;
  }).join("") : '<div class="empty-state">등록된 센서가 없습니다.</div>';
}

function renderEvents(items) {
  const labels = { pir_motion: "PIR 움직임", pir_idle: "PIR 무활동", csi_motion: "CSI 움직임", csi_fall: "CSI 낙상 의심", heartbeat: "센서 상태 신호" };
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
  const caregiverUrl = `https://${window.location.hostname}:8443/caregiver`;
  $("#userInstallUrl").textContent = userUrl;
  $("#caregiverInstallUrl").textContent = caregiverUrl;
  $("#secureCallLink").href = caregiverUrl;

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
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/service-worker.js").catch(() => {});
});
