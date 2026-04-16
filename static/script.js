/**
 * RAG Knowledge Assistant — Frontend Core
 *
 * Handles API communications, chat lifecycle, Markdown rendering, and UI state management.
 * Injects `X-Client-ID` in headers to maintain isolated browser sessions natively.
 */

// -- State ------------------------------------------------------------------

const API = "/api";

let activeConversationId = null;
let useRAG = false;
let isGenerating = false;
let currentAbortController = null;
let isStopping = false;

const AVAILABLE_MODELS = [
    // -- Ollama local models (require a running Ollama daemon) ---------------
    { id: "qwen3.5:2b", name: "Qwen 3.5 2B", provider: "ollama", supportsThinking: true },
    { id: "qwen3.5:4b", name: "Qwen 3.5 4B", provider: "ollama", supportsThinking: true },
    { id: "llama3.2:3b", name: "Llama 3.2 3B", provider: "ollama", supportsThinking: false },
    { id: "phi4-mini", name: "Phi-4 Mini", provider: "ollama", supportsThinking: true },
    // -- Google cloud models (require GEMINI_API_KEY on the server) --
    { id: "gemini-3-flash-preview", name: "Gemini 3 Flash", provider: "gemini", supportsThinking: false },
    { id: "gemma-4-31b-it", name: "Gemma 4 31B", provider: "gemini", supportsThinking: true },
];
let selectedModelId = "qwen3.5:2b";
let isThinkingEnabled = false;
let isModelDropdownOpen = false;

// Automatically fall back to whichever provider is reachable during startup
// Optimistically default to True so UI isn't blocked pending /api/status.
let ollamaAvailable = true;
let geminiAvailable = true;

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

// -- Theme Management --------------------------------------------------------

const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');
let isThemeDropdownOpen = false;

function applyTheme(theme) {
    if (theme === 'system') {
        document.documentElement.setAttribute('data-theme', 'system');
    } else {
        document.documentElement.setAttribute('data-theme', theme);
    }
    
    // Update dropdown UI icons and label
    const sysIcon = $("theme-icon-system");
    const lightIcon = $("theme-icon-light");
    const darkIcon = $("theme-icon-dark");
    const label = $("theme-label");

    if (sysIcon) sysIcon.classList.toggle('hidden', theme !== 'system');
    if (lightIcon) lightIcon.classList.toggle('hidden', theme !== 'light');
    if (darkIcon) darkIcon.classList.toggle('hidden', theme !== 'dark');
    
    const labelMap = { 'system': 'System', 'light': 'Light', 'dark': 'Dark' };
    if (label) label.textContent = labelMap[theme] || 'System';
}

function handleSystemThemeChange() {
    const currentTheme = localStorage.getItem('rag-theme') || 'system';
    if (currentTheme === 'system') {
        // Native CSS handles changes based on [data-theme="system"] via @media 
        // We only listen here in case we need JS reaction.
    }
}

themeMedia.addEventListener('change', handleSystemThemeChange);

window.setTheme = function(theme, event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    
    if (!['system', 'light', 'dark'].includes(theme)) {
        theme = 'system';
    }
    
    localStorage.setItem('rag-theme', theme);
    applyTheme(theme);
    
    isThemeDropdownOpen = false;
    const menu = $("theme-dropdown-menu");
    const trigger = $("btn-theme-trigger");
    if (menu) menu.classList.add("hidden");
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
};

window.toggleThemeDropdown = function(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    isThemeDropdownOpen = !isThemeDropdownOpen;
    const menu = $("theme-dropdown-menu");
    const trigger = $("btn-theme-trigger");
    if (menu) {
        menu.classList.toggle("hidden", !isThemeDropdownOpen);
        if (trigger) trigger.setAttribute('aria-expanded', isThemeDropdownOpen.toString());
        if (isThemeDropdownOpen) {
            menu.querySelector('button')?.focus();
        }
    }
};

