(() => {
  "use strict";

  const COMMANDS = [
    "Proofread",
    "Improve",
    "Rephrase",
    "Shorten",
    "Friendly",
    "Formal",
    "Professional",
    "Diplomatic",
    "Empathetic",
    "Cooperative",
    "Assertive",
    "Caveman"
  ];
  const INPUT_TYPES = new Set(["text", "search", "email", "url", "tel"]);

  let target = null;
  let capture = null;
  let requestCounter = 0;

  const anchor = document.createElement("div");
  anchor.className = "manuscript-extension-anchor";
  const shadow = anchor.attachShadow({ mode: "closed" });
  const iconButton = document.createElement("button");
  iconButton.type = "button";
  iconButton.className = "manuscript-icon";
  iconButton.setAttribute("aria-label", "Open Manuscript writing review");
  const icon = document.createElement("img");
  icon.src = chrome.runtime.getURL("manuscript.svg");
  icon.alt = "";
  iconButton.append(icon);

  const panel = document.createElement("section");
  panel.className = "manuscript-panel";
  panel.hidden = true;
  panel.setAttribute("aria-label", "Manuscript writing review");

  const title = document.createElement("strong");
  title.textContent = "Manuscript";
  const command = document.createElement("select");
  command.setAttribute("aria-label", "Writing tone");
  for (const value of COMMANDS) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = value;
    command.append(option);
  }
  const status = document.createElement("p");
  status.className = "manuscript-status";
  const original = document.createElement("del");
  original.className = "manuscript-original";
  const suggested = document.createElement("ins");
  suggested.className = "manuscript-suggested";
  suggested.tabIndex = 0;
  const actions = document.createElement("div");
  actions.className = "manuscript-actions";
  const replaceButton = document.createElement("button");
  replaceButton.type = "button";
  replaceButton.textContent = "Replace";
  const dismissButton = document.createElement("button");
  dismissButton.type = "button";
  dismissButton.textContent = "Dismiss";
  actions.append(replaceButton, dismissButton);
  panel.append(title, command, status, original, suggested, actions);
  shadow.append(iconButton, panel);

  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    * { box-sizing: border-box; font-family: "Segoe UI", sans-serif; }
    .manuscript-icon {
      width: 30px; height: 30px; padding: 2px; border-radius: 8px;
      border: 1px solid #E7B85C; background: #17120D; cursor: pointer;
      box-shadow: 0 4px 14px rgba(0,0,0,.35); pointer-events: auto;
    }
    .manuscript-icon img { width: 24px; height: 24px; display: block; }
    .manuscript-panel {
      width: min(380px, calc(100vw - 24px)); max-height: min(520px, calc(100vh - 24px));
      overflow: auto; margin-top: 6px; padding: 14px; border-radius: 10px;
      border: 1px solid #A08A68; background: #211A12; color: #FFF4DE;
      box-shadow: 0 12px 34px rgba(0,0,0,.52); pointer-events: auto;
    }
    .manuscript-panel[hidden], .manuscript-icon[hidden] { display: none; }
    strong { display: block; color: #FFE2A6; font-size: 16px; margin-bottom: 10px; }
    select, button {
      min-height: 36px; border: 1px solid #A08A68; border-radius: 7px;
      background: #2E2418; color: #FFF4DE; padding: 0 10px;
    }
    select { width: 100%; margin-bottom: 10px; }
    button { cursor: pointer; }
    button:focus, select:focus, ins:focus { outline: 2px solid #E7B85C; outline-offset: 2px; }
    .manuscript-status { color: #D5C3A2; margin: 8px 0; font-size: 13px; }
    del, ins {
      display: block; white-space: pre-wrap; overflow-wrap: anywhere;
      padding: 10px; border-radius: 7px; margin: 8px 0; text-decoration-thickness: 2px;
    }
    del { color: #FF8378; background: rgba(255,131,120,.10); }
    ins { color: #72D5A4; background: rgba(114,213,164,.10); text-decoration: none; }
    .manuscript-actions { display: flex; gap: 8px; margin-top: 10px; }
    .manuscript-actions button:first-child {
      background: #E7B85C; border-color: #FFE2A6; color: #1B1207; font-weight: 700;
    }
    .manuscript-actions button:disabled { opacity: .55; cursor: default; }
  `;
  shadow.prepend(style);
  document.documentElement.append(anchor);
  iconButton.hidden = true;

  function isEditable(element) {
    if (!(element instanceof HTMLElement) || element.matches("[disabled], [readonly]")) {
      return false;
    }
    const sensitive = globalThis.ManuscriptFieldSecurity?.isSensitiveFieldDescriptor({
      type: element instanceof HTMLInputElement ? element.type : "",
      autocomplete: element.getAttribute("autocomplete"),
      name: element.getAttribute("name"),
      id: element.id,
      ariaLabel: element.getAttribute("aria-label"),
      placeholder: element.getAttribute("placeholder")
    });
    if (sensitive) {
      return false;
    }
    if (element instanceof HTMLInputElement) {
      const type = (element.type || "text").toLowerCase();
      if (type === "password") {
        return false;
      }
      return INPUT_TYPES.has(type);
    }
    return element instanceof HTMLTextAreaElement || element.isContentEditable;
  }

  function currentText(value = target, snapshot = capture) {
    if (!value || !snapshot) {
      return "";
    }
    if (value instanceof HTMLInputElement || value instanceof HTMLTextAreaElement) {
      return value.value.slice(snapshot.start, snapshot.end);
    }
    if (snapshot.range && snapshot.range.commonAncestorContainer.isConnected) {
      return snapshot.range.toString();
    }
    return value.textContent || "";
  }

  function captureTarget(value) {
    if (value instanceof HTMLInputElement || value instanceof HTMLTextAreaElement) {
      const selectedStart = Number.isInteger(value.selectionStart) ? value.selectionStart : 0;
      const selectedEnd = Number.isInteger(value.selectionEnd) ? value.selectionEnd : 0;
      const hasSelection = selectedEnd > selectedStart;
      return {
        start: hasSelection ? selectedStart : 0,
        end: hasSelection ? selectedEnd : value.value.length,
        range: null,
        canReplace: true
      };
    }
    const selection = document.getSelection();
    if (selection && selection.rangeCount === 1) {
      const range = selection.getRangeAt(0);
      if (!range.collapsed && value.contains(range.commonAncestorContainer)) {
        return { start: 0, end: 0, range: range.cloneRange(), canReplace: true };
      }
    }
    const plainText = value.getAttribute("contenteditable") === "plaintext-only";
    return { start: 0, end: 0, range: null, canReplace: plainText };
  }

  function position() {
    if (!target || !target.isConnected) {
      closeAll();
      return;
    }
    const rect = target.getBoundingClientRect();
    const panelWidth = panel.hidden ? 30 : Math.min(380, window.innerWidth - 24);
    const left = Math.max(8, Math.min(rect.right - 30, window.innerWidth - panelWidth - 8));
    const top = Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 48));
    anchor.style.left = `${left}px`;
    anchor.style.top = `${top}px`;
  }

  function closePanel() {
    panel.hidden = true;
    status.textContent = "";
    original.textContent = "";
    suggested.textContent = "";
    capture = null;
    position();
  }

  function closeAll() {
    closePanel();
    iconButton.hidden = true;
    target = null;
  }

  function showFor(value) {
    if (!isEditable(value)) {
      closeAll();
      return;
    }
    target = value;
    iconButton.hidden = false;
    position();
  }

  function requestCorrection() {
    if (!target || !isEditable(target)) {
      closeAll();
      return;
    }
    capture = captureTarget(target);
    const source = currentText();
    if (!source.trim()) {
      status.textContent = "Enter text first.";
      original.textContent = "";
      suggested.textContent = "";
      panel.hidden = false;
      position();
      return;
    }
    const id = `browser-${Date.now()}-${++requestCounter}`;
    status.textContent = "Proofreading locally…";
    original.textContent = source;
    suggested.textContent = "";
    replaceButton.disabled = true;
    panel.hidden = false;
    position();
    chrome.runtime.sendMessage(
      {
        id,
        type: "analyze",
        text: source,
        command: command.value,
        profile: "General",
        mode: "auto"
      },
      (response) => {
        if (!capture || !target || response?.id !== id) {
          return;
        }
        if (chrome.runtime.lastError || !response?.ok || typeof response.text !== "string") {
          status.textContent = response?.error === "active_subscription_required"
            ? "An active Manuscript subscription is required."
            : "Local correction is unavailable.";
          replaceButton.disabled = true;
          return;
        }
        suggested.textContent = response.text;
        status.textContent = response.message || response.backend || "Review the correction.";
        replaceButton.disabled = !capture.canReplace;
        if (!capture.canReplace) {
          status.textContent += " Select the suggestion and copy it into this rich editor.";
        }
      }
    );
  }

  function applyReplacement() {
    if (!target || !capture || replaceButton.disabled) {
      return;
    }
    const expected = original.textContent || "";
    const replacement = suggested.textContent || "";
    if (!replacement || currentText() !== expected) {
      status.textContent = "The field changed. Run Manuscript again.";
      replaceButton.disabled = true;
      return;
    }
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      target.setRangeText(replacement, capture.start, capture.end, "end");
    } else if (capture.range) {
      capture.range.deleteContents();
      const node = document.createTextNode(replacement);
      capture.range.insertNode(node);
      capture.range.setStartAfter(node);
      capture.range.collapse(true);
      const selection = document.getSelection();
      selection.removeAllRanges();
      selection.addRange(capture.range);
    } else {
      target.textContent = replacement;
    }
    target.dispatchEvent(new InputEvent("input", {
      bubbles: true,
      inputType: "insertReplacementText",
      data: replacement
    }));
    target.dispatchEvent(new Event("change", { bubbles: true }));
    closePanel();
    target.focus();
  }

  iconButton.addEventListener("click", requestCorrection);
  replaceButton.addEventListener("click", applyReplacement);
  dismissButton.addEventListener("click", closePanel);
  command.addEventListener("change", () => {
    if (!panel.hidden) {
      requestCorrection();
    }
  });
  document.addEventListener("focusin", (event) => showFor(event.target), true);
  document.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (panel.hidden && document.activeElement !== target) {
        closeAll();
      }
    }, 100);
  }, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !panel.hidden) {
      closePanel();
      target?.focus();
    }
  }, true);
  window.addEventListener("scroll", position, true);
  window.addEventListener("resize", position);
})();
