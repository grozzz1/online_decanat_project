const $ = (id) => document.getElementById(id);
const navButtons = Array.from(document.querySelectorAll("[data-mode]"));
const descriptions = {
    question: ["Вопрос деканату", "Напиши свой вопрос. Ответ преподавателя появится здесь, в чате."],
    application: ["Отправка заявления", "Прикрепи заявление в формате JPG, PNG или PDF и отправь его в деканат."],
    application_question: ["Вопрос по заявлению", "Выбери документ, прикрепи файл и напиши, что хочешь уточнить."],
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
let currentVisuals = null;
let previewPage = 1;
let displayedRequest = null;
let requestsById = new Map();
let viewerPinned = false;

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
    $("form-hint").textContent = mode === "application" ? "Документ, распознанный текст и результаты анализа будут переданы в деканат." : "Ответ придёт в этот чат. Можно вернуться на страницу позже.";
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
    const image = $("preview-image");
    const container = $("document-preview");
    const fit = Math.min((container.clientWidth - 20) / (image.naturalWidth || 300),
        (container.clientHeight - 20) / (image.naturalHeight || 400), 1);
    $("preview-stage").style.width = `${Math.max(1, (image.naturalWidth || 300) * fit * zoom / 100)}px`;
    drawOverlay();
}

function showPreview(url) {
    if ($("preview-image").getAttribute("src") === url && !$("preview-stage").hidden) return;
    $("preview-image").src = url;
    $("preview-stage").hidden = false;
    $("preview-empty").hidden = true;
    setZoom(100);
}

function drawOverlay() {
    const image = $("preview-image");
    const canvas = $("visual-overlay");
    const width = image.clientWidth;
    const height = image.clientHeight;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    const context = canvas.getContext("2d");
    if (!width || !height || !image.complete || !currentVisuals || !$("show-visuals").checked) return;
    const page = currentVisuals.pages.find((item) => item.page === previewPage);
    if (!page) return;
    context.scale(ratio, ratio);
    context.lineWidth = 2;
    context.font = "11px Segoe UI";
    for (const item of currentVisuals.detections.filter((item) => item.page === previewPage)) {
        const [x1, y1, x2, y2] = item.bbox;
        const x = x1 * width / page.width;
        const y = y1 * height / page.height;
        context.strokeStyle = item.status === "uncertain" ? "#e2ad00" : item.type === "stamp" ? "#18a34a" : "#3375ec";
        context.strokeRect(x, y, (x2 - x1) * width / page.width, (y2 - y1) * height / page.height);
        context.fillStyle = context.strokeStyle;
        const label = item.type === "stamp" ? "Печать" : item.role === "student" ? "Подпись студента" : "Подпись (?)";
        context.fillText(label, x, Math.max(12, y - 4));
    }
}

function showVisualResults(visuals) {
    const target = $("visual-results");
    target.replaceChildren();
    if (!visuals) {
        target.textContent = "Анализ печатей и подписей появится после обработки документа.";
        return;
    }
    const summary = document.createElement("p");
    const unavailable = visuals.pages.some((page) => page.analysis_status === "unavailable");
    summary.textContent = unavailable ? "Визуальный анализ выполнен не для всех страниц." : "Визуальный анализ завершён.";
    const list = document.createElement("ul");
    for (const [key, label] of [["stamp", "Печать"], ["student_signature", "Подпись студента"]]) {
        const entry = document.createElement("li");
        const result = visuals[key];
        entry.textContent = `${label}: ${result.detected ? result.status === "uncertain" ? "сомнительный кандидат" : "найдены визуальные признаки" : result.status === "unknown" ? "не удалось определить" : "не обнаружена алгоритмом"}.`;
        list.append(entry);
    }
    for (const item of visuals.detections) {
        const entry = document.createElement("li");
        const label = item.type === "stamp" ? "Печать" : item.role === "student" ? "Подпись студента" : "Подпись, роль неизвестна";
        entry.textContent = `${label} · стр. ${item.page} · score ${item.confidence.toFixed(2)}${item.status === "uncertain" ? " · требуется проверка" : ""}`;
        list.append(entry);
    }
    const note = document.createElement("p");
    note.textContent = "Оценка эвристическая. Подлинность не проверяется; отсутствие признаков не запрещает отправку.";
    const legend = document.createElement("div");
    legend.className = "visual-legend";
    for (const [className, text] of [["stamp", "Зелёный — печать"], ["signature", "Синий — подпись"], ["uncertain", "Жёлтый — сомнение"]]) {
        const label = document.createElement("span");
        label.className = className;
        label.textContent = text;
        legend.append(label);
    }
    target.append(summary, list, note, legend);
    for (const warning of visuals.warnings) {
        const text = document.createElement("p");
        text.textContent = warning;
        target.append(text);
    }
}

