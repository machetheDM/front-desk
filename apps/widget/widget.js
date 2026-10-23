/*
 * Front Desk website chat widget. No build step, no dependencies, no cookies.
 *
 *   <script src="https://<cdn>/widget/widget.js" data-key="fdk_live_..." data-api="https://..." data-ws="wss://..." defer></script>
 *
 * The site's own Content-Security-Policy must allow script-src for the CDN and connect-src for
 * the API and WebSocket URLs. The visitor's session id is random and kept in localStorage so a
 * conversation continues across pages; nothing identifies the visitor unless they say who they are.
 */
(function () {
  "use strict";
  var me = document.currentScript;
  if (!me) return;
  var KEY = me.getAttribute("data-key"), API = me.getAttribute("data-api"), WS = me.getAttribute("data-ws");
  var TITLE = me.getAttribute("data-title") || "Ask us";

  function sessionId() {
    try {
      var s = localStorage.getItem("fd_session");
      if (!s) {
        s = (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now()).replace(/-/g, "");
        localStorage.setItem("fd_session", s);
      }
      return s;
    } catch (e) {
      return "nostorage" + Date.now() + Math.floor(Math.random() * 1e9);
    }
  }

  function el(tag, attrs, text) {
    var e = document.createElement(tag);
    for (var k in attrs || {}) e.setAttribute(k, attrs[k]);
    if (text) e.textContent = text; // textContent, never innerHTML: replies are data
    return e;
  }

  var root = el("div", { style: "position:fixed;right:16px;bottom:16px;z-index:2147483000;font:14px system-ui,sans-serif" });
  var button = el("button", { type: "button", "aria-expanded": "false", style: "padding:10px 14px;border-radius:20px;border:0;background:#1f6f50;color:#fff;cursor:pointer" }, TITLE);
  var panel = el("div", { role: "dialog", "aria-label": TITLE, style: "display:none;width:min(340px,calc(100vw - 32px));height:420px;background:#fff;color:#1d1d1b;border:1px solid #ddd;border-radius:10px;margin-bottom:8px;flex-direction:column;overflow:hidden" });
  var log = el("div", { "aria-live": "polite", style: "flex:1;overflow:auto;padding:10px" });
  var form = el("form", { style: "display:flex;border-top:1px solid #eee" });
  var input = el("input", { type: "text", maxlength: "1000", "aria-label": "Your message", placeholder: "Type your question", style: "flex:1;border:0;padding:10px;font:inherit" });
  var send = el("button", { type: "submit", style: "border:0;background:#1f6f50;color:#fff;padding:0 14px" }, "Send");
  form.appendChild(input); form.appendChild(send);
  panel.appendChild(log); panel.appendChild(form);
  root.appendChild(panel); root.appendChild(button);
  document.body.appendChild(root);

  function bubble(who, text) {
    var b = el("div", { style: "margin:6px 0;padding:8px 10px;border-radius:8px;white-space:pre-wrap;max-width:85%;" + (who === "me" ? "margin-left:auto;background:#e7f3ee" : "background:#f2f2ef") }, text);
    log.appendChild(b); log.scrollTop = log.scrollHeight;
    return b;
  }

  button.addEventListener("click", function () {
    var open = panel.style.display === "none";
    panel.style.display = open ? "flex" : "none";
    button.setAttribute("aria-expanded", String(open));
    if (open) input.focus();
  });

  var socket = null;
  function viaSocket(text) {
    // Progress streaming (ADR 0009): status first, then the validated answer in parts.
    return new Promise(function (resolve, reject) {
      if (!WS || !("WebSocket" in window)) return reject(new Error("no ws"));
      var current = null;
      function onMessage(ev) {
        var m = JSON.parse(ev.data);
        if (m.type === "status") { current = current || bubble("them", m.text); }
        else if (m.type === "answer_part") {
          if (!current || current.dataset.status !== "answer") { current = current || bubble("them", ""); current.textContent = ""; current.dataset.status = "answer"; }
          current.textContent += (current.textContent ? " " : "") + m.text;
        } else if (m.type === "done" || m.type === "error") { socket.removeEventListener("message", onMessage); resolve(); }
      }
      function go() {
        socket.addEventListener("message", onMessage);
        socket.send(JSON.stringify({ action: "ask", key: KEY, session: sessionId(), message: text }));
      }
      if (socket && socket.readyState === 1) return go();
      socket = new WebSocket(WS + "?key=" + encodeURIComponent(KEY));
      socket.addEventListener("open", go, { once: true });
      socket.addEventListener("error", function () { reject(new Error("ws failed")); }, { once: true });
    });
  }

  function viaHttp(text) {
    var waiting = bubble("them", "...");
    return fetch(API + "/v1/chat", {
      method: "POST",
      headers: { "content-type": "application/json", "x-frontdesk-widget-key": KEY },
      body: JSON.stringify({ session: sessionId(), message: text })
    }).then(function (r) { return r.json(); }).then(function (j) { waiting.textContent = j.reply || "Thanks, someone will reply here."; });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    bubble("me", text);
    viaSocket(text).catch(function () { return viaHttp(text); }).catch(function () { bubble("them", "Sorry, we could not send that. Please try again."); });
  });
})();
