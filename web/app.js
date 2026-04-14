// --- State ---
let currentMode = localStorage.getItem("stt-mode") || "raw";
let serverUrl = localStorage.getItem("stt-server") || "";
let mediaRecorder = null;
let isRecording = false;
let audioContext = null;
let analyser = null;
let animationFrame = null;
let lastRawText = "";
let ws = null;

// --- DOM ---
const recordBtn = document.getElementById("recordBtn");
const statusEl = document.getElementById("status");
const resultArea = document.getElementById("resultArea");
const rawTextEl = document.getElementById("rawText");
const resultText = document.getElementById("resultText");
const resultMeta = document.getElementById("resultMeta");
const copyRawBtn = document.getElementById("copyRawBtn");
const copyResultBtn = document.getElementById("copyResultBtn");
const saveCorrection = document.getElementById("saveCorrection");
const correctionStatus = document.getElementById("correctionStatus");
const settingsBtn = document.getElementById("settingsBtn");
const settingsModal = document.getElementById("settingsModal");
const closeSettings = document.getElementById("closeSettings");
const historyList = document.getElementById("historyList");
const clearHistory = document.getElementById("clearHistory");
const canvas = document.getElementById("waveform");
const canvasCtx = canvas.getContext("2d");

// --- API ---
function apiUrl(path) {
  const base = serverUrl || window.location.origin;
  return base.replace(/\/$/, "") + path;
}

function wsUrl(path) {
  const base = serverUrl || window.location.origin;
  return base.replace(/\/$/, "").replace(/^http/, "ws") + path;
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

// --- Mode selector (dynamic from presets) ---
function setMode(mode) {
  currentMode = mode;
  localStorage.setItem("stt-mode", mode);
  document.querySelectorAll(".mode-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
}

// Fixed mode buttons
const modeButtons = document.querySelectorAll(".mode-btn");
modeButtons.forEach(btn => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});
setMode(currentMode);

// --- Tab switching ---
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
  });
});

// --- WebSocket streaming ---
function getOpusMimeType() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  for (const type of types) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return "audio/webm";
}

async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
    });

    // Setup visualizer
    audioContext = new AudioContext();
    const source = audioContext.createMediaStreamSource(stream);
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 256;
    source.connect(analyser);
    drawWaveform();

    // Open WebSocket connection
    ws = new WebSocket(wsUrl("/ws/transcribe"));
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      // Start recording with Opus compression, send chunks every 500ms
      mediaRecorder = new MediaRecorder(stream, {
        mimeType: getOpusMimeType(),
        audioBitsPerSecond: 16000,
      });

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0 && ws && ws.readyState === WebSocket.OPEN) {
          e.data.arrayBuffer().then(buf => ws.send(buf));
        }
      };

      mediaRecorder.onstop = () => {
        stream.getTracks().forEach(t => t.stop());
        cancelAnimationFrame(animationFrame);
        if (audioContext) {
          audioContext.close();
          audioContext = null;
        }
        clearCanvas();
      };

      // Collect chunks every 500ms for streaming upload
      mediaRecorder.start(500);
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleStreamMessage(data);
    };

    ws.onerror = () => {
      statusEl.textContent = "WebSocket-Verbindung fehlgeschlagen, nutze Fallback...";
      // Fallback: if WebSocket fails, fall back to REST
      if (mediaRecorder && mediaRecorder.state === "recording") {
        fallbackToRest(stream);
      }
    };

    ws.onclose = () => {
      ws = null;
    };

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
  if (!isRecording) return;
  isRecording = false;
  recordBtn.classList.remove("recording");
  recordBtn.classList.add("processing");
  recordBtn.querySelector(".label").textContent = "Verarbeite...";

  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
  }

  // Send stop signal via WebSocket
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "stop", mode: currentMode, preset: currentMode }));
  }
}

// --- REST fallback for when WebSocket is unavailable ---
function fallbackToRest(stream) {
  const fallbackRecorder = new MediaRecorder(stream, { mimeType: getOpusMimeType() });
  const chunks = [];
  fallbackRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data); };
  fallbackRecorder.onstop = async () => {
    stream.getTracks().forEach(t => t.stop());
    if (chunks.length === 0) return;
    const blob = new Blob(chunks, { type: fallbackRecorder.mimeType });
    await sendAudioRest(blob);
  };
  fallbackRecorder.start();
  // Stop after current recording ends
  setTimeout(() => {
    if (fallbackRecorder.state === "recording") fallbackRecorder.stop();
  }, 100);
}

