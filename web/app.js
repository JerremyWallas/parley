// --- State ---
let currentMode = localStorage.getItem("stt-mode") || "raw";
let serverUrl = localStorage.getItem("stt-server") || "";
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let audioContext = null;
let analyser = null;
let animationFrame = null;
let lastRawText = "";

// --- DOM ---
const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");
const resultArea = document.getElementById("resultArea");
const rawTextEl = document.getElementById("rawText");
const resultText = document.getElementById("resultText");
const resultMeta = document.getElementById("resultMeta");
const copyBtn = document.getElementById("copyBtn");
const saveCorrection = document.getElementById("saveCorrection");
const correctionStatus = document.getElementById("correctionStatus");
const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeSettings = document.getElementById("closeSettings");
const modeButtons = document.querySelectorAll(".mode-btn");
const historyList = document.getElementById("historyList");
const clearHistory = document.getElementById("clearHistory");
const canvas = document.getElementById("waveform");
const canvasCtx = canvas.getContext("2d");

// --- API ---
function apiUrl(path) {
  const base = serverUrl || window.location.origin;
  return base.replace(/\/$/, "") + path;
}

async function apiPost(path, body) {
  const resp = await fetch(apiUrl(path), { method: "POST", ...body });
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

async function apiGet(path) {
  const resp = await fetch(apiUrl(path));
  if (!resp.ok) throw new Error(`API error: ${resp.status}`);
  return resp.json();
}

// --- Mode selector ---
function setMode(mode) {
  currentMode = mode;
  localStorage.setItem("stt-mode", mode);
  modeButtons.forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

modeButtons.forEach(btn => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});
setMode(currentMode);

// --- Audio recording ---
async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });

    // Setup visualizer
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    drawWaveform();

    mediaRecorder = new MediaRecorder(stream, { mimeType: getSupportedMimeType() });
    audioChunks = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };

    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      cancelAnimationFrame(animationFrame);
      if (audioContext) {
        audioContext.close();
        audioContext = null;
      }
      clearCanvas();

      if (audioChunks.length === 0) return;
      const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
      await sendAudio(blob);
    };

    mediaRecorder.start();
    isRecording = true;
    recordBtn.classList.add("recording");
    recordBtn.querySelector(".label").textContent = "Aufnahme läuft...";
    statusEl.textContent = "";
  } catch (err) {
    statusEl.textContent = "Mikrofon-Zugriff verweigert";
    console.error("Recording error:", err);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    isRecording = false;
    recordBtn.classList.remove("recording");
    recordBtn.classList.add("processing");
    recordBtn.querySelector(".label").textContent = "Verarbeite...";
  }
}

function getSupportedMimeType() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  for (const type of types) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "audio/webm";
}

// --- Send audio to server ---
async function sendAudio(blob) {
  try {
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");
    formData.append("mode", currentMode);

    const result = await apiPost("/api/transcribe", { body: formData });

    lastRawText = result.raw_text;
    resultText.value = result.processed_text || result.raw_text;

    // Show raw text if mode is not raw and texts differ
    if (currentMode !== "raw" && result.raw_text !== result.processed_text) {
      rawTextEl.classList.remove("hidden");
      rawTextEl.querySelector("p").textContent = result.raw_text;
    } else {
      rawTextEl.classList.add("hidden");
    }

    resultMeta.textContent = `${result.language?.toUpperCase()} · ${result.duration_ms}ms · ${currentMode}`;
    resultArea.classList.remove("hidden");
    correctionStatus.textContent = "";

    // Auto-copy to clipboard
    await copyToClipboard(result.processed_text || result.raw_text);

    // Save to history
    addToHistory(result);

    statusEl.textContent = "In Zwischenablage kopiert";
  } catch (err) {
    statusEl.textContent = "Fehler: " + err.message;
    console.error("Send error:", err);
  } finally {
    recordBtn.classList.remove("processing");
    recordBtn.querySelector(".label").textContent = "Halten zum Sprechen";
  }
}

// --- Clipboard ---
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    return true;
  }
}

copyBtn.addEventListener("click", async () => {
  await copyToClipboard(resultText.value);
  statusEl.textContent = "In Zwischenablage kopiert";
});

// --- Correction feedback ---
saveCorrection.addEventListener("click", async () => {
  const original = lastRawText;
  const corrected = resultText.value.trim();
  if (!original || !corrected || original === corrected) {
    correctionStatus.textContent = "Keine Änderung erkannt";
    return;
  }
  try {
    await apiPost("/api/correction", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ original, corrected }),
    });
    correctionStatus.textContent = "Korrektur gespeichert!";
  } catch (err) {
    correctionStatus.textContent = "Fehler: " + err.message;
  }
});

// --- Record button events (mouse + touch) ---
recordBtn.addEventListener("mousedown", (e) => { e.preventDefault(); startRecording(); });
recordBtn.addEventListener("mouseup", (e) => { e.preventDefault(); stopRecording(); });
recordBtn.addEventListener("mouseleave", () => { if (isRecording) stopRecording(); });

recordBtn.addEventListener("touchstart", (e) => { e.preventDefault(); startRecording(); });
recordBtn.addEventListener("touchend", (e) => { e.preventDefault(); stopRecording(); });
recordBtn.addEventListener("touchcancel", () => { if (isRecording) stopRecording(); });

// Prevent context menu on long press (mobile)
recordBtn.addEventListener("contextmenu", (e) => e.preventDefault());

