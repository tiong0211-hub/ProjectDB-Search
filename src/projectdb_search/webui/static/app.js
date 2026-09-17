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
      btn.textContent = "Opened ✓";
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
