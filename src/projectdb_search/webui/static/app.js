function initIndexProgress() {
  const form = document.getElementById("index-form");
  if (!form) return;

  const submitBtn = document.getElementById("index-submit");
  const panel = document.getElementById("index-progress-panel");
  const bar = document.getElementById("index-progress-bar");
  const status = document.getElementById("index-progress-status");
  const fileLine = document.getElementById("index-progress-file");
  const cancelBtn = document.getElementById("index-cancel");
  let pollHandle = null;

  function render(state) {
    const done = state.done || 0;
    const total = state.total || 0;
    if (state.phase === "scanning") {
      bar.style.width = "0%";
      status.textContent = "Scanning folder for documents…";
      fileLine.textContent = "";
    } else if (state.phase === "building") {
      bar.style.width = "100%";
      status.textContent = "Building search index…";
      fileLine.textContent = "";
    } else if (total > 0) {
      bar.style.width = Math.round((done / total) * 100) + "%";
      status.textContent = "Reading filenames: " + done.toLocaleString() + " / " + total.toLocaleString();
      fileLine.textContent = state.current_file || "";
    }
  }

  function stopPolling() {
    if (pollHandle) {
      clearInterval(pollHandle);
      pollHandle = null;
    }
  }

  function poll() {
    fetch("/index-documents/progress")
      .then(function (r) { return r.json(); })
      .then(function (state) {
        if (state.running) {
          panel.hidden = false;
          render(state);
          return;
        }
        stopPolling();
        if (state.error) {
          status.textContent = "Error: " + state.error;
          fileLine.textContent = "";
          submitBtn.disabled = false;
          submitBtn.textContent = "Build index";
          return;
        }
        // The finished run's summary is rendered server-side on the next
        // page load, along with the refreshed pending-deep-scan section.
        window.location.reload();
      })
      .catch(function () {
        stopPolling();
        status.textContent = "Lost contact with the indexer.";
        submitBtn.disabled = false;
        submitBtn.textContent = "Build index";
      });
  }

  function startPolling() {
    if (pollHandle) return;
    poll();
    pollHandle = setInterval(poll, 500);
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    submitBtn.disabled = true;
    submitBtn.textContent = "Indexing…";
    panel.hidden = false;
    status.textContent = "Starting…";
    fileLine.textContent = "";
    bar.style.width = "0%";

    fetch("/index-documents/start", { method: "POST", body: new FormData(form) })
      .then(function (r) {
        if (r.status === 400) {
          return r.json().then(function (body) { throw new Error(body.error || "invalid folder"); });
        }
        if (!r.ok && r.status !== 409) throw new Error("failed to start");
        startPolling();
      })
      .catch(function (err) {
        panel.hidden = false;
        status.textContent = err.message || "Failed to start indexing.";
        fileLine.textContent = "";
        submitBtn.disabled = false;
        submitBtn.textContent = "Build index";
      });
  });

  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      cancelBtn.disabled = true;
      fetch("/index-documents/cancel", { method: "POST" }).finally(function () {
        cancelBtn.disabled = false;
      });
    });
  }

  // An index started from another tab (or before a reload) keeps running
  // in the background -- pick its progress back up instead of showing an
  // idle form.
  fetch("/index-documents/progress")
    .then(function (r) { return r.json(); })
    .then(function (state) {
      if (!state.running) return;
      submitBtn.disabled = true;
      submitBtn.textContent = "Indexing…";
      panel.hidden = false;
      render(state);
      startPolling();
    })
    .catch(function () { /* nothing running, or page has no indexer */ });
}

document.addEventListener("DOMContentLoaded", initIndexProgress);

function initDeepScan() {
  const section = document.getElementById("deep-scan-section");
  if (!section) return;

  const startBtn = document.getElementById("deep-scan-start");
  const cancelBtn = document.getElementById("deep-scan-cancel");
  const panel = document.getElementById("deep-scan-panel");
  const bar = document.getElementById("deep-scan-bar");
  const status = document.getElementById("deep-scan-status");
  let pollHandle = null;

  function setStatus(state) {
    const done = state.done || 0;
    const total = state.total || 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;
    bar.style.width = pct + "%";
    if (state.running) {
      status.textContent = total > 0
        ? done + " / " + total + " — " + (state.current_file || "")
        : "Starting…";
    } else if (state.error) {
      status.textContent = "Error: " + state.error;
    } else if (state.summary) {
      const s = state.summary;
      status.textContent = s.cancelled
        ? "Stopped after " + s.processed + " / " + s.total_pending + " — click Start deep scan to resume."
        : "Done: " + s.processed + " / " + s.total_pending + " processed.";
    }
  }

  function poll() {
    fetch("/deep-scan/progress")
      .then(function (r) { return r.json(); })
      .then(function (state) {
        setStatus(state);
        if (state.running) {
          panel.hidden = false;
          if (startBtn) startBtn.hidden = true;
        } else {
          if (pollHandle) {
            clearInterval(pollHandle);
            pollHandle = null;
          }
          if (state.summary && !state.summary.cancelled && state.summary.processed === state.summary.total_pending) {
            if (startBtn) startBtn.hidden = true;
          } else if (startBtn) {
            startBtn.hidden = false;
            startBtn.textContent = state.summary ? "Resume deep scan" : "Start deep scan";
          }
        }
      })
      .catch(function () {
        if (pollHandle) {
          clearInterval(pollHandle);
          pollHandle = null;
        }
      });
  }

  function startPolling() {
    if (pollHandle) return;
    poll();
    pollHandle = setInterval(poll, 1000);
  }

  if (startBtn) {
    startBtn.addEventListener("click", function () {
      startBtn.hidden = true;
      panel.hidden = false;
      status.textContent = "Starting…";
      fetch("/deep-scan", { method: "POST" })
        .then(function (r) {
          if (!r.ok && r.status !== 409) throw new Error("failed to start");
          startPolling();
        })
        .catch(function () {
          status.textContent = "Failed to start deep scan.";
          startBtn.hidden = false;
        });
    });
  }

  if (cancelBtn) {
    cancelBtn.addEventListener("click", function () {
      cancelBtn.disabled = true;
      fetch("/deep-scan/cancel", { method: "POST" }).finally(function () {
        cancelBtn.disabled = false;
      });
    });
  }

  // The scan may already be running in the background from a previous page
  // load (it's not tied to this page's lifetime) -- check once on load so
  // reopening this page shows live progress instead of a stale start button.
  startPolling();
}

