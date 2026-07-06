const state = {
  session: null,
  preview: null,
  selectedAction: null,
  turns: [],
};

const elements = {
  healthText: document.getElementById("healthText"),
  sessionForm: document.getElementById("sessionForm"),
  loadSessionButton: document.getElementById("loadSessionButton"),
  sessionIdInput: document.getElementById("sessionIdInput"),
  profileSelect: document.getElementById("profileSelect"),
  sessionCard: document.getElementById("sessionCard"),
  previewForm: document.getElementById("previewForm"),
  messageInput: document.getElementById("messageInput"),
  previewEmpty: document.getElementById("previewEmpty"),
  previewPanel: document.getElementById("previewPanel"),
  suggestedActionBadge: document.getElementById("suggestedActionBadge"),
  riskBadge: document.getElementById("riskBadge"),
  previewIdText: document.getElementById("previewIdText"),
  originalText: document.getElementById("originalText"),
  redactedText: document.getElementById("redactedText"),
  previewSummary: document.getElementById("previewSummary"),
  actionButtons: document.getElementById("actionButtons"),
  overrideField: document.getElementById("overrideField"),
  overrideReasonInput: document.getElementById("overrideReasonInput"),
  confirmButton: document.getElementById("confirmButton"),
  resultPanel: document.getElementById("resultPanel"),
  historyPanel: document.getElementById("historyPanel"),
  toast: document.getElementById("toast"),
};

async function requestJson(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed with status ${response.status}`);
  }
  return payload;
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.classList.remove("hidden");
  window.clearTimeout(showToast.timeoutId);
  showToast.timeoutId = window.setTimeout(() => {
    elements.toast.classList.add("hidden");
  }, 2800);
}

function formatActionClass(value) {
  return `action-${String(value || "").toLowerCase()}`;
}

function formatRiskClass(value) {
  return `risk-${String(value || "").toLowerCase()}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderSession() {
  if (!state.session) {
    elements.sessionCard.innerHTML = "<p>No session yet. Create one before preview and confirm.</p>";
    return;
  }

  elements.sessionCard.innerHTML = `
    <p><strong>Session ID:</strong> ${escapeHtml(state.session.session_id)}</p>
    <p><strong>Profile:</strong> ${escapeHtml(state.session.profile)}</p>
    <p><strong>Created At:</strong> ${escapeHtml(state.session.created_at)}</p>
    <p><strong>Session Dir:</strong> ${escapeHtml(state.session.session_dir)}</p>
    <p><strong>Turns:</strong> ${state.turns.length}</p>
  `;
}

function renderPreview() {
  if (!state.preview) {
    elements.previewEmpty.classList.remove("hidden");
    elements.previewPanel.classList.add("hidden");
    return;
  }

  const suggestedAction = state.preview.suggested_action;
  const riskLevel = state.preview.risk_level;

  elements.previewEmpty.classList.add("hidden");
  elements.previewPanel.classList.remove("hidden");

  elements.suggestedActionBadge.className = `action-badge ${formatActionClass(suggestedAction)}`;
  elements.suggestedActionBadge.textContent = suggestedAction.toUpperCase();

  elements.riskBadge.className = `risk-badge ${formatRiskClass(riskLevel)}`;
  elements.riskBadge.textContent = riskLevel;

  elements.previewIdText.textContent = state.preview.preview_id;
  elements.originalText.textContent = state.preview.original_text;
  elements.redactedText.textContent = state.preview.redacted_text;
  elements.previewSummary.textContent =
    `Suggested action is ${suggestedAction.toUpperCase()}. You can still switch to allow, mask, or block before confirm.`;

  renderActionButtons();
  syncOverrideState();
}

function renderActionButtons() {
  elements.actionButtons.innerHTML = "";
  const options = state.preview.action_options;

  for (const actionName of ["allow", "mask", "block"]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `action-choice ${state.selectedAction === actionName ? "selected" : ""}`;
    button.innerHTML = `
      <strong class="pill ${formatActionClass(actionName)}">${actionName.toUpperCase()}</strong>
      <small>${escapeHtml(options[actionName].description)}</small>
    `;
    button.addEventListener("click", () => {
      state.selectedAction = actionName;
      renderActionButtons();
      syncOverrideState();
    });
    elements.actionButtons.appendChild(button);
  }
}

function syncOverrideState() {
  const isOverride = state.preview && state.selectedAction !== state.preview.suggested_action;
  elements.overrideField.classList.toggle("hidden", !isOverride);
}

function renderResult(result) {
  const artifactItems = Object.entries(result.artifacts || {})
    .map(([name, path]) => `<li><strong>${escapeHtml(name)}</strong>: ${escapeHtml(path)}</li>`)
    .join("");

  const replyBlock = result.blocked
    ? "<p><strong>This turn was blocked.</strong> Nothing was sent to the model.</p>"
    : `<pre>${escapeHtml(result.assistant_reply || "")}</pre>`;

  elements.resultPanel.className = "findings-card";
  elements.resultPanel.innerHTML = `
    <p><strong>Final Action:</strong> <span class="pill ${formatActionClass(result.final_action)}">${escapeHtml(result.final_action).toUpperCase()}</span></p>
    <p><strong>Override:</strong> ${result.override ? "YES" : "NO"}</p>
    <p><strong>Sent Text:</strong> ${escapeHtml(result.sent_text || "(not sent)")}</p>
    ${replyBlock}
    <ul class="artifact-list">${artifactItems}</ul>
  `;
}

function renderHistory() {
  if (!state.session) {
    elements.historyPanel.className = "history-list muted-card";
    elements.historyPanel.innerHTML = "<p>Load a session to view turn history.</p>";
    return;
  }

  if (!state.turns.length) {
    elements.historyPanel.className = "history-list muted-card";
    elements.historyPanel.innerHTML = "<p>This session has no turns yet.</p>";
    return;
  }

  elements.historyPanel.className = "history-list";
  elements.historyPanel.innerHTML = state.turns
    .map((turn) => {
      const artifacts = Object.entries(turn.artifacts || {})
        .map(([name, path]) => `<li><strong>${escapeHtml(name)}</strong>: ${escapeHtml(path)}</li>`)
        .join("");

      return `
        <article class="history-item">
          <h3>Turn ${turn.turn_id}</h3>
          <p><strong>Suggested:</strong> <span class="pill ${formatActionClass(turn.suggested_action)}">${escapeHtml(turn.suggested_action).toUpperCase()}</span></p>
          <p><strong>Final:</strong> <span class="pill ${formatActionClass(turn.final_action)}">${escapeHtml(turn.final_action).toUpperCase()}</span></p>
          <p><strong>User Sent:</strong> ${escapeHtml(turn.user_sent_text || "(blocked)")}</p>
          <p><strong>Assistant Reply:</strong> ${escapeHtml(turn.codex_restored_reply || turn.codex_raw_reply || "(none)")}</p>
          <ul class="artifact-list">${artifacts}</ul>
        </article>
      `;
    })
    .join("");
}

async function refreshSession(sessionId) {
  const session = await requestJson(`/sessions/${encodeURIComponent(sessionId)}`);
  const turnsPayload = await requestJson(`/sessions/${encodeURIComponent(sessionId)}/turns`);
  state.session = session;
  state.turns = turnsPayload.turns || [];
  renderSession();
  renderHistory();
}

async function checkHealth() {
  try {
    await requestJson("/health");
    elements.healthText.textContent = "API online";
    document.querySelector(".status-dot").style.background = "var(--ok)";
    document.querySelector(".status-dot").style.boxShadow = "0 0 0 6px rgba(46, 125, 87, 0.12)";
  } catch (error) {
    elements.healthText.textContent = "API unavailable";
    showToast(error.message);
  }
}

elements.sessionForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  try {
    const payload = { profile: elements.profileSelect.value };
    const sessionId = elements.sessionIdInput.value.trim();
    if (sessionId) {
      payload.session_id = sessionId;
    }

    const session = await requestJson("/sessions", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    state.session = session;
    state.turns = session.turns || [];
    state.preview = null;
    state.selectedAction = null;

    renderSession();
    renderPreview();
    renderHistory();
    showToast(`Session ${session.session_id} created`);
  } catch (error) {
    showToast(error.message);
  }
});