// Handle clicks outside of dropdowns
document.addEventListener('click', (e) => {
    // Theme Dropdown
    if (!e.target.closest('#btn-theme-trigger') && !e.target.closest('#theme-dropdown-menu')) {
        isThemeDropdownOpen = false;
        const themeMenu = $("theme-dropdown-menu");
        const themeTrigger = $("btn-theme-trigger");
        if (themeMenu) themeMenu.classList.add("hidden");
        if (themeTrigger) themeTrigger.setAttribute('aria-expanded', 'false');
    }
    
    // Model Dropdown
    if (!e.target.closest('#btn-model-trigger') && !e.target.closest('#model-dropdown-menu')) {
        isModelDropdownOpen = false;
        const modelMenu = $("model-dropdown-menu");
        if (modelMenu) modelMenu.classList.add("hidden");
    }
});


// -- Helpers: fetch wrapper with client ID ----------------------------------

function apiFetch(path, options = {}) {
    const headers = { "X-Client-ID": clientId, ...(options.headers || {}) };
    return fetch(`${API}${path}`, { ...options, headers });
}


// -- Init -------------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
    // Init theme
    const savedTheme = localStorage.getItem('rag-theme') || 'system';
    applyTheme(['system', 'light', 'dark'].includes(savedTheme) ? savedTheme : 'system');

    loadConversations();
    $messageInput.addEventListener("input", () => {
        $btnSend.disabled = !$messageInput.value.trim() || isGenerating;
    });
    // Query provider availability then render the model picker.
    // checkProviderAvailability() calls renderModelDropdown() internally.
    checkProviderAvailability();
});


// -- Conversation CRUD ------------------------------------------------------

