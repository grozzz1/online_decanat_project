const $ = (id) => document.getElementById(id);
const navButtons = Array.from(document.querySelectorAll("[data-mode]"));
const descriptions = {
    question: ["Вопрос деканату", "Напиши свой вопрос. Ответ преподавателя появится здесь, в чате."],
    application: ["Отправка заявления", "Прикрепи фото заявления на академический отпуск и отправь его в деканат."],
    application_question: ["Вопрос по заявлению", "Выбери документ, прикрепи фото и напиши, что хочешь уточнить."],
};
const documentTypes = Array.from($("document-type").options, (option) => option.value);
let mode = "question";
let csrf = "";
let selectedFile = null;
let previewUrl = null;
let zoom = 100;
let lastMessage = 0;
let activeRequest = null;
let completedRequest = null;
let busy = false;
let polling = false;
let pollTimer = null;

function showError(text) {
    $("form-error").textContent = text;
    $("form-error").hidden = !text;
}

async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (options.method === "POST") headers.set("X-CSRF-Token", csrf);
    const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
    const result = await response.json();
    if (response.status === 401) {
        window.location.replace("/login");
        throw new Error(result.error);
    }
    if (!response.ok) throw new Error(result.error || "Не удалось выполнить запрос.");
    return result;
}

function setBusy(value) {
    busy = value;
    $("send-request").disabled = value;
    $("question").disabled = value;
    $("document").disabled = value;
    $("document-type").disabled = value;
    $("remove-file").disabled = value;
    $("send-request").textContent = value ? "Отправляем…" : mode === "application" ? "📝 Отправить заявление" : "✉ Отправить вопрос";
}

