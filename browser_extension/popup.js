const BRIDGE = "http://127.0.0.1:8766";

const statusEl = document.getElementById("status");
const sourceTextEl = document.getElementById("sourceText");
const resultTextEl = document.getElementById("resultText");
const loadBtn = document.getElementById("loadBtn");
const correctBtn = document.getElementById("correctBtn");
const copyBtn = document.getElementById("copyBtn");
const applyBtn = document.getElementById("applyBtn");

let lastPayload = null;

function setStatus(message) {
  statusEl.textContent = message || "";
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function sendToActiveTab(message) {
  const tab = await activeTab();
  if (!tab || !tab.id) {
    throw new Error("Active tab was not found.");
  }
  return chrome.tabs.sendMessage(tab.id, {
    source: "writing-assistant-popup",
    ...message
  });
}

async function loadFocusedText() {
  setStatus("Reading the focused text field...");
  const response = await sendToActiveTab({ type: "getFocusedText" });
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "Could not read the focused text field.");
  }
  lastPayload = response.payload;
  sourceTextEl.value = lastPayload.text || "";
  setStatus(`${targetLabel(lastPayload.target_kind)} text loaded.`);
  return lastPayload;
}

async function correctText() {
  let text = sourceTextEl.value.trim();
  if (!text) {
    const payload = await loadFocusedText();
    text = String(payload.text || "").trim();
  }
  if (!text) {
    throw new Error("No text to correct.");
  }

  setStatus("Correcting...");
  const result = await correctViaBridge(text);
  resultTextEl.value = result.corrected || text;
  const source = result.source === "desktop" ? "Desktop correction" : "Dummy correction";
  const issues = result.issues ? ` ${result.issues}` : "";
  setStatus(`${source} complete.${issues}`.trim());
}

async function correctViaBridge(text) {
  try {
    const response = await fetch(`${BRIDGE}/correct`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    if (data && data.ok) {
      return data;
    }
    throw new Error((data && data.error) || "Correction failed.");
  } catch (_error) {
    return {
      ok: true,
      corrected: dummyCorrect(text),
      issues: "The desktop app bridge was unavailable, so popup dummy correction was used.",
      source: "dummy"
    };
  }
}

async function copyResult() {
  const text = resultTextEl.value.trim();
  if (!text) {
    throw new Error("No correction result to copy.");
  }
  await navigator.clipboard.writeText(text);
  setStatus("Correction result copied to the clipboard.");
}

async function applyResult() {
  const text = resultTextEl.value.trim();
  if (!text) {
    throw new Error("No correction result to apply.");
  }
  const response = await sendToActiveTab({
    type: "applyText",
    text,
    style_info: lastPayload || {}
  });
  if (!response || !response.ok) {
    throw new Error((response && response.error) || "Could not apply the result to this site.");
  }
  setStatus("Correction result applied to the current field.");
}

function dummyCorrect(text) {
  const replacements = new Map([
    ["\uc548\ub400", "\uc548 \ub41c"],
    ["\uc548\ub418", "\uc548 \ub3fc"],
    ["\ub42c", "\ub410"],
    ["\ub418\uc694", "\ub3fc\uc694"],
    ["\uc660\ub9cc", "\uc6ec\ub9cc"],
    ["\uc5b4\uc758", "\uc5b4\uc774"],
    ["\ub9de\ucda4\ubee1", "\ub9de\ucda4\ubc95"]
  ]);
  let corrected = String(text || "");
  for (const [wrong, right] of replacements.entries()) {
    corrected = corrected.split(wrong).join(right);
  }
  return corrected;
}

function targetLabel(kind) {
  if (kind === "textarea") return "textarea";
  if (kind === "input") return "input";
  if (kind === "contenteditable") return "contenteditable";
  return "field";
}

function wireButton(button, handler) {
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await handler();
    } catch (error) {
      setStatus(error && error.message ? error.message : "The action failed.");
    } finally {
      button.disabled = false;
    }
  });
}

wireButton(loadBtn, loadFocusedText);
wireButton(correctBtn, correctText);
wireButton(copyBtn, copyResult);
wireButton(applyBtn, applyResult);

loadFocusedText().catch(() => {
  setStatus("Click a web text field, then press Load.");
});