elements.loadSessionButton.addEventListener("click", async () => {
  const sessionId = elements.sessionIdInput.value.trim();
  if (!sessionId) {
    showToast("Enter a session ID before loading history.");
    return;
  }

  try {
    await refreshSession(sessionId);
    showToast(`Session ${sessionId} loaded`);
  } catch (error) {
    showToast(error.message);
  }
});

elements.previewForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  if (!state.session) {
    showToast("Create or load a session first.");
    return;
  }

  const message = elements.messageInput.value.trim();
  if (!message) {
    showToast("Enter a message before preview.");
    return;
  }

  try {
    const preview = await requestJson(`/sessions/${encodeURIComponent(state.session.session_id)}/preview`, {
      method: "POST",
      body: JSON.stringify({ message }),
    });

    state.preview = preview;
    state.selectedAction = preview.suggested_action;
    elements.overrideReasonInput.value = "";

    renderPreview();
    showToast("Preview created");
  } catch (error) {
    showToast(error.message);
  }
});

elements.confirmButton.addEventListener("click", async () => {
  if (!state.session || !state.preview || !state.selectedAction) {
    showToast("Create a preview first.");
    return;
  }

  const isOverride = state.selectedAction !== state.preview.suggested_action;
  const overrideReason = elements.overrideReasonInput.value.trim();
  if (isOverride && !overrideReason) {
    showToast("Enter an override reason.");
    return;
  }

  try {
    const result = await requestJson(`/sessions/${encodeURIComponent(state.session.session_id)}/confirm`, {
      method: "POST",
      body: JSON.stringify({
        preview_id: state.preview.preview_id,
        final_action: state.selectedAction,
        ...(overrideReason ? { override_reason: overrideReason } : {}),
      }),
    });

    renderResult(result);
    state.preview = null;
    state.selectedAction = null;
    renderPreview();
    await refreshSession(state.session.session_id);
    showToast(result.blocked ? "Turn blocked" : "Turn sent successfully");
  } catch (error) {
    showToast(error.message);
  }
});

checkHealth();
renderSession();
renderPreview();
renderHistory();