function setMode(value, audit = true) {
    if (!descriptions[value]) return;
    mode = value;
    sessionStorage.setItem("decanat-mode", mode);
    const isQuestion = mode === "question";
    $("workspace").classList.toggle("is-question", isQuestion);
    $("preview-card").hidden = isQuestion;
    $("document-type-field").hidden = isQuestion;
    $("upload-field").hidden = isQuestion;
    $("question-field").hidden = mode === "application";
    $("question").placeholder = isQuestion ? "Например: когда можно забрать справку об обучении?" : "Например: правильно ли заполнено заявление?";
    $("page-title").textContent = descriptions[mode][0];
    $("page-description").textContent = descriptions[mode][1];
    $("form-hint").textContent = mode === "application" ? "Фото и распознанный текст будут переданы в деканат." : "Ответ придёт в этот чат. Можно вернуться на страницу позже.";
    $("request-tip").textContent = isQuestion ? "Опиши ситуацию и укажи детали, которые помогут деканату ответить." : "Сфотографируй весь документ при хорошем освещении. Текст должен быть чётким.";
    document.querySelector(".chat-step").textContent = isQuestion ? "02" : "03";
    navButtons.forEach((button) => {
        const active = button.dataset.mode === mode;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
    const types = mode === "application" ? [$("application-only").textContent] : documentTypes;
    $("document-type").replaceChildren(...types.map((name) => new Option(name, name)));
    showError("");
    setBusy(busy);
    if (audit) api("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) }).catch((error) => showError(error.message));
}

function setZoom(value) {
    zoom = Math.max(50, Math.min(250, value));
    $("zoom-value").textContent = `${zoom}%`;
    $("preview-image").style.maxWidth = zoom === 100 ? "100%" : "none";
    $("preview-image").style.maxHeight = zoom === 100 ? "100%" : "none";
    $("preview-image").style.width = zoom === 100 ? "auto" : `${zoom}%`;
}

function showPreview(url) {
    if ($("preview-image").getAttribute("src") === url && !$("preview-image").hidden) return;
    $("preview-image").src = url;
    $("preview-image").hidden = false;
    $("preview-empty").hidden = true;
    setZoom(100);
}

function clearFile() {
    selectedFile = null;
    $("document").value = "";
    $("upload-title").textContent = "Перетащи фото сюда";
    $("upload-caption").textContent = "или выбери с компьютера";
    $("remove-file").hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
}

function chooseFile(file) {
    if (busy || !file) return;
    if (!["image/jpeg", "image/png"].includes(file.type)) return showError("Выбери фото в формате JPG или PNG.");
    if (file.size > 10 * 1024 * 1024) return showError("Фото должно быть не больше 10 МБ.");
    clearFile();
    selectedFile = file;
    previewUrl = URL.createObjectURL(file);
    showPreview(previewUrl);
    $("upload-title").textContent = file.name;
    $("upload-caption").textContent = "Фото готово к отправке";
    $("remove-file").hidden = false;
    $("recognized-text").textContent = "После отправки здесь появится распознанный текст документа.";
    $("recognized-text").classList.remove("has-text");
    $("ocr-status").textContent = "Распознавание";
    showError("");
}

function addMessage(message) {
    $("chat-empty").hidden = true;
    const container = $("chat-messages");
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    const entry = document.createElement("article");
    entry.className = `chat-message ${message.role}`;
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = { student: "Ты", bot: "Онлайн-деканат", dean: "Ответ деканата" }[message.role];
    const body = document.createElement("div");
    body.className = "message-body";
    if (message.photo) {
        const button = document.createElement("button");
        button.className = "message-photo";
        button.type = "button";
        button.setAttribute("aria-label", "Открыть фото документа");
        const image = document.createElement("img");
        image.src = `/api/requests/${encodeURIComponent(message.request_id)}/photo`;
        image.alt = "Отправленное фото документа";
        button.append(image);
        button.addEventListener("click", () => {
            $("dialog-photo").src = image.src;
            $("photo-dialog").showModal();
        });
        body.append(button);
    }
    body.append(document.createTextNode(message.text));
    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = message.time;
    entry.append(author, body, time);
    container.append(entry);
    lastMessage = message.id;
    if (nearBottom || message.role === "student") container.scrollTop = container.scrollHeight;
}

async function poll() {
    if (polling) return;
    polling = true;
    clearTimeout(pollTimer);
    try {
        const result = await api(`/api/messages?after=${lastMessage}`);
        result.messages.forEach(addMessage);
        const processing = result.requests.some((item) => item.status === "processing");
        setBusy(processing);
        $("connection-status").textContent = "Чат подключён. Ответы появятся автоматически.";
        $("chat-status").textContent = processing ? "Отправляем…" : "Ответы здесь";
        const document = [...result.requests].reverse().find((item) => item.mode !== "question");
        if (document && !selectedFile) {
            showPreview(`/api/requests/${encodeURIComponent(document.id)}/photo`);
            $("recognized-text").textContent = document.recognized || (document.status === "processing" ? "Распознаём текст с фото…" : document.status === "failed" ? "Не удалось обработать документ. Попробуй отправить его ещё раз." : "Не удалось распознать текст.");
            $("recognized-text").classList.toggle("has-text", Boolean(document.recognized));
            $("ocr-status").textContent = document.status === "processing" ? "В процессе" : document.status === "failed" ? "Ошибка" : "Готово";
        }
        const current = result.requests.find((item) => item.id === activeRequest);
        if (current && current.status !== "processing" && completedRequest !== current.id) {
            completedRequest = current.id;
            if (current.status === "failed") showError("Обращение не отправилось. Попробуй ещё раз.");
            else {
                $("question").value = "";
                clearFile();
                if (current.mode !== "question") {
                    showPreview(`/api/requests/${encodeURIComponent(current.id)}/photo`);
                    $("recognized-text").textContent = current.recognized || "Не удалось распознать текст.";
                    $("recognized-text").classList.toggle("has-text", Boolean(current.recognized));
                    $("ocr-status").textContent = "Готово";
                }
            }
        }
    } catch (error) {
        $("connection-status").textContent = "Связь прервалась. Пробуем подключиться снова…";
    } finally {
        polling = false;
        pollTimer = setTimeout(poll, document.hidden ? 10000 : 2000);
    }
}

navButtons.forEach((button) => button.addEventListener("click", () => setMode(button.dataset.mode)));
$("document").addEventListener("change", (event) => chooseFile(event.target.files[0]));
$("remove-file").addEventListener("click", () => {
    clearFile();
    $("preview-image").hidden = true;
    $("preview-empty").hidden = false;
});
$("zoom-out").addEventListener("click", () => setZoom(zoom - 25));
$("zoom-in").addEventListener("click", () => setZoom(zoom + 25));
$("close-photo").addEventListener("click", () => $("photo-dialog").close());
$("profile-button").addEventListener("click", () => {
    const open = $("profile-popover").hidden;
    $("profile-popover").hidden = !open;
    $("profile-button").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
    if (!event.target.closest(".profile")) {
        $("profile-popover").hidden = true;
        $("profile-button").setAttribute("aria-expanded", "false");
    }
});
$("logout").addEventListener("click", async () => {
    try {
        await api("/api/logout", { method: "POST" });
        window.location.replace("/login");
    } catch (error) { showError(error.message); }
});
for (const type of ["dragenter", "dragover"]) {
    $("dropzone").addEventListener(type, (event) => {
        event.preventDefault();
        if (!busy) $("dropzone").classList.add("dragging");
    });
}
for (const type of ["dragleave", "drop"]) $("dropzone").addEventListener(type, () => $("dropzone").classList.remove("dragging"));
$("dropzone").addEventListener("drop", (event) => {
    event.preventDefault();
    if (event.dataTransfer.files.length !== 1) return showError("Прикрепи одно фото документа.");
    chooseFile(event.dataTransfer.files[0]);
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
$("request-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const text = $("question").value.trim();
    if (mode !== "application" && (!text || text.startsWith("/"))) return showError("Напиши вопрос обычным текстом.");
    if (mode !== "question" && !selectedFile) return showError("Прикрепи одно фото документа.");
    const data = new FormData();
    data.append("mode", mode);
    data.append("text", mode === "application" ? "" : text);
    data.append("application_type", mode === "question" ? "" : $("document-type").value);
    if (selectedFile && mode !== "question") data.append("photo", selectedFile);
    showError("");
    setBusy(true);
    try {
        const result = await api("/api/requests", { method: "POST", body: data });
        activeRequest = result.request_id;
        await poll();
    } catch (error) {
        showError(error.message);
        setBusy(false);
    }
});

async function start() {
    try {
        const result = await api("/api/session");
        csrf = result.csrf;
        $("profile-name").textContent = result.user.name;
        $("profile-detail").textContent = result.user.name;
        if (result.user.photo) {
            const photo = new Image();
            photo.alt = "Фото профиля Telegram";
            photo.onload = () => $("avatar").replaceChildren(photo);
            photo.src = "/api/profile/photo";
        }
        setMode(sessionStorage.getItem("decanat-mode") || "question", false);
        await poll();
    } catch (error) { showError("Не удалось загрузить страницу. Обнови её чуть позже."); }
}
start();
