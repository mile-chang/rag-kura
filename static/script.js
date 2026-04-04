/**
 * RAG Knowledge Assistant — Frontend Logic
 *
 * Manages conversations, messages, file upload, and UI state.
 * All API calls include an X-Client-ID header for per-browser isolation.
 */

// -- State ------------------------------------------------------------------

const API = "/api";

let activeConversationId = null;
let useRAG = false;
let isGenerating = false;
let currentAbortController = null;
let isStopping = false;

const AVAILABLE_MODELS = [
    { id: "qwen3.5:2b", name: "Qwen 3.5 2B", supportsThinking: true },
    { id: "qwen3.5:4b", name: "Qwen 3.5 4B", supportsThinking: true },
    { id: "llama3.2:3b", name: "Llama 3.2 3B", supportsThinking: false },
    { id: "phi4-mini", name: "Phi-4 Mini", supportsThinking: true }
];
let selectedModelId = "qwen3.5:2b";
let isThinkingEnabled = false;
let isModelDropdownOpen = false;

// Per-browser identity: stored in localStorage, generated on first visit.
const clientId = (() => {
    const KEY = "rag-client-id";
    let id = localStorage.getItem(KEY);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(KEY, id);
    }
    return id;
})();

// Cached DOM references
const $ = (id) => document.getElementById(id);
const $convList = $("conv-list");
const $msgContainer = $("messages-container");
const $messageInput = $("message-input");
const $btnSend = $("btn-send");
const $btnRAG = $("btn-rag");
const $headerTitle = $("header-title");

const $uploadStatus = $("upload-status");
const $chatArea = $("chat-area");
const $sidebar = $("sidebar");


// -- Helpers: fetch wrapper with client ID ----------------------------------

function apiFetch(path, options = {}) {
    const headers = { "X-Client-ID": clientId, ...(options.headers || {}) };
    return fetch(`${API}${path}`, { ...options, headers });
}


// -- Init -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    loadConversations();
    $messageInput.addEventListener("input", () => {
        $btnSend.disabled = !$messageInput.value.trim() || isGenerating;
    });
    renderModelDropdown();
});


// -- Conversation CRUD ------------------------------------------------------

async function loadConversations() {
    try {
        const res = await apiFetch("/conversations");
        const convs = await res.json();
        renderConversationList(convs);
        
        // Auto-sync header title if active conversation title changed on the server
        if (activeConversationId) {
            const activeConv = convs.find(c => c.id === activeConversationId);
            if (activeConv && $headerTitle) {
                $headerTitle.textContent = activeConv.title;
            }
            const btnEdit = $("btn-edit-title");
            if (btnEdit) btnEdit.classList.remove("hidden");
        } else {
            const btnEdit = $("btn-edit-title");
            if (btnEdit) btnEdit.classList.add("hidden");
        }
    } catch (err) {
        console.error("Failed to load conversations:", err);
    }
}

async function createConversation() {
    try {
        const res = await apiFetch("/conversations", { method: "POST" });
        const conv = await res.json();
        activeConversationId = conv.id;
        await loadConversations();
        clearChat();
        $headerTitle.textContent = "New Chat";
        removeWelcome();
        $messageInput.focus();
    } catch (err) {
        console.error("Failed to create conversation:", err);
    }
}

async function selectConversation(id) {
    activeConversationId = id;
    highlightActiveConv();
    try {
        const res = await apiFetch(`/conversations/${id}`);
        const conv = await res.json();
        $headerTitle.textContent = conv.title;
        renderMessages(conv.messages);
        
        const btnEdit = $("btn-edit-title");
        if (btnEdit) btnEdit.classList.remove("hidden");
    } catch (err) {
        console.error("Failed to load conversation:", err);
    }
}

async function deleteConversation(id, event) {
    event.stopPropagation();
    try {
        await apiFetch(`/conversations/${id}`, { method: "DELETE" });
        if (activeConversationId === id) {
            activeConversationId = null;
            clearChat();
            $headerTitle.textContent = "RAG Knowledge Assistant";
            const btnEdit = $("btn-edit-title");
            if (btnEdit) btnEdit.classList.add("hidden");
            showWelcome();
        }
        await loadConversations();
    } catch (err) {
        console.error("Failed to delete conversation:", err);
    }
}

