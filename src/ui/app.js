(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const input = $("prompt");
  const messages = $("messages");
  const scrollArea = $("scrollArea");
  const welcome = $("welcome");
  const send = $("send");
  const status = $("status");
  const clearChatButton = $("clearChat");
  const newChatButton = $("newChat");

  // Conversations are held in browser memory. Each page load starts a fresh thread.
  const state = { conversations: [], activeId: null, busy: false, pendingConversationId: null };
  const newId = () => {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  };
  const active = () => state.conversations.find(conversation => conversation.id === state.activeId);
  const escapeHTML = value => String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  })[character]);

  // A small Markdown renderer for common assistant output. Source text is escaped first;
  // protected code content is restored only after Markdown formatting is complete.
  function markdown(source) {
    const protectedHtml = [];
    const protect = html => `\u0000${protectedHtml.push(html) - 1}\u0000`;
    let text = String(source ?? "");

    text = text.replace(/```([\w-]*)[ \t]*\n([\s\S]*?)```/g, (_match, _language, code) => {
      const content = code.endsWith("\n") ? code.slice(0, -1) : code;
      return protect(`<pre><code>${escapeHTML(content)}</code></pre>`);
    });
    text = escapeHTML(text);
    text = text.replace(/`([^`\n]+)`/g, (_match, code) => protect(`<code>${code}</code>`));
    text = text
      .replace(/^### (.+)$/gm, "<h3>$1</h3>")
      .replace(/^## (.+)$/gm, "<h2>$1</h2>")
      .replace(/^# (.+)$/gm, "<h1>$1</h1>")
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.+?)\*/g, "<em>$1</em>")
      .replace(/^\s*[-*] (.+)$/gm, "<li>$1</li>")
      .replace(/(?:<li>.*<\/li>\n?)+/g, list => `<ul>${list}</ul>`);

    return text.split(/\n{2,}/).map(block => {
      block = block.trim();
      if (!block) return "";
      if (/^\u0000\d+\u0000$/.test(block) || /^<(h\d|pre|ul|ol|blockquote)/.test(block)) {
        return block;
      }
      return `<p>${block.replace(/\n/g, "<br>")}</p>`;
    }).join("").replace(/\u0000(\d+)\u0000/g, (_match, index) => protectedHtml[Number(index)] ?? "");
  }

  function renderList() {
    const chatList = $("chatList");
    chatList.innerHTML = "";
    state.conversations.forEach(conversation => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "chat-item" + (conversation.id === state.activeId ? " active" : "");
      button.setAttribute("aria-current", conversation.id === state.activeId ? "page" : "false");
      const clock = document.createElement("span");
      clock.className = "clock";
      clock.setAttribute("aria-hidden", "true");
      clock.textContent = "◷";
      const title = document.createElement("span");
      title.textContent = conversation.title;
      button.append(clock, title);
      button.addEventListener("click", () => {
        state.activeId = conversation.id;
        renderList();
        renderMessages();
        closeMenu();
      });
      chatList.appendChild(button);
    });
  }

  function appendTypingIndicator() {
    const row = document.createElement("article");
    row.className = "message assistant";
    row.id = "typing";
    row.setAttribute("aria-label", "CortexFlow is responding");
    row.innerHTML = '<div class="message-avatar" aria-hidden="true">C</div><div class="message-content"><div class="typing" aria-hidden="true"><i></i><i></i><i></i></div></div>';
    messages.appendChild(row);
  }

  function renderMessages() {
    const conversation = active();
    messages.innerHTML = "";
    welcome.style.display = conversation && conversation.messages.length ? "none" : "block";
    if (!conversation) return;

    conversation.messages.forEach(message => {
      const row = document.createElement("article");
      row.className = `message ${message.role}`;
      const avatar = document.createElement("div");
      avatar.className = "message-avatar";
      avatar.textContent = message.role === "user" ? "You" : "C";
      avatar.setAttribute("aria-hidden", "true");
      const content = document.createElement("div");
      content.className = "message-content";
      content.innerHTML = markdown(message.content);
      row.append(avatar, content);
      messages.appendChild(row);

      if (message.role === "assistant") {
        const actions = document.createElement("div");
        actions.className = "message-actions";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.textContent = "▢ Copy";
        copy.setAttribute("aria-label", "Copy assistant response");
        copy.addEventListener("click", async () => {
          try {
            await navigator.clipboard.writeText(message.content);
            copy.textContent = "✓ Copied";
            window.setTimeout(() => { copy.textContent = "▢ Copy"; }, 1000);
          } catch {
            copy.textContent = "Copy unavailable";
            window.setTimeout(() => { copy.textContent = "▢ Copy"; }, 1400);
          }
        });
        actions.appendChild(copy);
        content.appendChild(actions);
      }
    });

    if (state.busy && state.pendingConversationId === conversation.id) {
      appendTypingIndicator();
    }
    scrollArea.scrollTop = scrollArea.scrollHeight;
  }

  function newConversation() {
    const conversation = { id: newId(), title: "New chat", messages: [] };
    state.conversations.unshift(conversation);
    state.activeId = conversation.id;
    renderList();
    renderMessages();
    closeMenu();
    input.focus();
  }

  function addMessage(conversation, role, content) {
    conversation.messages.push({ role, content: String(content ?? "") });
    if (role === "user" && conversation.title === "New chat") {
      conversation.title = conversation.messages[conversation.messages.length - 1].content.trim().slice(0, 36) || "New chat";
    }
    renderList();
    if (conversation.id === state.activeId) renderMessages();
  }

  function setBusy(value) {
    state.busy = value;
    send.disabled = value;
    input.disabled = value;
    clearChatButton.disabled = value;
    status.textContent = value ? "CortexFlow is working…" : "Enter to send · Shift+Enter for a new line";
  }

  async function submit(text) {
    text = text.trim();
    if (!text || state.busy) return;
    if (!active()) newConversation();

    const conversation = active();
    addMessage(conversation, "user", text);
    input.value = "";
    resize();
    state.pendingConversationId = conversation.id;
    setBusy(true);
    renderMessages();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, conversation_id: conversation.id })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
      addMessage(conversation, "assistant", data.response || "The agent returned no response.");
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      addMessage(conversation, "assistant", `**Request failed.**\n\n${detail}\n\nCheck the FastAPI terminal logs and confirm the MCP servers and graph initialized successfully.`);
    } finally {
      state.pendingConversationId = null;
      setBusy(false);
      renderMessages();
      input.focus();
    }
  }

  function resize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  function closeMenu() {
    $("sidebar").classList.remove("open");
    $("scrim").classList.remove("show");
    $("openSidebar").setAttribute("aria-expanded", "false");
  }

  $("chatForm").addEventListener("submit", event => {
    event.preventDefault();
    submit(input.value);
  });
  input.addEventListener("input", resize);
  input.addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      $("chatForm").requestSubmit();
    }
  });
  document.querySelectorAll(".suggestion").forEach(button => {
    button.addEventListener("click", () => submit(button.dataset.prompt || ""));
  });
  newChatButton.addEventListener("click", newConversation);
  clearChatButton.addEventListener("click", () => {
    if (state.busy) return;
    const conversation = active();
    if (!conversation) return;
    // A new thread ID is required because clearing the UI does not clear the server checkpoint.
    conversation.id = newId();
    conversation.messages = [];
    conversation.title = "New chat";
    state.activeId = conversation.id;
    renderList();
    renderMessages();
    input.focus();
  });
  $("openSidebar").addEventListener("click", () => {
    $("sidebar").classList.add("open");
    $("scrim").classList.add("show");
    $("openSidebar").setAttribute("aria-expanded", "true");
  });
  $("closeSidebar").addEventListener("click", closeMenu);
  $("scrim").addEventListener("click", closeMenu);

  newConversation();
})();