async function sendAudioRest(blob) {
  try {
    const formData = new FormData();
    formData.append("audio", blob, "recording.webm");
    formData.append("mode", currentMode);
    const result = await apiPost("/api/transcribe", { body: formData });
    showFinalResult(result.raw_text, result.processed_text || result.raw_text, result.language, result.duration_ms);
  } catch (err) {
    statusEl.textContent = "Fehler: " + err.message;
  } finally {
    recordBtn.classList.remove("processing");
    recordBtn.querySelector(".label").textContent = "Halten zum Sprechen";
  }
}

// --- Handle streaming messages from WebSocket ---
let streamingSegments = [];
let streamingLLMText = "";

function handleStreamMessage(data) {
  switch (data.type) {
    case "segment":
      // Whisper segment arrived — show immediately
      streamingSegments.push(data.text);
      rawTextEl.classList.remove("hidden");
      rawTextEl.querySelector("p").textContent = streamingSegments.join(" ");
      resultArea.classList.remove("hidden");
      statusEl.textContent = "Transkribiere...";
      break;

    case "transcription_done":
      lastRawText = data.raw_text;
      rawTextEl.querySelector("p").textContent = data.raw_text;
      resultMeta.textContent = `${data.language?.toUpperCase()} · ${data.duration_ms}ms · ${currentMode}`;

      if (currentMode === "raw") {
        resultText.value = data.raw_text;
        rawTextEl.classList.add("hidden");
      } else {
        // Show raw text, clear result for incoming LLM stream
        resultText.value = "";
        streamingLLMText = "";
        statusEl.textContent = "Formuliere um...";
      }
      break;

    case "llm_token":
      // LLM token arrived — append to result
      streamingLLMText += data.token;
      resultText.value = streamingLLMText;
      // Auto-scroll textarea to bottom
      resultText.scrollTop = resultText.scrollHeight;
      break;

    case "llm_done":
      resultText.value = data.processed_text || lastRawText;
      correctionStatus.textContent = "";
      resultArea.classList.remove("hidden");

      // Hide raw text section if mode is raw or texts are identical
      if (currentMode === "raw" || data.processed_text === lastRawText) {
        rawTextEl.classList.add("hidden");
      }

      // Auto-copy final result to clipboard
      copyToClipboard(data.processed_text || lastRawText);
      statusEl.textContent = "In Zwischenablage kopiert";

      // Save to server history
      saveToHistory({
        raw_text: lastRawText,
        processed_text: data.processed_text || lastRawText,
        mode: currentMode,
        language: resultMeta.textContent.split(" · ")[0],
      });

      // Reset state
      streamingSegments = [];
      streamingLLMText = "";
      recordBtn.classList.remove("processing");
      recordBtn.querySelector(".label").textContent = "Halten zum Sprechen";

      // Close WebSocket
      if (ws) { ws.close(); ws = null; }
      break;

    case "error":
      statusEl.textContent = "Fehler: " + data.message;
      recordBtn.classList.remove("processing");
      recordBtn.querySelector(".label").textContent = "Halten zum Sprechen";
      if (ws) { ws.close(); ws = null; }
      break;
  }
}

// --- Clipboard ---
async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    return true;
  }
}

copyResultBtn.addEventListener("click", async () => {
  await copyToClipboard(resultText.value);
  copyResultBtn.textContent = "Kopiert!";
  setTimeout(() => { copyResultBtn.textContent = "📋 Kopieren"; }, 1500);
});

