// Phantom dashboard front-end. Vanilla JS, no build chain, ≤200 LOC.
// Connects to /ws/chat, renders chat history, ships canvas-rendered HTML
// from the server straight into the DOM (the server already escaped it).

(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const conv = $("#conversation");
  const form = $("#composer");
  const input = $("#input");
  const send = $("#send");
  const status = $("#status");

  /** Append a chat row. role: "user" | "assistant" | "system". */
  function addMessage(role, text, html, level) {
    const hint = conv.querySelector(".hint");
    if (hint) hint.remove();

    const row = document.createElement("div");
    row.className = `msg msg-${role}` + (level ? ` level-${level}` : "");
    row.innerHTML = `
      <div class="role">${role}</div>
      <div class="body"></div>
    `;
    const body = row.querySelector(".body");
    if (html) {
      // Server has already HTML-escaped via canvas/render.py.
      body.innerHTML = html;
    } else {
      // Fallback: write text content (always safe).
      body.textContent = text || "";
    }
    conv.appendChild(row);
    conv.scrollTop = conv.scrollHeight;
  }

  /** WebSocket lifecycle with auto-reconnect. */
  let ws = null;
  let reconnectAfter = 1000;

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${location.host}/ws/chat`;
    ws = new WebSocket(url);

    ws.addEventListener("open", () => {
      status.textContent = "connected";
      status.className = "status status-connected";
      send.disabled = false;
      reconnectAfter = 1000;
    });

    ws.addEventListener("close", () => {
      status.textContent = "disconnected";
      status.className = "status status-disconnected";
      send.disabled = true;
      // Exponential backoff up to 30s.
      setTimeout(connect, reconnectAfter);
      reconnectAfter = Math.min(reconnectAfter * 2, 30000);
    });

    ws.addEventListener("error", () => {
      // Browsers fire 'close' after 'error'; let that handler reconnect.
    });

    ws.addEventListener("message", (evt) => {
      let msg;
      try {
        msg = JSON.parse(evt.data);
      } catch {
        addMessage("system", "(invalid JSON from server)", null, "error");
        return;
      }
      switch (msg.type) {
        case "assistant_message":
          addMessage("assistant", msg.text, msg.html);
          break;
        case "system":
          addMessage("system", msg.text, null, msg.level || "info");
          break;
        case "pong":
          break;
        default:
          addMessage("system",
            `(unknown event: ${msg.type})`, null, "warn");
      }
    });
  }

  /** Submit handler. */
  function submit() {
    const text = input.value.trim();
    if (!text) return;
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      addMessage("system",
        "Cannot send — not connected. Reconnecting…", null, "warn");
      return;
    }
    addMessage("user", text);
    ws.send(JSON.stringify({ type: "user_message", text }));
    input.value = "";
    input.style.height = "auto";
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    submit();
  });

  // Cmd/Ctrl-Enter sends; plain Enter inserts a newline.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  });

  // Auto-resize textarea up to max-height set in CSS.
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 192) + "px";
  });

  // Heartbeat — keeps idle connections alive through reverse proxies.
  setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
  }, 25000);

  connect();
})();
