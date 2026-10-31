(() => {
  "use strict";

  const $ = id => document.getElementById(id);
  const input = $("prompt");
  const messages = $("messages");
  const scrollArea = $("scrollArea");
  const welcome = $("welcome");
  const send = $("send");
  const status = $("status");
  const composerArea = $("composerArea");
  const composer = $("composer");
  const newChatButton = $("newChat");
  const fileInput = $("fileInput");
  const attachmentChips = $("attachmentChips");
  const MAX_API_MESSAGE = 20000;
  const MAX_ATTACHMENT_CHARS = 14500;
  const MAX_FILE_CHARS = 12000;
  const MAX_LIBRARY_FILES = 16;

  const state = {
    conversations: [], // Recent conversation summaries loaded from PostgreSQL.
    activeConversation: null, // Only the currently open transcript is held in browser memory.
    activeId: null,
    busy: false,
    pendingConversationId: null,
    view: "chat",
    selectedFiles: [],
    libraryFiles: [], // Session-only file list; conversation persistence is server-side.
    searchOpen: false,
    expanded: false,
    recognition: null,
    recording: false,
    listRequestId: 0
  };
  let listSearchTimer = null;

  const newId = () => {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  };
  const active = () => state.activeConversation;
  const escapeHTML = value => String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
  const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;

  async function apiJSON(url, options = {}) {
    const response = await fetch(url, { cache: "no-store", ...options });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  }

  async function refreshConversations(query = $("chatSearch").value.trim()) {
    const requestId = ++state.listRequestId;
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    try {
      const queryString = params.toString();
      const data = await apiJSON(`/api/conversations${queryString ? `?${queryString}` : ""}`);
      if (requestId !== state.listRequestId) return;
      state.conversations = Array.isArray(data) ? data : [];
      const activeSummary = state.conversations.find(item => item.thread_id === state.activeId);
      if (activeSummary && state.activeConversation) state.activeConversation.title = activeSummary.title;
      renderList();
    } catch (error) {
      if (requestId === state.listRequestId) {
        console.error("Could not load CortexFlow conversations.", error);
        showStatus(`Could not load conversations: ${error.message}`);
      }
    }
  }

  async function selectConversation(id) {
    if (!id || (state.busy && id !== state.activeId)) return;
    try {
      const data = await apiJSON(`/api/conversations/${encodeURIComponent(id)}/messages`);
      state.activeId = data.conversation.thread_id;
      state.activeConversation = {
        id: data.conversation.thread_id,
        title: data.conversation.title,
        messages: (data.messages || []).map(message => ({ role: message.role, content: message.content }))
      };
      showView("chat");
      renderList();
      renderMessages();
      closeMobileMenu();
      input.focus();
    } catch (error) {
      showStatus(`Could not open conversation: ${error.message}`);
    }
  }

  // A small Markdown renderer for common assistant output. User/source text is escaped first.
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
      if (/^\u0000\d+\u0000$/.test(block) || /^<(h\d|pre|ul|ol|blockquote)/.test(block)) return block;
      return `<p>${block.replace(/\n/g, "<br>")}</p>`;
    }).join("").replace(/\u0000(\d+)\u0000/g, (_match, index) => protectedHtml[Number(index)] ?? "");
  }

  function renderList() {
    const chatList = $("chatList");
    const query = $("chatSearch").value.trim();
    chatList.innerHTML = "";
    const conversations = state.conversations;
    conversations.forEach(conversation => {
      const id = conversation.thread_id;
      const row = document.createElement("div");
      row.className = "chat-item-row" + (id === state.activeId && state.view === "chat" ? " active" : "");

      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "chat-item" + (id === state.activeId && state.view === "chat" ? " active" : "");
      openButton.setAttribute("aria-current", id === state.activeId && state.view === "chat" ? "page" : "false");
      openButton.innerHTML = `${icon("chat")}<span></span>`;
      openButton.querySelector("span").textContent = conversation.title || "New chat";
      openButton.title = conversation.title || "New chat";
      openButton.addEventListener("click", () => selectConversation(id));

      const menuHost = document.createElement("div");
      menuHost.className = "thread-menu-host";
      const menuButton = document.createElement("button");
      menuButton.type = "button";
      menuButton.className = "thread-menu-button";
      menuButton.setAttribute("aria-label", `Options for ${conversation.title || "New chat"}`);
      menuButton.setAttribute("aria-haspopup", "menu");
      menuButton.setAttribute("aria-expanded", "false");
      menuButton.innerHTML = icon("more");
      const menu = document.createElement("div");
      menu.className = "thread-menu";
      menu.setAttribute("role", "menu");
      menu.hidden = true;
      const renameButton = document.createElement("button");
      renameButton.type = "button";
      renameButton.setAttribute("role", "menuitem");
      renameButton.innerHTML = `${icon("pen")}<span>Rename</span>`;
      renameButton.addEventListener("click", async event => {
        event.stopPropagation();
        closeThreadMenus();
        const title = window.prompt("Rename conversation", conversation.title || "New chat");
        if (title === null) return;
        const cleanTitle = title.trim();
        if (!cleanTitle) { showStatus("Conversation title cannot be empty."); return; }
        try {
          const updated = await apiJSON(`/api/conversations/${encodeURIComponent(id)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: cleanTitle })
          });
          conversation.title = updated.title;
          if (state.activeConversation?.id === id) state.activeConversation.title = updated.title;
          await refreshConversations();
          renderList();
        } catch (error) {
          showStatus(`Could not rename conversation: ${error.message}`);
        }
      });
      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "thread-delete-action";
      deleteButton.setAttribute("role", "menuitem");
      deleteButton.innerHTML = `${icon("close")}<span>Delete</span>`;
      deleteButton.addEventListener("click", async event => {
        event.stopPropagation();
        closeThreadMenus();
        if (state.busy && state.activeId === id) {
          showStatus("Wait for the current response to finish before deleting this conversation.");
          return;
        }
        if (!window.confirm(`Delete “${conversation.title || "New chat"}” permanently?`)) return;
        try {
          await apiJSON(`/api/conversations/${encodeURIComponent(id)}`, { method: "DELETE" });
          if (state.activeId === id) {
            state.activeId = null;
            state.activeConversation = null;
          }
          await refreshConversations();
          renderMessages();
          renderList();
        } catch (error) {
          showStatus(`Could not delete conversation: ${error.message}`);
        }
      });
      menu.append(renameButton, deleteButton);
      menuHost.append(menuButton, menu);
      menuButton.addEventListener("click", event => {
        event.stopPropagation();
        const wasOpen = !menu.hidden;
        closeThreadMenus();
        menu.hidden = wasOpen;
        menuButton.setAttribute("aria-expanded", String(!wasOpen));
      });
      row.append(openButton, menuHost);
      chatList.appendChild(row);
    });
    $("noSearchResults").hidden = !(state.searchOpen && query && conversations.length === 0);
  }

  function addTypingIndicator() {
    const row = document.createElement("article");
    row.className = "message assistant";
    row.id = "typing";
    row.setAttribute("aria-label", "CortexFlow is responding");
    row.innerHTML = '<div class="message-content"><div class="typing" aria-hidden="true"><i></i><i></i><i></i></div></div>';
    messages.appendChild(row);
  }

  function renderMessages() {
    const conversation = active();
    messages.innerHTML = "";
    const hasMessages = Boolean(conversation && conversation.messages.length);
    welcome.style.display = hasMessages ? "none" : "block";
    if (conversation) {
      conversation.messages.forEach(message => {
        const row = document.createElement("article");
        row.className = `message ${message.role}`;
        const content = document.createElement("div");
        content.className = "message-content";
        content.innerHTML = markdown(message.content);
        row.appendChild(content);
        messages.appendChild(row);

        if (message.role === "assistant") {
          const actions = document.createElement("div");
          actions.className = "message-actions";
          const copy = document.createElement("button");
          copy.type = "button";
          copy.innerHTML = `${icon("file")}<span>Copy</span>`;
          copy.setAttribute("aria-label", "Copy assistant response");
          copy.addEventListener("click", async () => {
            try {
              await navigator.clipboard.writeText(message.content);
              copy.querySelector("span").textContent = "Copied";
            } catch {
              copy.querySelector("span").textContent = "Copy unavailable";
            }
            window.setTimeout(() => { if (copy.isConnected) copy.querySelector("span").textContent = "Copy"; }, 1200);
          });
          actions.appendChild(copy);
          content.appendChild(actions);
        }
      });
    }
    if (state.busy && state.pendingConversationId === conversation?.id) addTypingIndicator();
    if (state.view === "chat") requestAnimationFrame(() => { scrollArea.scrollTop = scrollArea.scrollHeight; });
  }

  async function newConversation() {
    if (state.busy) return;
    // Keep a new chat as an in-memory draft until its first message is sent.
    // This avoids inserting empty conversations that never become real threads.
    state.activeId = null;
    state.activeConversation = { id: null, title: "New chat", messages: [] };
    showView("chat");
    renderList();
    renderMessages();
    closeMenus();
    closeMobileMenu();
    input.focus();
  }

  function addMessage(conversation, role, content) {
    conversation.messages.push({ role, content: String(content ?? "") });
    renderList();
    if (conversation.id === state.activeId && state.view === "chat") renderMessages();
  }

  function setBusy(value) {
    state.busy = value;
    send.disabled = value;
    input.disabled = value;
    $("attachButton").disabled = value;
    status.textContent = value ? "CortexFlow is working…" : "Enter to send · Shift+Enter for a new line";
  }

  function showStatus(message, duration = 3200) {
    const previous = status.textContent;
    status.textContent = message;
    status.classList.add("status-message");
    window.setTimeout(() => {
      if (!state.busy && status.textContent === message) status.textContent = previous.startsWith("CortexFlow is working") ? "Enter to send · Shift+Enter for a new line" : previous;
      status.classList.remove("status-message");
    }, duration);
  }

  function selectedContext() {
    if (!state.selectedFiles.length) return "";
    return state.selectedFiles.map(file => `\n\nAttached text file: ${file.name}\n--- file content start ---\n${file.content}\n--- file content end ---`).join("");
  }

  async function submit(value) {
    const typedText = String(value ?? "").trim();
    if ((!typedText && !state.selectedFiles.length) || state.busy) return;
    if (!active() || state.view !== "chat") await newConversation();
    const conversation = active();
    if (!conversation) return;

    const attachments = state.selectedFiles.slice();
    const visibleText = typedText || `Please review ${attachments.length === 1 ? "the attached file" : "the attached files"}.`;
    const attachmentNames = attachments.length ? `\n\nAttached: ${attachments.map(file => file.name).join(", ")}` : "";
    const transcriptContent = `${visibleText}${attachmentNames}`;
    const apiMessage = `${visibleText}${selectedContext()}`;
    if (apiMessage.length > MAX_API_MESSAGE) {
      showStatus("Message and attachments exceed the 20,000-character API limit.");
      return;
    }

    if (!conversation.id) {
      try {
        const created = await apiJSON("/api/conversations", { method: "POST" });
        conversation.id = created.thread_id;
        conversation.title = created.title || "New chat";
        state.activeId = created.thread_id;
      } catch (error) {
        showStatus(`Could not create a conversation: ${error.message}`);
        return;
      }
    }

    addMessage(conversation, "user", transcriptContent);
    input.value = "";
    resizeInput();
    state.selectedFiles = [];
    renderAttachmentChips();
    state.pendingConversationId = conversation.id;
    setBusy(true);
    renderMessages();
    showView("chat");

    try {
      const data = await apiJSON(`/api/conversations/${encodeURIComponent(conversation.id)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: apiMessage, display_content: transcriptContent })
      });
      addMessage(conversation, "assistant", data.response || "The agent returned no response.");
      if (data.title && state.activeConversation?.id === conversation.id) {
        state.activeConversation.title = data.title;
      }
      await refreshConversations();
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      addMessage(conversation, "assistant", `**Request failed.**\n\n${detail}\n\nConfirm PostgreSQL is available, the FastAPI server is running, required API keys are configured, and the graph initialized successfully.`);
      // The user turn may already have been saved. Refresh the transcript when possible.
      try {
        const data = await apiJSON(`/api/conversations/${encodeURIComponent(conversation.id)}/messages`);
        const persistedMessages = (data.messages || []).map(message => ({ role: message.role, content: message.content }));
        conversation.title = data.conversation.title;
        // Prefer persisted content when the assistant response was saved; otherwise keep the local error visible.
        if (persistedMessages.length >= conversation.messages.length) {
          conversation.messages = persistedMessages;
        }
        renderMessages();
      } catch { /* Keep the local error message visible if the API is still unavailable. */ }
      await refreshConversations();
    } finally {
      state.pendingConversationId = null;
      setBusy(false);
      renderMessages();
      renderList();
      input.focus();
    }
  }

  function resizeInput() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, state.expanded ? 430 : 190)}px`;
  }

  function showView(view) {
    state.view = view;
    document.querySelectorAll("[data-view]").forEach(button => {
      button.classList.toggle("active", button.dataset.view === view);
      if (button.dataset.view) button.setAttribute("aria-current", button.dataset.view === view ? "page" : "false");
    });
    const isChat = view === "chat";
    welcome.hidden = !isChat;
    messages.hidden = !isChat;
    composerArea.hidden = !isChat;
    const page = $("workspacePage");
    page.hidden = isChat;
    if (!isChat) renderWorkspacePage(view);
    else renderMessages();
    renderList();
    closeMenus();
  }

  function renderWorkspacePage(view) {
    const page = $("workspacePage");
    if (view !== "library") {
      page.innerHTML = "";
      return;
    }
    page.innerHTML = `<p class="page-eyebrow">Your files</p><h1>Library</h1><p class="page-description">Text and code files added during this browser session can be attached as context to a chat. Files are not uploaded to a file service and are not retained after the page is reloaded.</p>${state.libraryFiles.length ? `<div class="page-grid">${state.libraryFiles.map(file => `<article class="page-card library-card"><div class="library-file-icon">${icon("file")}</div><strong>${escapeHTML(file.name)}</strong><span>${Number(file.size).toLocaleString()} bytes · session only</span><button class="page-primary-button" type="button" data-library-attach="${escapeHTML(file.id)}">Attach to a chat</button></article>`).join("")}</div>` : `<div class="empty-state">${icon("library")}<strong>Your library is empty</strong><p>Add a text or code file using the plus button in a chat. The file list is kept only for this browser session.</p><button class="page-primary-button" type="button" data-go-chat="true">Start a chat</button></div>`}`;
  }

  function renderAttachmentChips() {
    attachmentChips.innerHTML = "";
    attachmentChips.hidden = state.selectedFiles.length === 0;
    state.selectedFiles.forEach(file => {
      const chip = document.createElement("div");
      chip.className = "attachment-chip";
      chip.innerHTML = `${icon("file")}<span></span><button type="button" aria-label="Remove ${escapeHTML(file.name)}">${icon("close")}</button>`;
      chip.querySelector("span").textContent = file.name;
      chip.querySelector("button").addEventListener("click", () => {
        state.selectedFiles = state.selectedFiles.filter(item => item.id !== file.id);
        renderAttachmentChips();
      });
      attachmentChips.appendChild(chip);
    });
  }

  async function chooseFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    let currentChars = state.selectedFiles.reduce((sum, file) => sum + file.content.length, 0);
    for (const file of files) {
      if (state.selectedFiles.some(item => item.name === file.name)) continue;
      if (file.size > 256 * 1024) {
        showStatus(`${file.name}: file is over 256 KB and was skipped.`);
        continue;
      }
      try {
        const content = await file.text();
        if (content.length > MAX_FILE_CHARS) {
          showStatus(`${file.name}: text exceeds the 12,000-character per-file limit.`);
          continue;
        }
        if (currentChars + content.length > MAX_ATTACHMENT_CHARS) {
          showStatus("Attached files exceed the context limit. Remove a file and try again.");
          break;
        }
        const entry = { id: newId(), name: file.name, size: file.size, content, updatedAt: Date.now() };
        state.selectedFiles.push(entry);
        currentChars += content.length;
        // File content is held in memory for this page session; it is not persisted to storage.
        state.libraryFiles = [entry, ...state.libraryFiles.filter(saved => saved.name !== entry.name)].slice(0, MAX_LIBRARY_FILES);
      } catch {
        showStatus(`${file.name}: could not read this file as text.`);
      }
    }
    renderAttachmentChips();
    renderWorkspacePageIfOpen();
    closeMenus();
    fileInput.value = "";
    input.focus();
  }

  function renderWorkspacePageIfOpen() {
    if (state.view === "library") renderWorkspacePage("library");
  }

  function closeMobileMenu() {
    $("sidebar").classList.remove("open");
    $("scrim").classList.remove("show");
  }

  function closeMenus() {
    $("moreMenu").hidden = true;
    $("moreButton").setAttribute("aria-expanded", "false");
    $("attachMenu").hidden = true;
    $("attachButton").setAttribute("aria-expanded", "false");
  }

  function toggleMenu(menu, trigger) {
    const shouldOpen = menu.hidden;
    closeMenus();
    menu.hidden = !shouldOpen;
    trigger.setAttribute("aria-expanded", String(shouldOpen));
  }

  async function checkConnection() {
    const statusBox = $("connectionStatus");
    const dot = $("moreMenu").querySelector(".menu-status-dot");
    statusBox.hidden = false;
    statusBox.textContent = "Checking API…";
    dot.style.background = "#c5a86b";
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      const data = await response.json();
      const healthy = response.ok && data.status === "ok";
      statusBox.textContent = healthy
        ? "API connected; PostgreSQL and agent graph are ready."
        : `API status: ${data.status || response.status}; database: ${data.database || "unknown"}; graph: ${data.graph || "unknown"}. Check server logs, database settings, and API keys.`;
      dot.style.background = healthy ? "#27b980" : "#c5a86b";
    } catch {
      statusBox.textContent = "API unavailable. Start the FastAPI server and reload this page.";
      dot.style.background = "#d86b6b";
    }
  }


  function toggleSearch() {
    state.searchOpen = !state.searchOpen;
    $("searchWrap").hidden = !state.searchOpen;
    if (!state.searchOpen) {
      $("chatSearch").value = "";
      refreshConversations("");
    } else {
      $("chatSearch").focus();
    }
    renderList();
  }

  function startDictation() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      showStatus("Voice input is not supported in this browser. Try a current Chromium-based browser.");
      return;
    }
    if (state.recording && state.recognition) {
      state.recognition.stop();
      return;
    }
    const recognition = new SpeechRecognition();
    recognition.lang = navigator.language || "en-US";
    recognition.interimResults = true;
    recognition.continuous = false;
    state.recognition = recognition;
    state.recording = true;
    $("voiceButton").classList.add("recording");
    $("voiceButton").setAttribute("aria-label", "Stop dictation");
    showStatus("Listening… speak now.", 10000);
    let finalTranscript = "";
    const textBeforeDictation = input.value.trim();
    recognition.onresult = event => {
      let interimTranscript = "";
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = event.results[index][0].transcript;
        if (event.results[index].isFinal) finalTranscript += transcript;
        else interimTranscript += transcript;
      }
      const transcript = `${finalTranscript}${interimTranscript}`.trim();
      input.value = `${textBeforeDictation}${textBeforeDictation && transcript ? " " : ""}${transcript}`;
      resizeInput();
    };
    recognition.onerror = () => showStatus("Voice input stopped. Check microphone permission and try again.");
    recognition.onend = () => {
      state.recording = false;
      state.recognition = null;
      $("voiceButton").classList.remove("recording");
      $("voiceButton").setAttribute("aria-label", "Dictate message");
      input.focus();
    };
    try { recognition.start(); } catch { state.recording = false; showStatus("Could not start voice input."); }
  }

  $("chatForm").addEventListener("submit", event => {
    event.preventDefault();
    submit(input.value);
  });
  input.addEventListener("input", resizeInput);
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
  $("homeButton").addEventListener("click", () => showView("chat"));
  document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", () => {
    showView(button.dataset.view);
    closeMobileMenu();
  }));
  $("workspacePage").addEventListener("click", event => {
    if (event.target.closest("[data-go-chat]")) {
      if (active()) showView("chat");
      else newConversation();
      return;
    }
    const attachButton = event.target.closest("[data-library-attach]");
    if (attachButton) {
      const file = state.libraryFiles.find(item => item.id === attachButton.dataset.libraryAttach);
      if (file) {
        const charCount = state.selectedFiles.reduce((sum, item) => sum + item.content.length, 0);
        if (charCount + file.content.length > MAX_ATTACHMENT_CHARS) {
          showStatus("Attached files exceed the context limit. Remove a file and try again.");
          return;
        }
        if (!state.selectedFiles.some(item => item.name === file.name)) state.selectedFiles.push({ ...file });
        renderAttachmentChips();
        newConversation();
        showStatus(`${file.name} attached. Add a question and send it.`);
      }
    }
  });

  $("moreButton").addEventListener("click", () => toggleMenu($("moreMenu"), $("moreButton")));
  $("checkConnection").addEventListener("click", checkConnection);
  $("searchToggle").addEventListener("click", toggleSearch);
  $("chatSearch").addEventListener("input", () => {
    window.clearTimeout(listSearchTimer);
    listSearchTimer = window.setTimeout(() => refreshConversations($("chatSearch").value.trim()), 160);
  });
  $("clearSearch").addEventListener("click", () => {
    $("chatSearch").value = "";
    refreshConversations("");
    $("chatSearch").focus();
  });
  $("collapseSidebar").addEventListener("click", () => document.body.classList.toggle("sidebar-collapsed"));
  $("openSidebar").addEventListener("click", () => {
    $("sidebar").classList.add("open");
    $("scrim").classList.add("show");
  });
  $("closeSidebar").addEventListener("click", closeMobileMenu);
  $("scrim").addEventListener("click", closeMobileMenu);

  $("attachButton").addEventListener("click", () => toggleMenu($("attachMenu"), $("attachButton")));
  $("chooseFiles").addEventListener("click", () => { closeMenus(); fileInput.click(); });
  fileInput.addEventListener("change", () => chooseFiles(fileInput.files));
  $("expandComposer").addEventListener("click", () => {
    state.expanded = !state.expanded;
    composer.classList.toggle("expanded", state.expanded);
    $("expandComposer").setAttribute("aria-label", state.expanded ? "Collapse message box" : "Expand message box");
    $("expandComposer").title = state.expanded ? "Collapse message box" : "Expand message box";
    resizeInput();
    input.focus();
  });
  $("voiceButton").addEventListener("click", startDictation);

  function closeThreadMenus() {
    document.querySelectorAll(".thread-menu").forEach(menu => { menu.hidden = true; });
    document.querySelectorAll(".thread-menu-button").forEach(button => button.setAttribute("aria-expanded", "false"));
  }

  document.addEventListener("click", event => {
    if (!event.target.closest(".thread-menu-host")) closeThreadMenus();
    if (!event.target.closest(".menu-host")) {
      $("moreMenu").hidden = true;
      $("moreButton").setAttribute("aria-expanded", "false");
    }
    if (!event.target.closest(".attach-host")) {
      $("attachMenu").hidden = true;
      $("attachButton").setAttribute("aria-expanded", "false");
    }
  });
  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (!state.searchOpen) toggleSearch();
      else $("chatSearch").focus();
    }
    if (event.key === "Escape") {
      closeMenus();
      closeMobileMenu();
      if (state.searchOpen && document.activeElement === $("chatSearch")) toggleSearch();
    }
  });

  async function bootstrap() {
    renderAttachmentChips();
    showView("chat");
    await refreshConversations("");
    if (state.conversations.length) {
      await selectConversation(state.conversations[0].thread_id);
    } else {
      state.activeId = null;
      state.activeConversation = null;
      renderList();
      renderMessages();
    }
  }

  bootstrap().catch(error => {
    console.error("CortexFlow frontend initialization failed.", error);
    showStatus(`Could not initialize the chat UI: ${error.message}`);
  });
})();