copyRawBtn.addEventListener("click", async () => {
  await copyToClipboard(lastRawText);
  copyRawBtn.textContent = "Kopiert!";
  setTimeout(() => { copyRawBtn.textContent = "📋 Kopieren"; }, 1500);
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
  const radius = 95; // Just outside the 180px button (90px radius + border)

  canvasCtx.clearRect(0, 0, width, height);

  const bars = 64;
  const step = Math.floor(bufferLength / bars);

  for (let i = 0; i < bars; i++) {
    const value = dataArray[i * step] / 255;
    const barHeight = value * 25 + 2;
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

// --- History (server-side with local fallback) ---
async function saveToHistory(result) {
  try {
    await apiPost("/api/history", {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        raw_text: result.raw_text,
        processed_text: result.processed_text,
        mode: result.mode,
        language: result.language,
      }),
    });
  } catch {
    // Fallback: save locally if server is unreachable
    const history = getLocalHistory();
    history.unshift({
      text: result.processed_text || result.raw_text,
      raw: result.raw_text,
      mode: result.mode,
      language: result.language,
      time: new Date().toISOString(),
    });
    if (history.length > 50) history.length = 50;
    localStorage.setItem("stt-history", JSON.stringify(history));
  }
  loadHistory();
}

function getLocalHistory() {
  try {
    return JSON.parse(localStorage.getItem("stt-history") || "[]");
  } catch { return []; }
}

async function loadHistory() {
  try {
    const data = await apiGet("/api/history");
    renderHistory(data.entries || []);
  } catch {
    renderHistory(getLocalHistory());
  }
}

function renderHistory(entries) {
  historyList.innerHTML = "";
  if (entries.length === 0) {
    historyList.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Noch keine Einträge</p>';
    return;
  }
  for (const item of entries.slice(0, 20)) {
    const el = document.createElement("div");
    el.className = "history-item";
    const text = item.processed_text || item.text || "";
    const mode = item.mode || "raw";
    const time = item.time ? new Date(item.time).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" }) : "";
    el.innerHTML = `
      <span class="time">${time}<span class="mode-tag">${mode}</span></span>
      <div class="text">${escapeHtml(text)}</div>
    `;
    el.addEventListener("click", async () => {
      await copyToClipboard(text);
      statusEl.textContent = "Aus Verlauf kopiert";
    });
    historyList.appendChild(el);
  }
}

clearHistory.addEventListener("click", async () => {
  localStorage.removeItem("stt-history");
  try {
    await fetch(apiUrl("/api/history"), { method: "DELETE" });
  } catch { /* ignore */ }
  loadHistory();
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
  document.getElementById("serverUrl").value = serverUrl;

  checkServer();
  loadModels();
  loadWhisperModels();
  loadPromptEditors();

  try {
    const data = await apiGet("/api/glossary");
    renderGlossary(data.words || []);
  } catch { renderGlossary([]); }

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

// Model selector
async function loadModels() {
  const container = document.getElementById("modelSelector");
  const modelStatusEl = document.getElementById("modelStatus");
  modelStatusEl.innerHTML = "";
  try {
    const data = await apiGet("/api/models");
    container.innerHTML = "";
    for (const model of data.models) {
      const isActive = model.id === data.active;
      const tooLarge = model.fits_gpu === false;
      const el = document.createElement("div");
      el.className = "model-option" + (isActive ? " active" : "") + (tooLarge ? " disabled" : "");

      const infoDiv = document.createElement("div");
      infoDiv.className = "model-info";
      infoDiv.innerHTML = `
        <span class="model-name">${escapeHtml(model.name)}</span>
        <span class="model-desc">${tooLarge ? "Passt nicht in GPU-Speicher" : escapeHtml(model.desc)}</span>
      `;

      const actionsDiv = document.createElement("div");
      actionsDiv.className = "model-actions";

      if (!model.installed && !tooLarge) {
        // Download button
        const dlBtn = document.createElement("button");
        dlBtn.className = "model-dl-btn";
        dlBtn.title = "Modell herunterladen";
        dlBtn.textContent = "⬇";
        dlBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          pullModel(model.id, el, dlBtn);
        });
        actionsDiv.appendChild(dlBtn);
      } else if (!isActive && model.installed) {
        // Delete button for installed but non-active models
        const delBtn = document.createElement("button");
        delBtn.className = "model-del-btn";
        delBtn.title = "Modell loeschen";
        delBtn.textContent = "🗑";
        delBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          delBtn.disabled = true;
          delBtn.textContent = "...";
          try {
            await fetch(apiUrl("/api/models/delete"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ model: model.id }),
            });
            modelStatusEl.innerHTML = '<span style="color:var(--success)">Modell geloescht!</span>';
            await loadModels();
          } catch (err) {
            modelStatusEl.innerHTML = '<span style="color:var(--recording)">Fehler: ' + escapeHtml(err.message) + '</span>';
            delBtn.disabled = false;
            delBtn.textContent = "🗑";
          }
        });
        actionsDiv.appendChild(delBtn);
      }

      const vramBadge = document.createElement("span");
      vramBadge.className = "model-vram";
      vramBadge.textContent = model.vram;
      actionsDiv.appendChild(vramBadge);

      el.appendChild(infoDiv);
      el.appendChild(actionsDiv);

      // Click to activate (only if installed and fits GPU)
      el.addEventListener("click", async () => {
        if (tooLarge) {
          modelStatusEl.innerHTML = '<span style="color:var(--text-muted)">Modell passt nicht in den GPU-Speicher.</span>';
          return;
        }
        if (!model.installed) {
          modelStatusEl.innerHTML = '<span style="color:var(--text-muted)">Modell muss erst heruntergeladen werden.</span>';
          return;
        }
        try {
          await fetch(apiUrl("/api/models"), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: model.id }),
          });
          modelStatusEl.innerHTML = '<span style="color:var(--success)">Modell gewechselt!</span>';
          await loadModels();
        } catch (err) {
          modelStatusEl.innerHTML = '<span style="color:var(--recording)">Fehler: ' + escapeHtml(err.message) + '</span>';
        }
      });

      container.appendChild(el);
    }
  } catch {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Server nicht erreichbar</p>';
  }
}

