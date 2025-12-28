async function fetchStatus() {
  try {
    const url = new URL("/status", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 2000);
    if (!resp.ok) throw new Error("status error");
    return await resp.json();
  } catch (e) {
    return { has_key: false, error: true };
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function fetchWithTimeout(url, options = {}, timeoutMs = 2000) {
  const controller = new AbortController();
  const id = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(id);
  }
}

async function waitForStatus(timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    try {
      const url = new URL("/status", window.location.origin);
      url.searchParams.set("_", Date.now().toString());
      const resp = await fetchWithTimeout(url, {}, 2000);
      if (resp.ok) return await resp.json();
    } catch (e) {}
    if (Date.now() >= deadline) return null;
    await sleep(500);
  }
}

async function validateKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch("/validate_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || "validation_failed");
  }
  return data;
}

async function saveKey(key) {
  const body = { openai_api_key: key };
  const resp = await fetch("/openai_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
}

// ---------- ElevenLabs API ----------
async function fetchElevenLabsStatus() {
  try {
    const url = new URL("/elevenlabs_status", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 2000);
    if (!resp.ok) throw new Error("status error");
    return await resp.json();
  } catch (e) {
    return { has_key: false };
  }
}

async function saveElevenLabsKey(key) {
  const body = { elevenlabs_api_key: key };
  const resp = await fetch("/elevenlabs_api_key", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
}

// ---------- Dance Settings API ----------
async function fetchDanceSettings() {
  try {
    const url = new URL("/dance_settings", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 2000);
    if (!resp.ok) throw new Error("fetch_failed");
    return await resp.json();
  } catch (e) {
    console.error("Failed to fetch dance settings:", e);
    return { intensity: 0.7 };
  }
}

async function saveDanceSettings(intensity) {
  const resp = await fetch("/dance_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ intensity }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "save_failed");
  }
  return await resp.json();
}

// ---------- Songs API ----------
async function fetchSongs() {
  try {
    const url = new URL("/songs", window.location.origin);
    url.searchParams.set("_", Date.now().toString());
    const resp = await fetchWithTimeout(url, {}, 5000);
    if (!resp.ok) throw new Error("fetch_failed");
    return await resp.json();
  } catch (e) {
    console.error("Failed to fetch songs:", e);
    return { songs: [] };
  }
}

async function deleteSong(filename) {
  const resp = await fetch(`/songs/${encodeURIComponent(filename)}`, {
    method: "DELETE",
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "delete_failed");
  }
  return await resp.json();
}

async function updateSongTitle(filename, title) {
  const resp = await fetch(`/songs/${encodeURIComponent(filename)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}));
    throw new Error(data.error || "update_failed");
  }
  return await resp.json();
}

function formatFileSize(bytes) {
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function formatTimestamp(timestamp) {
  if (!timestamp) return "";
  const date = new Date(timestamp * 1000);
  return date.toLocaleDateString() + " " + date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function show(el, flag) {
  if (el) el.classList.toggle("hidden", !flag);
}

// Music icon SVG
const MUSIC_ICON = `<svg viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>`;
const EDIT_ICON = `<svg viewBox="0 0 24 24" width="14" height="14"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34c-.39-.39-1.02-.39-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>`;

async function init() {
  const loading = document.getElementById("loading");
  show(loading, true);
  const statusEl = document.getElementById("status");
  const formPanel = document.getElementById("form-panel");
  const configuredPanel = document.getElementById("configured");
  const songsPanel = document.getElementById("songs-panel");
  const saveBtn = document.getElementById("save-btn");
  const changeKeyBtn = document.getElementById("change-key-btn");
  const input = document.getElementById("api-key");

  // ElevenLabs elements
  const elevenLabsConfigured = document.getElementById("elevenlabs-configured");
  const elevenLabsPanel = document.getElementById("elevenlabs-panel");
  const elevenLabsInput = document.getElementById("elevenlabs-key");
  const elevenLabsSaveBtn = document.getElementById("save-elevenlabs-btn");
  const changeElevenLabsBtn = document.getElementById("change-elevenlabs-btn");
  const elevenLabsStatus = document.getElementById("elevenlabs-status");

  // Songs elements
  const songsList = document.getElementById("songs-list");
  const songsCount = document.getElementById("songs-count");
  const songsStatus = document.getElementById("songs-status");
  const refreshSongsBtn = document.getElementById("refresh-songs");

  // Currently playing audio element
  let currentAudio = null;

  if (statusEl) statusEl.textContent = "Checking configuration...";
  show(formPanel, false);
  show(configuredPanel, false);
  show(songsPanel, false);

  const st = (await waitForStatus()) || { has_key: false };
  if (st.has_key) {
    if (statusEl) statusEl.textContent = "";
    show(configuredPanel, true);
  }

  // Handler for "Change API key" button
  if (changeKeyBtn) {
    changeKeyBtn.addEventListener("click", () => {
      show(configuredPanel, false);
      show(formPanel, true);
      if (input) input.value = "";
      if (statusEl) {
        statusEl.textContent = "";
        statusEl.className = "status";
      }
    });
  }

  // Remove error styling when user starts typing
  if (input) {
    input.addEventListener("input", () => {
      input.classList.remove("error");
    });
  }

  if (saveBtn) {
    saveBtn.addEventListener("click", async () => {
      const key = input ? input.value.trim() : "";
      if (!key) {
        if (statusEl) {
          statusEl.textContent = "Please enter a valid key.";
          statusEl.className = "status warn";
        }
        if (input) input.classList.add("error");
        return;
      }
      if (statusEl) {
        statusEl.textContent = "Validating API key...";
        statusEl.className = "status";
      }
      if (input) input.classList.remove("error");

      let validationPassed = false;
      let validationError = null;

      try {
        // Try to validate the key (may fail if network is unavailable)
        const validation = await validateKey(key);
        if (validation.valid) {
          validationPassed = true;
        } else if (validation.error === "invalid_api_key") {
          // Definitely invalid key - don't save
          if (statusEl) {
            statusEl.textContent = "Invalid API key. Please check your key and try again.";
            statusEl.className = "status error";
          }
          if (input) input.classList.add("error");
          return;
        } else {
          // Network or other error - allow saving anyway
          validationError = validation.error || "validation_failed";
        }
      } catch (e) {
        // Network error during validation - allow saving anyway
        validationError = e.message || "network_error";
      }

      try {
        if (statusEl) {
          if (validationPassed) {
            statusEl.textContent = "Key valid! Saving...";
          } else {
            statusEl.textContent = "Saving key (validation skipped)...";
          }
          statusEl.className = "status";
        }
        await saveKey(key);
        if (statusEl) {
          statusEl.textContent = validationPassed ? "Saved. Reloading…" : "Saved (not validated). Reloading…";
          statusEl.className = "status ok";
        }
        window.location.reload();
      } catch (e) {
        if (input) input.classList.add("error");
        if (statusEl) {
          statusEl.textContent = "Failed to save key: " + (e.message || "unknown error");
          statusEl.className = "status error";
        }
      }
    });
  }

  // Initialize ElevenLabs panel
  async function initElevenLabsPanel() {
    if (!elevenLabsPanel && !elevenLabsConfigured) return;
    
    const elSt = await fetchElevenLabsStatus();
    
    // Show appropriate panel based on key status
    if (elSt.has_key) {
      show(elevenLabsConfigured, true);
      show(elevenLabsPanel, false);
    } else {
      show(elevenLabsConfigured, false);
      show(elevenLabsPanel, true);
    }

    // Handler for "Change API key" button
    if (changeElevenLabsBtn) {
      changeElevenLabsBtn.addEventListener("click", () => {
        show(elevenLabsConfigured, false);
        show(elevenLabsPanel, true);
        if (elevenLabsInput) elevenLabsInput.value = "";
        if (elevenLabsStatus) {
          elevenLabsStatus.textContent = "";
          elevenLabsStatus.className = "status";
        }
      });
    }

    // Remove error styling when user starts typing
    if (elevenLabsInput) {
      elevenLabsInput.addEventListener("input", () => {
        elevenLabsInput.classList.remove("error");
      });
    }

    if (elevenLabsSaveBtn) {
      elevenLabsSaveBtn.addEventListener("click", async () => {
        const key = elevenLabsInput ? elevenLabsInput.value.trim() : "";
        if (!key) {
          if (elevenLabsStatus) {
            elevenLabsStatus.textContent = "Please enter a valid key.";
            elevenLabsStatus.className = "status warn";
          }
          if (elevenLabsInput) elevenLabsInput.classList.add("error");
          return;
        }
        if (elevenLabsStatus) {
          elevenLabsStatus.textContent = "Saving...";
          elevenLabsStatus.className = "status";
        }
        if (elevenLabsInput) elevenLabsInput.classList.remove("error");
        try {
          await saveElevenLabsKey(key);
          if (elevenLabsStatus) {
            elevenLabsStatus.textContent = "Saved!";
            elevenLabsStatus.className = "status ok";
          }
          if (elevenLabsInput) elevenLabsInput.value = "";
          // Switch to configured view after successful save
          setTimeout(() => {
            show(elevenLabsPanel, false);
            show(elevenLabsConfigured, true);
          }, 1000);
        } catch (e) {
          console.error("ElevenLabs save error:", e);
          if (elevenLabsInput) elevenLabsInput.classList.add("error");
          if (elevenLabsStatus) {
            elevenLabsStatus.textContent = "Failed to save: " + (e.message || "unknown error");
            elevenLabsStatus.className = "status error";
          }
        }
      });
    }
  }

  // Render songs list
  function renderSongs(songs) {
    if (!songsList) return;
    if (songsCount) songsCount.textContent = `${songs.length} song${songs.length !== 1 ? "s" : ""}`;
    
    if (!songs.length) {
      songsList.innerHTML = `
        <div class="songs-empty">
          <svg viewBox="0 0 24 24"><path d="M12 3v10.55c-.59-.34-1.27-.55-2-.55-2.21 0-4 1.79-4 4s1.79 4 4 4 4-1.79 4-4V7h4V3h-6z"/></svg>
          <p>No songs yet. Ask Reachy to make you a song!</p>
        </div>
      `;
      return;
    }

    // Escape HTML to prevent XSS
    function escapeHtml(str) {
      const div = document.createElement("div");
      div.textContent = str;
      return div.innerHTML;
    }

    songsList.innerHTML = songs.map(song => `
      <div class="song-item" data-filename="${escapeHtml(song.filename)}">
        <div class="song-icon">${MUSIC_ICON}</div>
        <div class="song-info">
          <div class="song-title-row">
            <span class="song-name" data-filename="${escapeHtml(song.filename)}">${escapeHtml(song.title || song.prompt || song.filename)}</span>
            <button class="edit-btn" data-filename="${escapeHtml(song.filename)}" data-title="${escapeHtml(song.title || song.prompt || "")}" title="Edit title">${EDIT_ICON}</button>
          </div>
          <div class="song-meta">${formatTimestamp(song.timestamp)} • ${formatFileSize(song.size)}</div>
        </div>
        <div class="song-actions">
          <button class="play-btn" data-filename="${escapeHtml(song.filename)}">Play</button>
          <button class="download-btn" data-filename="${escapeHtml(song.filename)}">Download</button>
          <button class="delete-btn" data-filename="${escapeHtml(song.filename)}">Delete</button>
        </div>
      </div>
    `).join("");

    // Attach play button listeners
    songsList.querySelectorAll(".play-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const filename = e.target.dataset.filename;
        const songUrl = `/songs/${encodeURIComponent(filename)}`;
        const wasPlaying = e.target.textContent === "Stop";
        
        // Stop current audio if playing
        if (currentAudio) {
          currentAudio.pause();
          currentAudio = null;
          // Reset all play buttons
          songsList.querySelectorAll(".play-btn").forEach(b => b.textContent = "Play");
        }
        
        // If this button was showing "Stop", we just wanted to stop - don't restart
        if (wasPlaying) {
          return;
        }

        try {
          currentAudio = new Audio(songUrl);
          currentAudio.play();
          e.target.textContent = "Stop";
          
          currentAudio.onended = () => {
            e.target.textContent = "Play";
            currentAudio = null;
          };
          
          currentAudio.onerror = () => {
            songsStatus.textContent = "Failed to play song.";
            songsStatus.className = "status error";
            e.target.textContent = "Play";
            currentAudio = null;
          };
        } catch (err) {
          songsStatus.textContent = "Failed to play song.";
          songsStatus.className = "status error";
        }
      });
    });

    // Attach download button listeners
    songsList.querySelectorAll(".download-btn").forEach(btn => {
      btn.addEventListener("click", (e) => {
        const filename = e.target.dataset.filename;
        const songUrl = `/songs/${encodeURIComponent(filename)}`;
        
        // Create a temporary link and trigger download
        const link = document.createElement("a");
        link.href = songUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
      });
    });

    // Attach delete button listeners
    songsList.querySelectorAll(".delete-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const filename = e.target.dataset.filename;
        const songItem = e.target.closest(".song-item");
        const title = songItem.querySelector(".song-name").textContent;
        if (!confirm(`Delete "${title}"?`)) return;
        
        songsStatus.textContent = "Deleting...";
        songsStatus.className = "status";
        
        try {
          await deleteSong(filename);
          songsStatus.textContent = "Song deleted.";
          songsStatus.className = "status ok";
          // Refresh the list
          await loadSongs();
        } catch (err) {
          songsStatus.textContent = "Failed to delete: " + (err.message || "unknown error");
          songsStatus.className = "status error";
        }
      });
    });

    // Attach edit button listeners
    songsList.querySelectorAll(".edit-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const filename = e.currentTarget.dataset.filename;
        const currentTitle = e.currentTarget.dataset.title;
        const songItem = e.currentTarget.closest(".song-item");
        const titleRow = songItem.querySelector(".song-title-row");
        const nameSpan = songItem.querySelector(".song-name");
        
        // Replace with input field
        const input = document.createElement("input");
        input.type = "text";
        input.className = "song-title-input";
        input.value = currentTitle;
        input.placeholder = "Enter song title";
        
        const saveBtn = document.createElement("button");
        saveBtn.className = "save-title-btn";
        saveBtn.textContent = "Save";
        
        const cancelBtn = document.createElement("button");
        cancelBtn.className = "cancel-title-btn ghost";
        cancelBtn.textContent = "Cancel";
        
        // Hide original elements
        nameSpan.style.display = "none";
        e.currentTarget.style.display = "none";
        
        // Add input and buttons
        titleRow.appendChild(input);
        titleRow.appendChild(saveBtn);
        titleRow.appendChild(cancelBtn);
        input.focus();
        input.select();
        
        const cleanup = () => {
          input.remove();
          saveBtn.remove();
          cancelBtn.remove();
          nameSpan.style.display = "";
          titleRow.querySelector(".edit-btn").style.display = "";
        };
        
        cancelBtn.addEventListener("click", cleanup);
        
        saveBtn.addEventListener("click", async () => {
          const newTitle = input.value.trim();
          if (!newTitle) {
            songsStatus.textContent = "Title cannot be empty.";
            songsStatus.className = "status warn";
            return;
          }
          
          songsStatus.textContent = "Saving...";
          songsStatus.className = "status";
          
          try {
            await updateSongTitle(filename, newTitle);
            nameSpan.textContent = newTitle;
            titleRow.querySelector(".edit-btn").dataset.title = newTitle;
            cleanup();
            songsStatus.textContent = "Title updated.";
            songsStatus.className = "status ok";
            setTimeout(() => { songsStatus.textContent = ""; }, 2000);
          } catch (err) {
            songsStatus.textContent = "Failed to update: " + (err.message || "unknown error");
            songsStatus.className = "status error";
          }
        });
        
        // Save on Enter, cancel on Escape
        input.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter") {
            saveBtn.click();
          } else if (ev.key === "Escape") {
            cleanup();
          }
        });
      });
    });
  }

  // Load and display songs
  async function loadSongs() {
    const data = await fetchSongs();
    renderSongs(data.songs || []);
  }

  // Initialize songs panel
  async function initSongsPanel() {
    if (!songsPanel) return;
    show(songsPanel, true);
    await loadSongs();
    
    if (refreshSongsBtn) {
      refreshSongsBtn.addEventListener("click", async () => {
        if (songsStatus) {
          songsStatus.textContent = "Refreshing...";
          songsStatus.className = "status";
        }
        await loadSongs();
        if (songsStatus) {
          songsStatus.textContent = "Refreshed.";
          songsStatus.className = "status ok";
          setTimeout(() => {
            songsStatus.textContent = "";
          }, 2000);
        }
      });
    }
  }

  // Initialize dance settings panel
  async function initDancePanel() {
    const dancePanel = document.getElementById("dance-panel");
    const danceSlider = document.getElementById("dance-intensity");
    const intensityValue = document.getElementById("intensity-value");
    const danceStatus = document.getElementById("dance-status");

    if (!dancePanel) return;
    show(dancePanel, true);

    // Load current intensity (stored as 0.0-0.25 internally, displayed as 0-100% on slider)
    // Mapping: slider 0-100 → internal 0.0-0.25
    const settings = await fetchDanceSettings();
    const internalIntensity = settings.intensity || 0.20;
    // Convert internal (0-0.25) to slider (0-100): slider = internal * 400
    const sliderValue = Math.round(internalIntensity * 400);
    const clampedValue = Math.max(0, Math.min(100, sliderValue));
    if (danceSlider) danceSlider.value = clampedValue;
    if (intensityValue) intensityValue.textContent = clampedValue + "%";

    // Debounce timer for saving
    let saveTimeout = null;

    if (danceSlider) {
      // Update display on input (immediate feedback)
      danceSlider.addEventListener("input", () => {
        const val = danceSlider.value;
        if (intensityValue) intensityValue.textContent = val + "%";
      });

      // Save on change (debounced)
      danceSlider.addEventListener("change", async () => {
        const val = parseInt(danceSlider.value, 10);
        // Convert slider (0-100) to internal (0-0.25): internal = slider / 400
        const intensity = val / 400;

        if (danceStatus) {
          danceStatus.textContent = "Saving...";
          danceStatus.className = "status";
        }

        try {
          await saveDanceSettings(intensity);
          if (danceStatus) {
            danceStatus.textContent = "Saved!";
            danceStatus.className = "status ok";
            setTimeout(() => {
              danceStatus.textContent = "";
            }, 2000);
          }
        } catch (e) {
          console.error("Failed to save dance settings:", e);
          if (danceStatus) {
            danceStatus.textContent = "Failed to save.";
            danceStatus.className = "status error";
          }
        }
      });
    }
  }

  if (!st.has_key) {
    if (statusEl) statusEl.textContent = "";
    show(formPanel, true);
    await initElevenLabsPanel();
    show(loading, false);
    return;
  }

  // Initialize panels when API key is configured
  await initElevenLabsPanel();
  await initSongsPanel();
  await initDancePanel();
  show(loading, false);
}

window.addEventListener("DOMContentLoaded", init);