function editConversationTitle() {
    if (!activeConversationId) return;
    const editingConvId = activeConversationId; // Freeze ID for async closure
    const $title = $("header-title");
    const $btnEdit = $("btn-edit-title");
    const currentTitle = $title.textContent;
    
    // Create inline input
    const $input = document.createElement("input");
    $input.type = "text";
    $input.value = currentTitle;
    $input.maxLength = 100; // Hard restriction to prevent layout breakage
    $input.className = "bg-white/[0.06] border border-indigo-500/50 rounded px-2 py-0.5 text-sm text-slate-200 outline-none w-48 sm:w-64 font-semibold text-slate-300";
    
    $title.parentNode.replaceChild($input, $title);
    if ($btnEdit) $btnEdit.classList.add("hidden");
    $input.focus();
    $input.select();
    
    let isSaved = false;
    const saveTitle = async () => {
        if (isSaved) return;
        isSaved = true;
        
        // Strip out excessive lengths and sanitize slightly
        const newTitle = ($input.value.trim() || currentTitle).substring(0, 100);
        
        // Restore DOM immediately for snappy UX
        if ($input.parentNode) {
            $input.parentNode.replaceChild($title, $input);
        }
        
        // Only unhide if we haven't switched to the home banner
        if ($btnEdit && activeConversationId) $btnEdit.classList.remove("hidden");
        
        if (newTitle !== currentTitle) {
            if (activeConversationId === editingConvId) {
                $title.textContent = "Updating...";
            }
            try {
                await apiFetch(`/conversations/${editingConvId}/title`, {
                    method: "PATCH",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ title: newTitle })
                });
                await loadConversations();
            } catch (e) {
                console.error("Failed to update title:", e);
                // Only revert the UI if the user hasn't browsed away from this chat
                if (activeConversationId === editingConvId) {
                    $title.textContent = currentTitle;
                }
            }
        } else {
            if (activeConversationId === editingConvId) {
                $title.textContent = currentTitle;
            }
        }
    };
    
    $input.addEventListener("blur", saveTitle);
    $input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
            saveTitle();
        } else if (e.key === "Escape") {
            $input.value = currentTitle;
            saveTitle();
        }
    });
}


// -- Messaging --------------------------------------------------------------

async function sendMessage() {
    const text = $messageInput.value.trim();
    if (!text || isGenerating) return;

    $messageInput.value = "";
    autoResize($messageInput);
    
    currentAbortController = new AbortController();
    isStopping = false;
    
    setGenerating(true);
    appendMessage("user", text);

    let thinkingId = null;
    
    // Check if the targeted model is warmed up in VRAM
    let isWarmingUp = false;
    try {
        const checkRes = await apiFetch(
            `/models/check_loaded?base_model=${selectedModelId}&use_reasoning=${isThinkingEnabled}`,
            { method: "GET", signal: currentAbortController.signal }
        );
        const data = await checkRes.json();
        if (data && !data.is_loaded) isWarmingUp = true;
    } catch (e) {
        if (e.name !== 'AbortError') {
            console.warn("Could not check model status", e);
        }
    }

    if (isWarmingUp) {
        // Show warm up indicator with same bubble style
        thinkingId = "thinking-" + Date.now();
        const el = document.createElement("div");
        el.id = thinkingId;
        el.className = "chat-bubble";
        el.innerHTML = `
            <div class="flex justify-start">
                <div class="flex items-center gap-2.5 px-4 py-3 text-[13px] text-slate-400 font-medium tracking-wide">
                    <svg class="animate-spin h-3.5 w-3.5 text-indigo-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <span class="animate-pulse">正在載入模型引擎...</span>
                </div>
            </div>`;
        $msgContainer.appendChild(el);
        scrollToBottom();
    } else {
        thinkingId = appendThinking();
    }

    try {
        // Auto-create conversation if none is active
        if (!activeConversationId) {
            const res = await apiFetch("/conversations", { method: "POST" });
            const conv = await res.json();
            activeConversationId = conv.id;
            // The title will be auto-updated by the backend on the first message
            $headerTitle.textContent = "New Chat";
        }

        const res = await apiFetch(
            `/conversations/${activeConversationId}/messages`,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    message: text,
                    base_model: selectedModelId,
                    use_reasoning: isThinkingEnabled,
                    use_rag: useRAG,
                }),
                signal: currentAbortController.signal
            }
        );

        removeThinking(thinkingId);

        if (!res.ok) {
            const err = await res.json();
            appendMessage("assistant", `Error: ${err.detail || res.statusText}`);
            return;
        }

        const data = await res.json();
        appendMessage("assistant", data.content, {
            model: data.model,
            elapsed: data.elapsed_seconds,
            tools: data.tools_used,
            rag: data.use_rag,
        });

        // Refresh sidebar (title may have been auto-generated)
        await loadConversations();
    } catch (err) {
        removeThinking(thinkingId);
        if (err.name === 'AbortError') {
            appendMessage("assistant", "*(已停止回覆)*");
        } else {
            appendMessage("assistant", `Connection error: ${err.message}`);
        }
    } finally {
        setGenerating(false);
        currentAbortController = null;
        isStopping = false;
    }
}