async function pullModel(modelId, optionEl, dlBtn) {
  // Replace download button with progress bar
  dlBtn.remove();
  const progressWrap = document.createElement("div");
  progressWrap.className = "model-progress-wrap";
  progressWrap.innerHTML = `
    <div class="model-progress-bar"><div class="model-progress-fill" style="width:0%"></div></div>
    <span class="model-progress-text">0%</span>
  `;
  optionEl.querySelector(".model-actions").prepend(progressWrap);

  const fill = progressWrap.querySelector(".model-progress-fill");
  const text = progressWrap.querySelector(".model-progress-text");
  const modelStatusEl = document.getElementById("modelStatus");

  try {
    const resp = await fetch(apiUrl("/api/models/pull"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelId }),
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = JSON.parse(line.slice(6));

        if (data.status === "error") {
          text.textContent = "Fehler";
          modelStatusEl.innerHTML = '<span style="color:var(--recording)">Download fehlgeschlagen: ' + escapeHtml(data.message) + '</span>';
          return;
        }

        if (data.percent > 0) {
          fill.style.width = data.percent + "%";
          text.textContent = data.percent + "%";
        } else {
          text.textContent = data.status;
        }

        if (data.status === "success") {
          text.textContent = "Fertig!";
          fill.style.width = "100%";
          modelStatusEl.innerHTML = '<span style="color:var(--success)">Download abgeschlossen!</span>';
          setTimeout(() => loadModels(), 1000);
          return;
        }
      }
    }
  } catch (err) {
    text.textContent = "Fehler";
    modelStatusEl.innerHTML = '<span style="color:var(--recording)">Download fehlgeschlagen</span>';
  }
}

// Whisper model selector
async function loadWhisperModels() {
  const container = document.getElementById("whisperModelSelector");
  const statusEl = document.getElementById("whisperModelStatus");
  statusEl.innerHTML = "";
  try {
    const data = await apiGet("/api/whisper-models");
    container.innerHTML = "";
    for (const model of data.models) {
      const isActive = model.id === data.active;
      const tooLarge = model.fits_gpu === false;
      const el = document.createElement("div");
      el.className = "model-option" + (isActive ? " active" : "") + (tooLarge ? " disabled" : "");

      const infoDiv = document.createElement("div");
      infoDiv.className = "model-info";
      infoDiv.innerHTML = `
        <span class="model-name">${escapeHtml(model.name)}</span>
        <span class="model-desc">${tooLarge ? "Passt nicht in GPU-Speicher" : escapeHtml(model.desc)}</span>
      `;

      const actionsDiv = document.createElement("div");
      actionsDiv.className = "model-actions";

      const vramBadge = document.createElement("span");
      vramBadge.className = "model-vram";
      vramBadge.textContent = model.vram;
      actionsDiv.appendChild(vramBadge);

      el.appendChild(infoDiv);
      el.appendChild(actionsDiv);

      el.addEventListener("click", async () => {
        if (tooLarge) {
          statusEl.innerHTML = '<span style="color:var(--text-muted)">Modell passt nicht in den GPU-Speicher.</span>';
          return;
        }
        try {
          await fetch(apiUrl("/api/whisper-models"), {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ model: model.id }),
          });
          statusEl.innerHTML = '<span style="color:var(--success)">Modell wird beim naechsten Gebrauch geladen</span>';
          await loadWhisperModels();
        } catch (err) {
          statusEl.innerHTML = '<span style="color:var(--recording)">Fehler: ' + escapeHtml(err.message) + '</span>';
        }
      });

      container.appendChild(el);
    }
  } catch {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem">Server nicht erreichbar</p>';
  }
}

