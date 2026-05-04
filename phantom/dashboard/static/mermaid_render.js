/* Phantom dashboard — mermaid renderer.
 *
 * Scans newly-injected assistant messages for fenced ```mermaid blocks
 * and renders them in place. We use a MutationObserver so the renderer
 * reacts to the WebSocket-driven message inserts without coupling to
 * main.js's render path.
 *
 * Security: mermaid is initialised with `securityLevel: 'strict'` (no
 * inline HTML evaluation) in index.html. This file only walks the DOM.
 */

(function () {
  "use strict";

  let counter = 0;

  function renderBlock(pre) {
    if (!window.mermaid || !window.__phantom_mermaid_loaded) return;
    if (pre.dataset.phantomMermaid === "rendered") return;
    pre.dataset.phantomMermaid = "rendered";
    const code = pre.textContent || "";
    const id = "phantom-mmd-" + (++counter);
    const host = document.createElement("div");
    host.className = "mermaid-host";
    host.id = id;
    pre.parentNode.replaceChild(host, pre);
    try {
      window.mermaid
        .render(id + "-svg", code)
        .then(({ svg }) => {
          host.innerHTML = svg;
        })
        .catch((err) => {
          host.innerHTML =
            '<pre class="mermaid-error">mermaid render failed: ' +
            String(err).replace(/[<>&]/g, (c) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c])) +
            "</pre>";
        });
    } catch (err) {
      host.textContent = "mermaid render failed: " + String(err);
    }
  }

  function scan(root) {
    const blocks = root.querySelectorAll(
      'pre code.language-mermaid, pre.language-mermaid, pre[data-lang="mermaid"]'
    );
    blocks.forEach((node) => {
      const pre = node.tagName === "PRE" ? node : node.parentNode;
      if (pre && pre.tagName === "PRE") renderBlock(pre);
    });
  }

  function start() {
    scan(document);
    const target = document.getElementById("conversation") || document.body;
    const obs = new MutationObserver((muts) => {
      for (const m of muts) {
        m.addedNodes.forEach((n) => {
          if (n.nodeType === 1) scan(n);
        });
      }
    });
    obs.observe(target, { childList: true, subtree: true });
    // expose for tests
    window.__phantom_mermaid_scan = scan;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