function showDocument(item) {
    if (displayedRequest !== item.id) {
        previewPage = 1;
        displayedRequest = item.id;
    }
    currentVisuals = item.visuals;
    const pages = currentVisuals ? currentVisuals.pages : [];
    if ($("preview-page").options.length !== pages.length) {
        $("preview-page").replaceChildren(...pages.map((page) => new Option(String(page.page), String(page.page))));
    }
    $("preview-page").value = String(previewPage);
    $("page-field").hidden = pages.length <= 1;
    $("original-document").hidden = false;
    $("original-document").href = `/api/requests/${encodeURIComponent(item.id)}/photo`;
    $("original-document").target = "_blank";
    $("original-document").rel = "noopener";
    if (pages.length || item.mimetype !== "application/pdf") {
        showPreview(`/api/requests/${encodeURIComponent(item.id)}/pages/${previewPage}/photo${pages.length ? "?analyzed=1" : ""}`);
    } else {
        $("preview-stage").hidden = true;
        $("preview-empty").hidden = false;
        $("preview-empty").querySelector("strong").textContent = "Предпросмотр PDF появится после обработки";
    }
    $("recognized-text").textContent = item.recognized || (item.status === "processing" ? "Распознаём текст документа…" : item.status === "failed" ? "Не удалось обработать документ. Попробуй отправить его ещё раз." : "Не удалось распознать текст.");
    $("recognized-text").classList.toggle("has-text", Boolean(item.recognized));
    $("ocr-status").textContent = item.status === "processing" ? "В процессе" : item.status === "failed" ? "Ошибка" : "Готово";
    showVisualResults(currentVisuals);
    drawOverlay();
}

function clearFile() {
    selectedFile = null;
    $("document").value = "";
    $("upload-title").textContent = "Перетащи документ сюда";
    $("upload-caption").textContent = "или выбери с компьютера";
    $("remove-file").hidden = true;
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    currentVisuals = null;
    displayedRequest = null;
    previewPage = 1;
    $("page-field").hidden = true;
    $("original-document").hidden = true;
    showVisualResults(null);
    drawOverlay();
}

function chooseFile(file) {
    if (busy || !file) return;
    if (!["image/jpeg", "image/png", "application/pdf"].includes(file.type) && !/\.(pdf|jpe?g|png)$/i.test(file.name)) return showError("Выбери документ в формате JPG, PNG или PDF.");
    if (file.size > 10 * 1024 * 1024) return showError("Документ должен быть не больше 10 МБ.");
    clearFile();
    selectedFile = file;
    viewerPinned = false;
    if (file.type === "application/pdf" || /\.pdf$/i.test(file.name)) {
        $("preview-stage").hidden = true;
        $("preview-empty").hidden = false;
        $("preview-empty").querySelector("strong").textContent = "Предпросмотр PDF появится после отправки";
    } else {
        previewUrl = URL.createObjectURL(file);
        showPreview(previewUrl);
    }
    $("upload-title").textContent = file.name;
    $("upload-caption").textContent = "Документ готов к отправке";
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
        button.setAttribute("aria-label", "Посмотреть документ и результаты анализа");
        button.textContent = "📄 Посмотреть документ";
        button.addEventListener("click", () => {
            const item = requestsById.get(message.request_id);
            if (!item || busy) return;
            clearFile();
            viewerPinned = true;
            setMode(item.mode, false);
            showDocument(item);
            $("preview-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
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
        requestsById = new Map(result.requests.map((item) => [item.id, item]));
        result.messages.forEach(addMessage);
        const processing = result.requests.some((item) => item.status === "processing");
        setBusy(processing);
        $("connection-status").textContent = "Чат подключён. Ответы появятся автоматически.";
        $("chat-status").textContent = processing ? "Отправляем…" : "Ответы здесь";
        const latestDocument = viewerPinned ? requestsById.get(displayedRequest) : [...result.requests].reverse().find((item) => item.mode !== "question");
        if (latestDocument && !selectedFile) showDocument(latestDocument);
        const current = result.requests.find((item) => item.id === activeRequest);
        if (current && current.status !== "processing" && completedRequest !== current.id) {
            completedRequest = current.id;
            if (current.status === "failed") showError("Обращение не отправилось. Попробуй ещё раз.");
            else {
                $("question").value = "";
                clearFile();
                if (current.mode !== "question") {
                    showDocument(current);
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
    $("preview-stage").hidden = true;
    $("preview-empty").hidden = false;
});
$("preview-image").addEventListener("load", () => setZoom(zoom));
$("show-visuals").addEventListener("change", drawOverlay);
$("preview-page").addEventListener("change", () => {
    previewPage = Number($("preview-page").value);
    const item = requestsById.get(displayedRequest);
    if (item) showDocument(item);
});
new ResizeObserver(() => setZoom(zoom)).observe($("document-preview"));
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
    if (event.dataTransfer.files.length !== 1) return showError("Прикрепи один документ.");
    chooseFile(event.dataTransfer.files[0]);
});
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());
$("request-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const text = $("question").value.trim();
    if (mode !== "application" && (!text || text.startsWith("/"))) return showError("Напиши вопрос обычным текстом.");
    if (mode !== "question" && !selectedFile) return showError("Прикрепи один документ.");
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
        clearFile();
        viewerPinned = false;
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