// --- Waveform visualizer ---
function drawWaveform() {
  if (!analyser) return;
  animationFrame = requestAnimationFrame(drawWaveform);

  const bufferLength = analyser.frequencyBinCount;
  const dataArray = new Uint8Array(bufferLength);
  analyser.getByteFrequencyData(dataArray);

  const width = canvas.width;
  const height = canvas.height;
  const centerX = width / 2;
  const centerY = height / 2;
  const radius = 70;

  canvasCtx.clearRect(0, 0, width, height);

  // Draw circular waveform
  const bars = 64;
  const step = Math.floor(bufferLength / bars);

  for (let i = 0; i < bars; i++) {
    const value = dataArray[i * step] / 255;
    const barHeight = value * 30 + 2;
    const angle = (i / bars) * Math.PI * 2 - Math.PI / 2;

    const x1 = centerX + Math.cos(angle) * radius;
    const y1 = centerY + Math.sin(angle) * radius;
    const x2 = centerX + Math.cos(angle) * (radius + barHeight);
    const y2 = centerY + Math.sin(angle) * (radius + barHeight);

    canvasCtx.beginPath();
    canvasCtx.moveTo(x1, y1);
    canvasCtx.lineTo(x2, y2);
    canvasCtx.strokeStyle = `rgba(239, 68, 68, ${0.4 + value * 0.6})`;
    canvasCtx.lineWidth = 2.5;
    canvasCtx.lineCap = "round";
    canvasCtx.stroke();
  }
}

function clearCanvas() {
  canvasCtx.clearRect(0, 0, canvas.width, canvas.height);
}

// --- History ---
function getHistory() {
  try {
    return JSON.parse(localStorage.getItem("stt-history") || "[]");
  } catch { return []; }
}

function addToHistory(result) {
  const history = getHistory();
  history.unshift({
    text: result.processed_text || result.raw_text,
    raw: result.raw_text,
    mode: result.mode,
    language: result.language,
    time: new Date().toISOString(),
  });
  // Keep last 50
  if (history.length > 50) history.length = 50;
  localStorage.setItem("stt-history", JSON.stringify(history));
  renderHistory();
}

function renderHistory() {
  const history = getHistory();
  historyList.innerHTML = "";
  if (history.length === 0) {
    historyList.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Noch keine Einträge</p>';
    return;
  }
  for (const item of history.slice(0, 20)) {
    const el = document.createElement("div");
    el.className = "history-item";
    const time = new Date(item.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `
      <span class="time">${time}<span class="mode-tag">${item.mode}</span></span>
      <div class="text">${escapeHtml(item.text)}</div>
    `;
    el.addEventListener("click", async () => {
      await copyToClipboard(item.text);
      statusEl.textContent = "Aus Verlauf kopiert";
    });
    historyList.appendChild(el);
  }
}

clearHistory.addEventListener("click", () => {
  localStorage.removeItem("stt-history");
  renderHistory();
});

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

// --- Settings ---
settingsBtn.addEventListener("click", () => {
  settingsModal.classList.remove("hidden");
  loadSettings();
});

closeSettings.addEventListener("click", () => {
  settingsModal.classList.add("hidden");
});

settingsModal.addEventListener("click", (e) => {
  if (e.target === settingsModal) settingsModal.classList.add("hidden");
});

async function loadSettings() {
  // Server URL
  document.getElementById("serverUrl").value = serverUrl;

  // Glossary
  try {
    const data = await apiGet("/api/glossary");
    renderGlossary(data.words || []);
  } catch { renderGlossary([]); }

  // Language stats
  try {
    const data = await apiGet("/api/health");
    const stats = data.language_stats || {};
    const percents = stats.percentages || {};
    const entries = Object.entries(percents).sort((a, b) => b[1] - a[1]);
    document.getElementById("langStats").innerHTML = entries.length
      ? entries.map(([lang, pct]) => `${lang.toUpperCase()}: ${pct}%`).join(" · ")
      : "Noch keine Daten";
  } catch {
    document.getElementById("langStats").textContent = "Server nicht erreichbar";
  }
}

function renderGlossary(words) {
  const list = document.getElementById("glossaryList");
  list.innerHTML = "";
  for (const word of words) {
    const tag = document.createElement("span");
    tag.className = "glossary-tag";
    tag.innerHTML = `${escapeHtml(word)} <span class="remove" data-word="${escapeHtml(word)}">✕</span>`;
    tag.querySelector(".remove").addEventListener("click", async () => {
      try {
        await fetch(apiUrl("/api/glossary"), {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ word }),
        });
        loadSettings();
      } catch (err) { console.error(err); }
    });
    list.appendChild(tag);
  }
}

document.getElementById("addGlossaryBtn").addEventListener("click", async () => {
  const input = document.getElementById("glossaryInput");
  const word = input.value.trim();
  if (!word) return;
  try {
    await apiPost("/api/glossary", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word }),
    });
    input.value = "";
    loadSettings();
  } catch (err) { console.error(err); }
});

document.getElementById("glossaryInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter") document.getElementById("addGlossaryBtn").click();
});

// Server URL
document.getElementById("saveServerUrl").addEventListener("click", () => {
  serverUrl = document.getElementById("serverUrl").value.trim();
  localStorage.setItem("stt-server", serverUrl);
  checkServer();
});

async function checkServer() {
  const el = document.getElementById("serverStatus");
  try {
    const data = await apiGet("/api/health");
    el.innerHTML = `<span style="color:var(--success)">Verbunden</span> · GPU: ${data.gpu} · Whisper: ${data.whisper_model}`;
  } catch {
    el.innerHTML = '<span style="color:var(--recording)">Nicht erreichbar</span>';
  }
}

// --- Init ---
renderHistory();

// Register service worker
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
