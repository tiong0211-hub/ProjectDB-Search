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