async function loadConversations() {
    try {
        const res = await apiFetch("/conversations");
        const convs = await res.json();
        renderConversationList(convs);

        // Sync header title, or reset UI if the active conversation was deleted elsewhere
        if (activeConversationId) {
            const activeConv = convs.find(c => c.id === activeConversationId);
            const btnEdit = $("btn-edit-title");

            if (activeConv) {
                if ($headerTitle) $headerTitle.textContent = activeConv.title;
                if (btnEdit) btnEdit.classList.remove("hidden");
            } else {
                activeConversationId = null;
                clearChat();
                if ($headerTitle) $headerTitle.textContent = "RAG Knowledge Assistant";
                if (btnEdit) btnEdit.classList.add("hidden");
                showWelcome();
            }
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
    $input.maxLength = 100; // Hard cap prevents layout breakage
    $input.className = "bg-input border border-brand/50 rounded px-2 py-0.5 text-sm text-primary outline-none w-48 sm:w-64 font-semibold focus:ring-1 focus:ring-brand";

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

        // Swap back to text element instantly for snappy UX
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

    // Ollama lazily loads models into VRAM. Gemini is a cloud architecture and requires no warmup.
    let isWarmingUp = false;
    try {
        const checkRes = await apiFetch(
            `/models/${encodeURIComponent(selectedModelId)}/status?use_reasoning=${isThinkingEnabled}`,
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
                <div class="flex items-center gap-2.5 px-4 py-3 text-[13px] text-muted font-medium tracking-wide">
                    <svg class="animate-spin h-3.5 w-3.5 text-brand" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                    <span class="animate-pulse">Loading model engine...</span>
                </div>
            </div>`;
        $msgContainer.appendChild(el);
        scrollToBottom();
    } else {
        thinkingId = appendThinking();
    }

    try {
        // Lazy initialize a new conversation when sending the first prompt
        if (!activeConversationId) {
            const res = await apiFetch("/conversations", { method: "POST" });
            const conv = await res.json();
            activeConversationId = conv.id;
            $headerTitle.textContent = "New Chat";
            // Async sync sidebar to avoid blocking message dispatch
            loadConversations();
        }

        // All models use SSE streaming for real-time token display
        await sendMessageStream(text, thinkingId);

    } catch (err) {
        removeThinking(thinkingId);
        if (err.name === 'AbortError') {
            appendMessage("assistant", "*(Stopped)*");
        } else {
            appendMessage("assistant", `Connection error: ${err.message}`);
        }
    } finally {
        setGenerating(false);
        currentAbortController = null;
        isStopping = false;
    }
}


/**
 * Send a message using SSE streaming and render tokens in real time.
 *
 * Uses fetch + ReadableStream + TextDecoder with a string buffer to correctly
 * handle TCP packet fragmentation: only complete "data: ...\n\n" segments are
 * JSON-parsed; incomplete trailing data waits in the buffer for the next read.
 *
 * SSE event types from the server:
 *   status — tool is executing (shown in the thinking bubble)
 *   chunk  — a text token to append to the assistant bubble
 *   done   — stream complete; carries model/elapsed/tools_used metadata
 *   error  — server-side error message
 *
 * @param {string} text        The user message text.
 * @param {string} thinkingId  ID of the thinking indicator element to update.
 */
async function sendMessageStream(text, thinkingId) {
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
                stream: true,
            }),
            signal: currentAbortController.signal,
        }
    );

    if (!res.ok) {
        removeThinking(thinkingId);
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        appendMessage("assistant", `Error: ${err.detail || res.statusText}`);
        return;
    }

    // Create the assistant bubble immediately (before the first token arrives)
    removeThinking(thinkingId);
    const bubbleId = "stream-bubble-" + Date.now();
    const wrapper = document.createElement("div");
    wrapper.id = bubbleId;
    wrapper.className = "chat-bubble";
    wrapper.innerHTML = `
        <div class="flex justify-start">
            <div class="max-w-[85%]">
                <div class="msg-content text-sm text-primary leading-relaxed"></div>
                <div class="msg-meta mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted opacity-80 hidden"></div>
            </div>
        </div>`;
    $msgContainer.appendChild(wrapper);
    scrollToBottom();

    const $content = wrapper.querySelector(".msg-content");
    const $meta = wrapper.querySelector(".msg-meta");

    // Status indicator shown while tools are executing
    let statusEl = null;
    const showStatus = (msg) => {
        if (!statusEl) {
            statusEl = document.createElement("div");
            statusEl.className = "flex items-center gap-2 mb-2 text-xs text-brand";
            $content.prepend(statusEl);
        }
        statusEl.innerHTML = `
            <svg class="animate-spin h-3 w-3" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>${escapeHtml(msg)}</span>`;
    };

    // ── Thinking accordion helpers ──
    let thinkEl = null;       // The live accordion DOM element
    let thinkBody = null;     // The scrollable body inside it
    let isInThink = false;    // Are we currently inside <think> content?
    let thinkBuffer = "";     // Accumulated thinking text (for the body)

    const createThinkAccordion = () => {
        thinkEl = document.createElement("div");
        thinkEl.className = "think-accordion mb-3 rounded-xl border border-border bg-surface-2 overflow-hidden";
        thinkEl.innerHTML = `
            <button class="think-header w-full flex items-center gap-2 px-3 py-2 text-xs text-muted hover:text-primary outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand transition-colors" onclick="this.closest('.think-accordion').classList.toggle('open')">
                <svg class="think-spinner w-4 h-4 flex-shrink-0 text-brand" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M12 3v3m0 12v3M3 12h3m12 0h3M5.636 5.636l2.122 2.122m8.484 8.484l2.122 2.122M5.636 18.364l2.122-2.122m8.484-8.484l2.122-2.122"/>
                </svg>
                <span class="think-label font-medium">Thinking…</span>
                <svg class="think-chevron w-3 h-3 ml-auto transition-transform" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/>
                </svg>
            </button>
            <div class="think-body px-3 pb-3 text-xs text-muted leading-relaxed whitespace-pre-wrap font-mono max-h-64 overflow-y-auto"></div>`;
        thinkBody = thinkEl.querySelector(".think-body");
        $content.appendChild(thinkEl);
    };

    const finaliseThinkAccordion = () => {
        if (!thinkEl) return;
        // Swap spinner for static icon, update label, auto-collapse
        const spinner = thinkEl.querySelector(".think-spinner");
        spinner.outerHTML = `<svg class="w-4 h-4 flex-shrink-0 text-brand opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>`;
        thinkEl.querySelector(".think-label").textContent = "Thought Process";
        // Collapse by default (toggle open class off)
        thinkEl.classList.remove("open");
        thinkEl = null;
        thinkBody = null;
    };

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let accumulatedText = "";   // Only the non-think, final answer text
    let rawChunkBuffer = "";     // Carry-over for split <think>/</ think> tags

    // A small <div> that holds only the final answer (appears after accordion)
    const $answer = document.createElement("div");
    $content.appendChild($answer);

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            // Append decoded bytes to the buffer
            buffer += decoder.decode(value, { stream: true });

            // Split on the SSE double-newline delimiter.
            // Keep the last (potentially incomplete) segment in the buffer.
            const segments = buffer.split("\n\n");
            buffer = segments.pop(); // last element may be incomplete

            for (const segment of segments) {
                const line = segment.trim();
                if (!line.startsWith("data: ")) continue;

                let event;
                try {
                    event = JSON.parse(line.slice(6)); // strip "data: " prefix
                } catch {
                    console.warn("SSE JSON parse error:", line);
                    continue;
                }

                switch (event.type) {
                    case "status":
                        showStatus(event.content);
                        break;

                    case "chunk": {
                        // Remove status indicator once real text starts flowing
                        if (statusEl) { statusEl.remove(); statusEl = null; }

                        // Process the new content, routing into accordion or answer
                        rawChunkBuffer += event.content;

                        // Parse out <think> and </think> boundaries
                        let processed = rawChunkBuffer;
                        rawChunkBuffer = "";

                        while (processed.length > 0) {
                            if (!isInThink) {
                                const startIdx = processed.indexOf("<think>");
                                if (startIdx === -1) {
                                    // Check for partial tag at end
                                    const partial = "<think>";
                                    let tailMatch = -1;
                                    for (let l = 1; l < partial.length; l++) {
                                        if (processed.endsWith(partial.slice(0, l))) { tailMatch = l; break; }
                                    }
                                    if (tailMatch > 0) {
                                        accumulatedText += processed.slice(0, processed.length - tailMatch);
                                        rawChunkBuffer = processed.slice(processed.length - tailMatch);
                                    } else {
                                        accumulatedText += processed;
                                    }
                                    processed = "";
                                } else {
                                    accumulatedText += processed.slice(0, startIdx);
                                    processed = processed.slice(startIdx + 7); // skip "<think>"
                                    isInThink = true;
                                    createThinkAccordion();
                                    // Spin the spinner
                                    const sp = thinkEl?.querySelector(".think-spinner");
                                    if (sp) sp.style.animation = "spin 1s linear infinite";
                                }
                            } else {
                                const endIdx = processed.indexOf("</think>");
                                if (endIdx === -1) {
                                    // Still in think; check for partial closing tag
                                    const partial = "</think>";
                                    let tailMatch = -1;
                                    for (let l = 1; l < partial.length; l++) {
                                        if (processed.endsWith(partial.slice(0, l))) { tailMatch = l; break; }
                                    }
                                    if (tailMatch > 0) {
                                        thinkBuffer += processed.slice(0, processed.length - tailMatch);
                                        rawChunkBuffer = processed.slice(processed.length - tailMatch);
                                    } else {
                                        thinkBuffer += processed;
                                    }
                                    if (thinkBody) thinkBody.textContent = thinkBuffer;
                                    processed = "";
                                } else {
                                    thinkBuffer += processed.slice(0, endIdx);
                                    if (thinkBody) thinkBody.textContent = thinkBuffer;
                                    processed = processed.slice(endIdx + 8); // skip "</think>"
                                    isInThink = false;
                                    thinkBuffer = "";
                                    finaliseThinkAccordion();
                                }
                            }
                        }

                        $answer.innerHTML = renderMarkdown(accumulatedText);
                        scrollToBottom();
                        break;
                    }

                    case "done": {
                        if (statusEl) { statusEl.remove(); statusEl = null; }
                        finaliseThinkAccordion(); // safety net if stream ended mid-think
                        // Render final metadata footer
                        const toolsLabel = event.tools_used?.length
                            ? event.tools_used.join(", ")
                            : "None";
                        $meta.innerHTML = `
                            <span>${escapeHtml(event.model || selectedModelId)}</span>
                            <span class="opacity-50">|</span>
                            <span>${event.elapsed_seconds}s</span>
                            <span class="opacity-50">|</span>
                            <span>Tools: ${escapeHtml(toolsLabel)}</span>
                            ${useRAG ? '<span class="opacity-50">|</span><span class="text-brand">RAG</span>' : ""}
                        `;
                        $meta.classList.remove("hidden");
                        // Refresh sidebar (title may have been auto-generated)
                        await loadConversations();
                        break;
                    }

                    case "error":
                        if (statusEl) { statusEl.remove(); statusEl = null; }
                        $content.innerHTML = `<span class="text-error">Error: ${escapeHtml(event.content)}</span>`;
                        break;
                }
            }
        }
    } finally {
        reader.releaseLock();
    }
}

function stopGeneration() {
    if (!isGenerating || isStopping || !currentAbortController) return;

    isStopping = true;
    const btnStop = document.getElementById("btn-stop");

    // Change to spinning loader
    if (btnStop) {
        btnStop.innerHTML = `<svg class="animate-spin h-4 w-4 text-primary" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>`;
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
            $uploadStatus.classList.replace("text-success", "text-error");
            setTimeout(() => {
                $uploadStatus.classList.add("hidden");
                $uploadStatus.classList.replace("text-error", "text-success");
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
                       text-left text-sm cursor-pointer group focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand outline-none
                       ${c.id === activeConversationId ? 'bg-surface-2 text-primary' : 'text-muted hover:bg-hover'}"
                onclick="selectConversation('${c.id}')">
            <span class="truncate flex-1">${escapeHtml(c.title)}</span>
            <span class="delete-btn text-muted hover:text-error ml-2 flex-shrink-0"
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
        el.classList.toggle("bg-surface-2", isActive);
        el.classList.toggle("hover:bg-hover", !isActive);
        el.classList.toggle("text-primary", isActive);
        el.classList.toggle("text-muted", !isActive);
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
                <div class="max-w-[80%] bg-brand/20 border border-brand/20
                            rounded-2xl rounded-br-md px-4 py-3">
                    <p class="text-sm text-primary leading-relaxed whitespace-pre-wrap">${escapeHtml(content)}</p>
                </div>
            </div>`;
    } else {
        const toolsLabel = meta?.tools?.length ? meta.tools.join(", ") : "None";
        const metaHtml = meta ? `
            <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted opacity-80">
                <span>${meta.model}</span>
                <span class="opacity-50">|</span>
                <span>${meta.elapsed}s</span>
                <span class="opacity-50">|</span>
                <span>Tools: ${toolsLabel}</span>
                ${meta.rag ? '<span class="opacity-50">|</span><span class="text-brand">RAG</span>' : ""}
            </div>` : "";

        wrapper.innerHTML = `
            <div class="flex justify-start">
                <div class="max-w-[85%]">
                    <div class="msg-content text-sm text-primary leading-relaxed">
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
                <span class="thinking-dot w-2 h-2 rounded-full bg-brand inline-block"></span>
                <span class="thinking-dot w-2 h-2 rounded-full bg-brand inline-block"></span>
                <span class="thinking-dot w-2 h-2 rounded-full bg-brand inline-block"></span>
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
    document.getElementById("welcome-state")?.remove();
}

function showWelcome() {
    $msgContainer.innerHTML = `
        <div id="welcome-state" class="flex flex-col items-center justify-center h-full pt-24">
            <div class="text-3xl font-bold text-primary mb-2 tracking-tight">RAG Knowledge Assistant</div>
            <p class="text-muted text-sm mb-8">Select a conversation or start a new one.</p>
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
            btnStop.innerHTML = `<svg class="w-3.5 h-3.5 text-error fill-current" viewBox="0 0 16 16"><rect width="10" height="10" x="3" y="3" rx="2" /></svg>`;
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

    // ── Strip out any <think>…</think> blocks in saved messages (render as accordion) ──
    let html = text.replace(/<think>([\s\S]*?)<\/think>/g, (_, thinkContent) => {
        const escaped = escapeHtml(thinkContent.trim());
        return `<div class="think-accordion mb-3 rounded-xl border border-border bg-surface-2 overflow-hidden">
            <button class="think-header w-full flex items-center gap-2 px-3 py-2 text-xs text-muted hover:text-primary focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand outline-none transition-colors" onclick="this.closest('.think-accordion').classList.toggle('open')">
                <svg class="w-4 h-4 flex-shrink-0 text-brand opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>
                <span class="think-label font-medium">Thought Process</span>
                <svg class="think-chevron w-3 h-3 ml-auto transition-transform" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
            </button>
            <div class="think-body px-3 pb-3 text-xs text-muted leading-relaxed whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">${escaped}</div>
        </div>`;
    });

    html = escapeHtml(html
        .replace(/<div class="think-accordion[\s\S]*?<\/div>\s*<\/div>\s*<\/div>/g, "__THINK_BLOCK__")
    );

    // Re-do: process cleanly — split on think-accordion placeholders
    // to avoid double-escaping the accordion HTML we just built.
    const thinkBlocks = [];
    let cleanText = text.replace(/<think>[\s\S]*?<\/think>/g, (match) => {
        thinkBlocks.push(match);
        return `\x00THINK${thinkBlocks.length - 1}\x00`;
    });

    // Now escape and markdown-render the clean text
    html = escapeHtml(cleanText);

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

    // Re-inject think accordion blocks
    html = html.replace(/\x00THINK(\d+)\x00/g, (_, idx) => {
        const thinkContent = thinkBlocks[parseInt(idx)]
            .replace(/<think>([\s\S]*?)<\/think>/, (__, inner) => inner.trim());
        const escaped = escapeHtml(thinkContent);
        return `<div class="think-accordion mb-3 rounded-xl border border-border bg-surface-2 overflow-hidden">
            <button class="think-header w-full flex items-center gap-2 px-3 py-2 text-xs text-muted hover:text-primary focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand outline-none transition-colors" onclick="this.closest('.think-accordion').classList.toggle('open')">
                <svg class="w-4 h-4 flex-shrink-0 text-brand opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path stroke-linecap="round" stroke-linejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9  5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z"/></svg>
                <span class="think-label font-medium">Thought Process</span>
                <svg class="think-chevron w-3 h-3 ml-auto transition-transform" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd"/></svg>
            </button>
            <div class="think-body px-3 pb-3 text-xs text-muted leading-relaxed whitespace-pre-wrap font-mono max-h-64 overflow-y-auto">${escaped}</div>
        </div>`;
    });

    return html;
}


// -- Utilities --------------------------------------------------------------

function escapeHtml(text) {
    const el = document.createElement("div");
    el.textContent = text;
    return el.innerHTML;
}

// -- Provider availability check -------------------------------------------

async function checkProviderAvailability() {
    try {
        const res = await fetch("/api/status");
        if (res.ok) {
            const data = await res.json();
            ollamaAvailable = data.ollama_available ?? true;
            geminiAvailable = data.gemini_available ?? true;
        }
    } catch (err) {
        console.warn("Could not reach /api/status:", err);
    }

    const current = AVAILABLE_MODELS.find(m => m.id === selectedModelId);
    if (current && !isModelAvailable(current)) {
        const fallback = AVAILABLE_MODELS.find(m => isModelAvailable(m));
        if (fallback) {
            selectedModelId = fallback.id;
            if (!fallback.supportsThinking) isThinkingEnabled = false;
        }
    }

    renderModelDropdown();
}

function isModelAvailable(model) {
    if (model.provider === "ollama") return ollamaAvailable;
    if (model.provider === "gemini") return geminiAvailable;
    return true;
}


// -- Custom Model Dropdown --------------------------------------------------

function toggleModelDropdown(event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    isModelDropdownOpen = !isModelDropdownOpen;
    renderModelDropdown();
}

function selectModel(id, event) {
    if (event) {
        event.preventDefault();
        event.stopPropagation();
    }
    const model = AVAILABLE_MODELS.find(m => m.id === id);
    if (!model || !isModelAvailable(model)) return; 
    selectedModelId = id;
    if (!model.supportsThinking) isThinkingEnabled = false;
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

    const currentModel = AVAILABLE_MODELS.find(m => m.id === selectedModelId);
    if ($("current-model-label") && currentModel) {
        $("current-model-label").textContent = currentModel.name;
    }

    if (!isModelDropdownOpen) {
        menu.classList.add("hidden");
        return;
    }

    menu.classList.remove("hidden");

    const ollamaModels = AVAILABLE_MODELS.filter(m => m.provider === "ollama");
    const geminiModels = AVAILABLE_MODELS.filter(m => m.provider === "gemini");

    const renderOption = (m, available) => {
        const isSelected = m.id === selectedModelId;
        const unavailableLabel = available ? "" : `<span class="text-[10px] text-muted opacity-60 ml-1">unavailable</span>`;
        const tooltip = available ? "" : `title="${m.provider === 'ollama' ? 'Ollama not available on this server' : 'GEMINI_API_KEY not configured'}"`;

        const icon = m.provider === "gemini"
            ? `<svg class="w-3.5 h-3.5 flex-shrink-0" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg"><path d="M12 2a10 10 0 100 20A10 10 0 0012 2zm1 14.5V13h3l-4-7v5H9l4 7z"/></svg>`
            : `<svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>`;

        const clickHandler = available
            ? `onclick="selectModel('${m.id}', event)"`
            : `onclick="event.stopPropagation()"`;

        return `
            <button ${clickHandler} ${tooltip} role="menuitem" tabindex="0"
                    class="w-full flex items-center justify-between px-3 py-2.5 text-left transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-brand outline-none
                           ${available ? 'hover:bg-hover cursor-pointer' : 'cursor-not-allowed opacity-40'}
                           ${isSelected && available ? 'bg-surface-2' : ''}">
                <div class="flex items-center gap-2">
                    <span class="${isSelected && available ? 'text-brand' : 'text-muted'}">${icon}</span>
                    <span class="${isSelected && available ? 'text-primary font-medium' : 'text-primary opacity-80'}">${m.name}</span>
                    ${unavailableLabel}
                </div>
                ${isSelected && available ? '<svg class="w-4 h-4 text-success" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="3" d="M5 13l4 4L19 7"/></svg>' : ''}
            </button>
        `;
    };

    const renderThinkingToggle = () => `
        <div class="flex items-center justify-between px-3 py-2.5 bg-surface border-y border-border mb-1" onclick="event.stopPropagation()">
            <span class="text-xs text-primary font-medium pl-1">Thinking</span>
            <label class="relative inline-flex items-center cursor-pointer" onclick="toggleThinkingMode(event)">
                <input type="checkbox" class="sr-only peer" ${isThinkingEnabled ? 'checked' : ''} onclick="event.stopPropagation()">
                <div class="w-7 h-4 bg-hover peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-3 peer-checked:after:bg-inverse after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-muted after:border-transparent after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-success"></div>
            </label>
        </div>
    `;

    let html = "";

    if (geminiModels.length > 0) {
        html += `
            <div class="px-3 py-1.5 text-[10px] text-muted uppercase tracking-widest font-semibold flex items-center gap-1.5">
                <svg class="w-3 h-3" viewBox="0 0 24 24" fill="currentColor"><path d="M6.5 2h11l3 5-10 15L.5 7zm1.7 2l-5 7.5h13.6l-5-7.5z"/></svg>
                Cloud
            </div>`;
        geminiModels.forEach(m => {
            html += renderOption(m, geminiAvailable);
            if (m.id === selectedModelId && m.supportsThinking && geminiAvailable) {
                html += renderThinkingToggle();
            }
        });
    }

    if (ollamaModels.length > 0) {
        const borderClass = geminiModels.length > 0 ? "border-t border-border mt-1 pt-1" : "";
        html += `
            <div class="px-3 py-1.5 text-[10px] text-muted uppercase tracking-widest font-semibold flex items-center gap-1.5 ${borderClass}">
                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/></svg>
                Local
            </div>`;
        ollamaModels.forEach(m => {
            html += renderOption(m, ollamaAvailable);
            if (m.id === selectedModelId && m.supportsThinking && ollamaAvailable) {
                html += renderThinkingToggle();
            }
        });
    }

    menu.innerHTML = html;
}