// Preset management


// --- Prompt editors for Cleanup and Reformulieren ---
async function loadPromptEditors() {
  try {
    const data = await apiGet("/api/presets");
    const presets = data.presets || [];
    for (const p of presets) {
      if (p.id === "cleanup") {
        document.getElementById("cleanupPromptText").value = p.prompt || "";
      } else if (p.id === "rephrase") {
        document.getElementById("rephrasePromptText").value = p.prompt || "";
      }
    }
  } catch { /* ignore */ }
}

function _setupPromptEditor(mode, textareaId, saveBtnId, resetBtnId, statusId) {
  document.getElementById(saveBtnId).addEventListener("click", async () => {
    const prompt = document.getElementById(textareaId).value.trim();
    const statusEl = document.getElementById(statusId);
    try {
      await fetch(apiUrl("/api/presets/" + mode), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      statusEl.textContent = "Gespeichert!";
      setTimeout(() => { statusEl.textContent = ""; }, 2000);
    } catch (err) {
      statusEl.textContent = "Fehler: " + err.message;
    }
  });

  document.getElementById(resetBtnId).addEventListener("click", async () => {
    const statusEl = document.getElementById(statusId);
    try {
      // Fetch default prompts from cleanup.py via a special reset call
      await fetch(apiUrl("/api/presets/" + mode + "/reset"), { method: "POST" });
      await loadPromptEditors();
      statusEl.textContent = "Zurueckgesetzt!";
      setTimeout(() => { statusEl.textContent = ""; }, 2000);
    } catch (err) {
      statusEl.textContent = "Fehler: " + err.message;
    }
  });
}

_setupPromptEditor("cleanup", "cleanupPromptText", "cleanupPromptSave", "cleanupPromptReset", "cleanupPromptStatus");
_setupPromptEditor("rephrase", "rephrasePromptText", "rephrasePromptSave", "rephrasePromptReset", "rephrasePromptStatus");

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
    const usedMB = data.gpu_memory_used_mb || 0;
    const totalMB = data.gpu_memory_total_mb || 0;
    const pct = data.gpu_memory_percent || 0;
    const usedGB = (usedMB / 1024).toFixed(1);
    const totalGB = (totalMB / 1024).toFixed(1);

    el.innerHTML = `
      <div class="server-status-grid">
        <div class="status-row">
          <span class="status-dot connected"></span>
          <span class="status-label">Verbunden</span>
        </div>
        <div class="status-row">
          <span class="status-key">GPU</span>
          <span class="status-value">${escapeHtml(data.gpu_name || "unknown")}</span>
        </div>
        <div class="status-row">
          <span class="status-key">VRAM</span>
          <span class="status-value">${usedGB} / ${totalGB} GB (${pct}%)</span>
        </div>
        <div class="status-row">
          <span class="status-key">Transkription</span>
          <span class="status-value">${escapeHtml(data.whisper_model || "unknown")}</span>
        </div>
        <div class="status-row">
          <span class="status-key">Textverarbeitung</span>
          <span class="status-value">${escapeHtml(data.llm_model || "keins")}</span>
        </div>
      </div>
    `;
  } catch {
    el.innerHTML = '<div class="server-status-grid"><div class="status-row"><span class="status-dot disconnected"></span><span class="status-label" style="color:var(--recording)">Nicht erreichbar</span></div></div>';
  }
}

// --- Init ---
loadHistory();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => {});
}