function stopGeneration() {
    if (!isGenerating || isStopping || !currentAbortController) return;
    
    isStopping = true;
    const btnStop = document.getElementById("btn-stop");
    
    // Change to spinning loader
    if (btnStop) {
        btnStop.innerHTML = `<svg class="animate-spin h-4 w-4 text-slate-300" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
    }
    
    // Delay slightly to show the animation
    setTimeout(() => {
        if (currentAbortController) {
            currentAbortController.abort();
        }
    }, 400);
}


// -- File upload -------------------------------------------------------------

async function handleFileUpload(files) {
    if (!files.length) return;

    $uploadStatus.textContent = "Uploading...";
    $uploadStatus.classList.remove("hidden");

    const form = new FormData();
    for (const f of files) form.append("files", f);

    try {
        const res = await apiFetch("/upload", { method: "POST", body: form });

        if (res.ok) {
            $uploadStatus.textContent = (await res.json()).message;
            setTimeout(() => $uploadStatus.classList.add("hidden"), 4000);
        } else {
            const err = await res.json();
            $uploadStatus.textContent = `Upload failed: ${err.detail}`;
            $uploadStatus.classList.replace("text-emerald-400", "text-red-400");
            setTimeout(() => {
                $uploadStatus.classList.add("hidden");
                $uploadStatus.classList.replace("text-red-400", "text-emerald-400");
            }, 5000);
        }
    } catch (err) {
        $uploadStatus.textContent = `Upload error: ${err.message}`;
        setTimeout(() => $uploadStatus.classList.add("hidden"), 5000);
    }

    $("file-input").value = "";
}


// -- RAG toggle -------------------------------------------------------------

function toggleRAG() {
    useRAG = !useRAG;
    $btnRAG.className = $btnRAG.className.replace(
        useRAG ? "chip-inactive" : "chip-active",
        useRAG ? "chip-active" : "chip-inactive"
    );
}


// -- Sidebar toggle (mobile) ------------------------------------------------

function toggleSidebar() {
    $sidebar.classList.toggle("hidden");
}


// -- Rendering --------------------------------------------------------------

function renderConversationList(convs) {
    $convList.innerHTML = convs.map(c => `
        <button class="conv-item w-full flex items-center justify-between px-3 py-2.5 rounded-lg
                       text-left text-sm cursor-pointer group
                       ${c.id === activeConversationId ? 'bg-white/[0.08] text-slate-200' : 'text-slate-400'}"
                onclick="selectConversation('${c.id}')">
            <span class="truncate flex-1">${escapeHtml(c.title)}</span>
            <span class="delete-btn text-slate-600 hover:text-red-400 ml-2 flex-shrink-0"
                  onclick="deleteConversation('${c.id}', event)" title="Delete">
                <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                          d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                </svg>
            </span>
        </button>
    `).join("");
}

function highlightActiveConv() {
    $convList.querySelectorAll(".conv-item").forEach(el => {
        const isActive = el.getAttribute("onclick")?.includes(activeConversationId);
        el.classList.toggle("bg-white/[0.08]", isActive);
        el.classList.toggle("text-slate-200", isActive);
        el.classList.toggle("text-slate-400", !isActive);
    });
}

function renderMessages(messages) {
    clearChat();
    for (const msg of messages) {
        if (msg.role === "user") {
            appendMessage("user", msg.content);
        } else if (msg.role === "assistant") {
            appendMessage("assistant", msg.content, {
                model: msg.model,
                elapsed: msg.elapsed_seconds,
                tools: msg.tools_used || [],
                rag: msg.use_rag,
            });
        }
    }
}

function appendMessage(role, content, meta = null) {
    removeWelcome();

    const wrapper = document.createElement("div");
    wrapper.className = "chat-bubble";

    if (role === "user") {
        wrapper.innerHTML = `
            <div class="flex justify-end">
                <div class="max-w-[80%] bg-indigo-600/20 border border-indigo-500/20
                            rounded-2xl rounded-br-md px-4 py-3">
                    <p class="text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">${escapeHtml(content)}</p>
                </div>
            </div>`;
    } else {
        const toolsLabel = meta?.tools?.length ? meta.tools.join(", ") : "None";
        const metaHtml = meta ? `
            <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-600">
                <span>${meta.model}</span>
                <span class="text-slate-700">|</span>
                <span>${meta.elapsed}s</span>
                <span class="text-slate-700">|</span>
                <span>Tools: ${toolsLabel}</span>
                ${meta.rag ? '<span class="text-slate-700">|</span><span class="text-indigo-500">RAG</span>' : ""}
            </div>` : "";

        wrapper.innerHTML = `
            <div class="flex justify-start">
                <div class="max-w-[85%]">
                    <div class="msg-content text-sm text-slate-300 leading-relaxed">
                        ${renderMarkdown(content)}
                    </div>
                    ${metaHtml}
                </div>
            </div>`;
    }

    $msgContainer.appendChild(wrapper);
    scrollToBottom();
}

function appendThinking() {
    const id = "thinking-" + Date.now();
    const el = document.createElement("div");
    el.id = id;
    el.className = "chat-bubble";
    el.innerHTML = `
        <div class="flex justify-start">
            <div class="flex items-center gap-1 px-4 py-3">
                <span class="thinking-dot w-2 h-2 rounded-full bg-indigo-400 inline-block"></span>
                <span class="thinking-dot w-2 h-2 rounded-full bg-indigo-400 inline-block"></span>
                <span class="thinking-dot w-2 h-2 rounded-full bg-indigo-400 inline-block"></span>
            </div>
        </div>`;
    $msgContainer.appendChild(el);
    scrollToBottom();
    return id;
}

function removeThinking(id) {
    document.getElementById(id)?.remove();
}

function clearChat() {
    $msgContainer.innerHTML = "";
}

function removeWelcome() {
    // Always query the DOM dynamically instead of using a stale cached reference
    document.getElementById("welcome-state")?.remove();
}

function showWelcome() {
    $msgContainer.innerHTML = `
        <div id="welcome-state" class="flex flex-col items-center justify-center h-full pt-24">
            <div class="text-3xl font-bold text-slate-300 mb-2 tracking-tight">RAG Knowledge Assistant</div>
            <p class="text-slate-500 text-sm mb-8">Select a conversation or start a new one.</p>
        </div>`;
}


// -- Input helpers ----------------------------------------------------------

function handleKeyDown(event) {
    // Enter sends, Shift+Enter inserts newline
    if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
}

function autoResize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

function setGenerating(value) {
    isGenerating = value;
    const btnStop = document.getElementById("btn-stop");
    
    if (value) {
        $btnSend.classList.add("hidden");
        if (btnStop) {
            btnStop.classList.remove("hidden");
            btnStop.classList.add("flex");
            btnStop.innerHTML = `<svg class="w-3.5 h-3.5 text-indigo-200 fill-current" viewBox="0 0 16 16"><rect width="10" height="10" x="3" y="3" rx="2" /></svg>`;
        }
    } else {
        if (btnStop) {
            btnStop.classList.add("hidden");
            btnStop.classList.remove("flex");
        }
        $btnSend.classList.remove("hidden");
        $btnSend.disabled = !$messageInput.value.trim();
        $messageInput.focus();
    }
    
    $messageInput.disabled = value;
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        $chatArea.scrollTop = $chatArea.scrollHeight;
    });
}


// -- Lightweight markdown renderer ------------------------------------------

function renderMarkdown(text) {
    if (!text) return "";
    let html = escapeHtml(text);

    // Fenced code blocks
    html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_, lang, code) =>
        `<pre><code>${code.trim()}</code></pre>`
    );

    // Inline code, bold, italic (order matters)
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");

    // Headings
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");

    // Blockquotes
    html = html.replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>");

    // Lists
    html = html.replace(/^[\-\*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");
    html = html.replace(/^\d+\. (.+)$/gm, "<li>$1</li>");

    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>'
    );

    // Paragraphs
    html = html.replace(/\n\n/g, "</p><p>");
    html = html.replace(/\n/g, "<br>");
    html = `<p>${html}</p>`;

    // Strip empty / mis-nested paragraphs around block elements
    const blocks = ["h[1-3]", "pre", "ul", "blockquote"];
    for (const tag of blocks) {
        html = html.replace(new RegExp(`<p>(<${tag}>)`, "g"), "$1");
        html = html.replace(new RegExp(`(</${tag}>)</p>`, "g"), "$1");
    }
    html = html.replace(/<p>\s*<\/p>/g, "");

    return html;
}


// -- Utilities --------------------------------------------------------------

function escapeHtml(text) {
    const el = document.createElement("div");
    el.textContent = text;
    return el.innerHTML;
}

// -- Custom Model Dropdown --------------------------------------------------

function toggleModelDropdown(event) {
    if (event) event.stopPropagation();
    isModelDropdownOpen = !isModelDropdownOpen;
    renderModelDropdown();
}

document.addEventListener('click', (e) => {
    if (!e.target.closest('#btn-model-trigger') && !e.target.closest('#model-dropdown-menu')) {
        isModelDropdownOpen = false;
        const menu = $("model-dropdown-menu");
        if (menu) menu.classList.add("hidden");
    }
});

function selectModel(id, event) {
    if (event) event.stopPropagation();
    selectedModelId = id;
    const model = AVAILABLE_MODELS.find(m => m.id === id);
    if (!model.supportsThinking) {
        isThinkingEnabled = false;
    }
    isModelDropdownOpen = false;
    $("current-model-label").textContent = model.name;
    renderModelDropdown();
}

function toggleThinkingMode(event) {
    if (event) event.stopPropagation();
    isThinkingEnabled = !isThinkingEnabled;
    renderModelDropdown();
}

function renderModelDropdown() {
    const menu = $("model-dropdown-menu");
    if (!menu) return;
    if (isModelDropdownOpen) {
        menu.classList.remove("hidden");
        let html = "";
        AVAILABLE_MODELS.forEach(m => {
            const isSelected = m.id === selectedModelId;
            html += `
                <button onclick="selectModel('${m.id}', event)" class="w-full flex items-center justify-between px-3 py-2.5 text-left hover:bg-white/[0.06] transition-colors cursor-pointer ${isSelected ? 'bg-white/[0.04]' : ''}">
                    <div class="flex items-center gap-2">
                        <!-- Dummy unified icon for now -->
                        <svg class="w-4 h-4 ${isSelected ? 'text-indigo-400' : 'text-slate-500'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/>
                        </svg>
                        <span class="${isSelected ? 'text-slate-100 font-medium' : 'text-slate-300'}">${m.name}</span>
                    </div>
                    ${isSelected ? '<svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>' : ''}
                </button>
            `;
            if (isSelected && m.supportsThinking) {
                html += `
                <div class="flex items-center justify-between px-3 py-2.5 bg-white/[0.02] border-y border-white/[0.06] mb-1" onclick="event.stopPropagation()">
                    <span class="text-xs text-slate-300 font-medium pl-1">思考中</span>
                    <label class="relative inline-flex items-center cursor-pointer" onclick="toggleThinkingMode(event)">
                        <input type="checkbox" class="sr-only peer" ${isThinkingEnabled ? 'checked' : ''} onclick="event.stopPropagation()">
                        <div class="w-7 h-4 bg-white/[0.1] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-3 peer-checked:after:bg-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-slate-300 after:border-transparent after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-emerald-500"></div>
                    </label>
                </div>
                `;
            }
        });
        menu.innerHTML = html;
        const currentModel = AVAILABLE_MODELS.find(m => m.id === selectedModelId);
        if ($("current-model-label")) {
            $("current-model-label").textContent = currentModel.name;
        }
    } else {
        menu.classList.add("hidden");
    }
}