document.addEventListener("DOMContentLoaded", initDeepScan);

document.addEventListener("click", function (event) {
  const btn = event.target.closest(".reveal-btn");
  if (!btn) return;

  const original = btn.textContent;
  btn.disabled = true;
  fetch("/reveal/" + btn.dataset.docId, { method: "POST" })
    .then(function (response) {
      if (response.status === 501) {
        btn.textContent = "Not supported here";
        return;
      }
      if (!response.ok) throw new Error("request failed");
      return response.json().then(function (body) {
        // Path too long for Explorer to select the exact file (Windows
        // MAX_PATH) -- we opened the parent folder instead, so say so
        // rather than claiming the file itself was found.
        btn.textContent = body.warning === "path_too_long" ? "Folder only (path too long)" : "Opened ✓";
      });
    })
    .catch(function () {
      btn.textContent = "Failed";
    })
    .finally(function () {
      setTimeout(function () {
        btn.textContent = original;
        btn.disabled = false;
      }, 1500);
    });
});

document.addEventListener("click", function (event) {
  const btn = event.target.closest("#show-more-results");
  if (!btn) return;

  // The full (up to 200) candidate pool is already in the page, just
  // hidden -- revealing a batch is pure DOM work, no request needed.
  const batch = Number(btn.dataset.revealedBatches || "0");
  document.querySelectorAll('.result-item[data-batch="' + batch + '"]').forEach(function (item) {
    item.hidden = false;
  });
  btn.dataset.revealedBatches = String(batch + 1);

  const remaining = document.querySelectorAll(".result-item[hidden]").length;
  if (remaining === 0) {
    btn.remove();
  } else {
    btn.textContent = "Show " + Math.min(remaining, 25) + " more (of " + remaining + ")";
  }
});

document.addEventListener("click", function (event) {
  const btn = event.target.closest(".feedback-btn");
  if (!btn) return;

  const contextInput = document.getElementById("search-context");
  if (!contextInput) return;
  const context = JSON.parse(contextInput.value);

  fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: context.query,
      top_doc_ids: context.top_doc_ids,
      scores: context.scores,
      ambiguous: context.ambiguous,
      used_llm_rerank: context.used_llm_rerank,
      doc_id: btn.dataset.docId,
      correct: btn.dataset.correct === "true",
    }),
  })
    .then(function (response) {
      if (!response.ok) throw new Error("request failed");
      return response.json();
    })
    .then(function () {
      btn.closest(".result-actions").innerHTML = '<span class="feedback-thanks">Thanks — feedback logged.</span>';
    })
    .catch(function () {
      btn.insertAdjacentHTML("afterend", '<span class="feedback-error"> (failed to log)</span>');
    });
});

function initFolderPicker() {
  const picker = document.getElementById("folder-picker");
  if (!picker) return;

  const chips = document.getElementById("folder-chips");
  const input = document.getElementById("folder-input");
  const known = Array.from(document.querySelectorAll("#folder-options option")).map(function (o) {
    return o.value;
  });

  function addChip(folder) {
    folder = folder.trim();
    if (!folder || chips.querySelector('[data-folder="' + CSS.escape(folder) + '"]')) {
      input.value = "";
      return;
    }
    const chip = document.createElement("span");
    chip.className = "folder-chip";
    chip.dataset.folder = folder;
    chip.appendChild(document.createTextNode(folder + " "));

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "folder-chip-remove";
    remove.setAttribute("aria-label", "Remove " + folder);
    remove.textContent = "×";
    chip.appendChild(remove);

    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = "folder";
    hidden.value = folder;
    chip.appendChild(hidden);

    chips.appendChild(chip);
    input.value = "";
  }

  // Only a value that's actually in the <datalist> (typed in full, or
  // picked from the browser's own autocomplete dropdown) becomes a chip --
  // this keeps stray typed text from turning into a bogus folder filter.
  input.addEventListener("keydown", function (event) {
    if (event.key === "Enter" && known.includes(input.value.trim())) {
      event.preventDefault(); // don't submit the whole search form
      addChip(input.value);
    }
  });
  input.addEventListener("change", function () {
    if (known.includes(input.value)) addChip(input.value);
  });

  chips.addEventListener("click", function (event) {
    const btn = event.target.closest(".folder-chip-remove");
    if (btn) btn.closest(".folder-chip").remove();
  });
}

document.addEventListener("DOMContentLoaded", initFolderPicker);
