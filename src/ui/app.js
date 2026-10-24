(() => {
  const $ = id => document.getElementById(id);
  const input = $("prompt"), messages = $("messages"), scrollArea = $("scrollArea");
  const welcome = $("welcome"), send = $("send"), status = $("status");
  // No transcript is restored from disk. Each new page load starts a fresh thread.
  const state = { conversations: [], activeId: null, busy: false };
  const newId = () => crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const active = () => state.conversations.find(c => c.id === state.activeId);
  const escapeHTML = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  // Safe, intentionally small Markdown renderer for common LLM output. HTML is escaped first.
  function markdown(source) {
    let s = escapeHTML(source);
    s = s.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_, lang, code) => `<pre><code>${code.trim()}</code></pre>`);
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>').replace(/^## (.+)$/gm, '<h2>$1</h2>').replace(/^# (.+)$/gm, '<h1>$1</h1>');
    s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/\*(.+?)\*/g, '<em>$1</em>');
    s = s.replace(/^\s*[-*] (.+)$/gm, '<li>$1</li>').replace(/(?:<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`);
    return s.split(/\n{2,}/).map(block => {
      block = block.trim();
      if (!block) return '';
      if (/^<(h\d|pre|ul|ol)/.test(block)) return block;
      return `<p>${block.replace(/\n/g, '<br>')}</p>`;
    }).join('');
  }
  function renderList() {
    $("chatList").innerHTML = "";
    state.conversations.forEach(c => {
      const b = document.createElement("button");
      b.className = "chat-item" + (c.id === state.activeId ? " active" : "");
      b.innerHTML = `<span class="clock">◷</span><span>${escapeHTML(c.title)}</span>`;
      b.onclick = () => { state.activeId = c.id; renderList(); renderMessages(); closeMenu(); };
      $("chatList").appendChild(b);
    });
  }
  function renderMessages() {
    const c = active(); messages.innerHTML = "";
    welcome.style.display = c && c.messages.length ? "none" : "block";
    if (!c) return;
    c.messages.forEach(m => {
      const row = document.createElement("article"); row.className = `message ${m.role}`;
      const avatar = document.createElement("div"); avatar.className = "message-avatar"; avatar.textContent = m.role === "user" ? "You" : "C";
      const content = document.createElement("div"); content.className = "message-content"; content.innerHTML = markdown(m.content);
      row.append(avatar, content); messages.appendChild(row);
      if (m.role === "assistant") {
        const actions = document.createElement("div"); actions.className = "message-actions";
        const copy = document.createElement("button"); copy.textContent = "▢ Copy";
        copy.onclick = async () => { try { await navigator.clipboard.writeText(m.content); copy.textContent = "✓ Copied"; setTimeout(() => copy.textContent = "▢ Copy", 1000); } catch {} };
        actions.appendChild(copy); content.appendChild(actions);
      }
    });
    scrollArea.scrollTop = scrollArea.scrollHeight;
  }
  function newConversation() {
    const c = { id: newId(), title: "New chat", messages: [] };
    state.conversations.unshift(c); state.activeId = c.id; renderList(); renderMessages(); input.focus();
  }
  function addMessage(role, content) {
    const c = active(); c.messages.push({role, content});
    if (role === "user" && c.title === "New chat") c.title = content.trim().slice(0, 36) || "New chat";
    renderList(); renderMessages();
  }
  function setBusy(value) { state.busy = value; send.disabled = value; input.disabled = value; status.textContent = value ? "CortexFlow is working…" : "Enter to send · Shift+Enter for a new line"; }
  function showTyping() {
    const row = document.createElement("article"); row.className = "message assistant"; row.id = "typing";
    row.innerHTML = '<div class="message-avatar">C</div><div class="message-content"><div class="typing"><i></i><i></i><i></i></div></div>';
    messages.appendChild(row); scrollArea.scrollTop = scrollArea.scrollHeight;
  }
  async function submit(text) {
    text = text.trim(); if (!text || state.busy) return;
    if (!active()) newConversation();
    const c = active(); addMessage("user", text); input.value = ""; resize(); setBusy(true); showTyping();
    try {
      const response = await fetch("/api/chat", {
        method: "POST", headers: {"Content-Type":"application/json"},
        body: JSON.stringify({message:text, conversation_id:c.id})
      });
      $("typing")?.remove();
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      addMessage("assistant", data.response || "The agent returned no response.");
    } catch (err) {
      $("typing")?.remove();
      addMessage("assistant", `**Request failed.**\n\n${err.message}\n\nCheck the FastAPI terminal logs and confirm the MCP servers and graph initialized successfully.`);
    } finally { setBusy(false); input.focus(); }
  }
  function resize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 180) + "px"; }
  function closeMenu() { $("sidebar").classList.remove("open"); $("scrim").classList.remove("show"); }
  $("chatForm").addEventListener("submit", e => { e.preventDefault(); submit(input.value); });
  input.addEventListener("input", resize);
  input.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("chatForm").requestSubmit(); } });
  document.querySelectorAll(".suggestion").forEach(b => b.addEventListener("click", () => submit(b.dataset.prompt)));
  $("newChat").onclick = newConversation;
  $("clearChat").onclick = () => { const c = active(); if (c) { c.messages = []; c.title = "New chat"; renderList(); renderMessages(); } };
  $("openSidebar").onclick = () => { $("sidebar").classList.add("open"); $("scrim").classList.add("show"); };
  $("closeSidebar").onclick = closeMenu; $("scrim").onclick = closeMenu;
  newConversation();
})();