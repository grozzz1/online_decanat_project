const $ = (id) => document.getElementById(id);
window.Telegram?.WebApp?.ready();
window.Telegram?.WebApp?.expand();
const navButtons = Array.from(document.querySelectorAll("[data-mode]"));
let allDocumentTypes = Array.from($("document-type").options, (option) => ({
    value: option.value, label: option.textContent, modes: option.dataset.modes.split(","),
}));
const descriptions = {
    question: ["Вопрос деканату", "Напиши свой вопрос. Ответ преподавателя появится здесь, в чате."],
    application: ["Отправка заявления", "Выбери тип документа, прикрепи JPG, PNG или PDF и отправь его в деканат."],
    application_question: ["Вопрос по заявлению", "Прикрепи документ и задай вопрос AI-помощнику. Ответ будет проверен по документу и Положению."],
};
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
let reviewRequestId = null;
let editingType = false;
let actionInFlight = false;
let aiSubmitting = false;
let messageHistory = [];
let dialogUrl = null;
const fieldLabels = {
    student_name: "ФИО студента", university: "Название вуза", program: "Направление подготовки",
    study_form: "Форма обучения", study_start: "Начало обучения", study_end: "Окончание обучения",
    document_number: "Номер документа", issue_date: "Дата выдачи", signature: "Подпись", stamp: "Печать",
    group: "Учебная группа", course: "Курс", leave_reason: "Причина отпуска", leave_start: "Начало отпуска",
    leave_end: "Окончание отпуска", application_date: "Дата заявления", student_signature: "Подпись студента",
    organization: "Организация", internship_start: "Начало практики", internship_end: "Окончание практики",
    supervisor_name: "ФИО руководителя", grade: "Оценка",
};

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
    const review = requestsById.get(reviewRequestId);
    const mismatch = review?.document_type_matches_selection === false && !review.type_mismatch_confirmed;
    $("send-request").disabled = value || (mode !== "question" && mismatch && !editingType);
    navButtons.forEach((button) => { button.disabled = value; });
    $("question").disabled = value;
    $("document").disabled = value;
    $("document-type").disabled = value || review?.status === "failed";
    $("remove-file").disabled = value;
    $("override-document-type").disabled = value;
    $("override-document-type").textContent = value ? "Отправляем…" : "Подтвердить выбранный тип";
    $("confirm-document").disabled = value || (mismatch && !editingType);
    $("send-request").textContent = value ? "Обрабатываем…" : mode === "application" ? "📝 Отправить заявление" : mode === "application_question" ? "Задать вопрос" : "✉ Отправить вопрос";
}

function renderDocumentTypes() {
    const selectedType = $("document-type").value;
    const types = allDocumentTypes.filter((item) => mode === "question" || item.modes.includes(mode));
    $("document-type").replaceChildren(...types.map((item) => new Option(item.label, item.value)));
    $("document-type").value = types.some((item) => item.value === selectedType) ? selectedType : types.some((item) => item.value === "academic_leave_application") ? "academic_leave_application" : types[0]?.value || "";
}

function setMode(value, audit = true) {
    if (!descriptions[value]) return;
    if (mode !== value) {
        reviewRequestId = null;
        viewerPinned = false;
        editingType = false;
    }
    mode = value;
    renderDocumentTypes();
    sessionStorage.setItem("decanat-mode", mode);
    const isQuestion = mode === "question";
    $("workspace").classList.toggle("is-question", isQuestion);
    $("preview-card").hidden = isQuestion;
    $("document-type-field").hidden = isQuestion;
    $("upload-field").hidden = isQuestion;
    $("question-field").hidden = mode === "application";
    $("ai-panel").hidden = mode !== "application_question";
    $("chat-heading").textContent = mode === "application_question" ? "Вопросы AI-помощнику" : "Чат с деканатом";
    $("question").placeholder = isQuestion ? "Например: когда можно забрать справку об обучении?" : "Например: правильно ли заполнено заявление?";
    $("page-title").textContent = descriptions[mode][0];
    $("page-description").textContent = descriptions[mode][1];
    $("form-hint").textContent = mode === "application_question" ? "Ответит AI-помощник с указанием источников. Документ сохранится для следующих вопросов." : mode === "application" ? "Документ, распознанный текст и результаты анализа будут переданы в деканат." : "Ответ сотрудника придёт в этот чат. Можно вернуться на страницу позже.";
    $("request-tip").textContent = isQuestion ? "Опиши ситуацию и укажи детали, которые помогут деканату ответить." : "Сфотографируй весь документ при хорошем освещении. Текст должен быть чётким.";
    document.querySelector(".chat-step").textContent = isQuestion ? "02" : "03";
    navButtons.forEach((button) => {
        const active = button.dataset.mode === mode;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
    });
    showError("");
    renderHistory();
    const latestDocument = [...requestsById.values()].reverse().find((item) => item.mode === mode && item.mode !== "question");
    if (latestDocument && !selectedFile) showDocument(latestDocument);
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

function showVisualResults(visuals, documentType = "academic_leave_application") {
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
    const signatureField = documentType === "academic_leave_application" ? ["student_signature", "Подпись студента"] : ["signature", "Подпись"];
    for (const [key, label] of [["stamp", "Печать"], signatureField]) {
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
    showVisualResults(currentVisuals, item.document_type);
    showTypeReview(item);
    showFieldResults(item);
    showAIResults(item);
    drawOverlay();
}

function showFieldResults(item) {
    const extraction = item.extraction;
    $("field-card").hidden = !extraction;
    $("field-results").replaceChildren();
    const statuses = extraction?.field_statuses || {};
    for (const [name, value] of Object.entries(extraction?.fields || {})) {
        const label = document.createElement("dt");
        label.textContent = fieldLabels[name] || name;
        const description = document.createElement("dd");
        const status = statuses[name];
        description.textContent = status === "found" ? "Найдено" : status === "missing" ? "Не найдено" : "Не удалось проверить";
        if (status === "found" && value !== null && typeof value !== "boolean") description.textContent += `: ${value}`;
        $("field-results").append(label, description);
    }
    const check = item.preliminary_check;
    $("validation-result").hidden = !check;
    if (check) {
        $("validation-result").dataset.status = check.status;
        $("validation-title").textContent = check.status === "document_ok" ? "Документ в порядке" : check.status === "unknown" ? "Проверка выполнена не полностью" : "Чего-то не хватает";
        const labels = (names) => names.map((name) => fieldLabels[name] || name).join(", ");
        const parts = [];
        if (check.missing_fields.length) parts.push(`Не найдены обязательные реквизиты: ${labels(check.missing_fields)}.`);
        if (check.unknown_fields.length) parts.push(`Не удалось проверить: ${labels(check.unknown_fields)}.`);
        if (check.status === "document_ok") parts.push("Документ содержит все обязательные сведения.");
        if (check.sources.length) parts.push(`Пункты Положения: ${check.sources.map((source) => source.replace("rule:", "")).join(", ")}.`);
        else parts.push("Для этого типа документа автоматические правила ещё не заданы.");
        parts.push("Это предварительная проверка. Окончательное решение примет сотрудник деканата.");
        $("validation-summary").textContent = parts.join(" ");
    }
}

function showAIResults(item) {
    $("ai-panel").hidden = mode !== "application_question" || item.mode !== "application_question";
    $("ai-status").textContent = item.ai_status === "processing" || aiSubmitting ? "Проверяем ответ и источники…" : item.ai_status === "failed" ? "Не удалось получить проверенный ответ. Попробуй ещё раз." : "";
    const result = item.ai_answer?.result;
    $("ai-answer").textContent = result ? result.answer + (result.sources.length ? `\nИсточники: ${result.sources.join(", ")}` : "") : "";
    if (item.mode === "application_question" && mode === "application_question" && item.status === "checked") {
        $("upload-title").textContent = item.filename || "Документ сохранён";
        $("upload-caption").textContent = "Можно задать следующий вопрос без повторной загрузки";
    }
}

function showTypeReview(item) {
    const pending = item.status === "needs_review";
    const retry = item.status === "failed" && item.retry_allowed;
    if ((pending || retry) && reviewRequestId !== item.id) {
        if (item.mode === "application_question") $("question").value = item.text || "";
        $("upload-title").textContent = item.filename || "Документ сохранён";
        $("upload-caption").textContent = "Файл сохранён в обращении";
    }
    if ((pending || retry) && reviewRequestId !== item.id &&
        Array.from($("document-type").options).some((option) => option.value === item.selected_document_type)) {
        $("document-type").value = item.selected_document_type;
    }
    reviewRequestId = pending || retry ? item.id : null;
    const mismatch = item.document_type_matches_selection === false;
    const unknown = item.detected_document_type === "unknown";
    $("document-title").hidden = !item.document_title;
    $("document-title").textContent = item.document_title ? `Название документа: ${item.document_title}` : "";
    $("type-notice").hidden = editingType || (!mismatch && !unknown) || item.type_mismatch_confirmed;
    $("type-notice").classList.toggle("neutral", unknown);
    const label = allDocumentTypes.find((entry) => entry.value === item.detected_document_type)?.label;
    $("type-notice-text").textContent = unknown
        ? "Не удалось уверенно определить тип документа. Проверь выбранный тип перед отправкой."
        : `Возможно, вы ошиблись в выборе типа документа. Вы отправили нам: „${label || item.document_title}“.`;
    $("change-document-type").hidden = !pending;
    $("override-document-type").hidden = !pending || !mismatch;
    $("review-actions").hidden = !pending && !retry;
    $("confirm-document").textContent = retry ? "Повторить отправку" : "Подтвердить и отправить";
    setBusy(busy);
}

async function applyStoredType(refresh = true) {
    if (!reviewRequestId) return;
    await api(`/api/requests/${encodeURIComponent(reviewRequestId)}/type`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ document_type: $("document-type").value, mode, text: $("question").value.trim() }),
    });
    editingType = false;
    $("type-change-hint").textContent = "Файл сохранён. Тип проверен без повторного распознавания.";
    if (refresh) await poll();
}

async function confirmStoredDocument(override = false) {
    if (busy || !reviewRequestId) return;
    if (mode === "application_question" && !$("question").value.trim()) return showError("Напиши вопрос по заявлению.");
    const requestId = reviewRequestId;
    actionInFlight = true;
    setBusy(true);
    showError("");
    try {
        const item = requestsById.get(requestId);
        if (item.status !== "failed") await applyStoredType(false);
        if (!override && item.status !== "failed" && item.document_type_matches_selection === false && $("document-type").value === item.selected_document_type) {
            editingType = false;
            showTypeReview(item);
            showError("Подтверди выбранный тип жёлтой кнопкой или измени его в списке.");
            return;
        }
        await api(`/api/requests/${encodeURIComponent(requestId)}/confirm`, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ type_mismatch_confirmed: override }),
        });
        activeRequest = requestId;
        completedRequest = null;
    } catch (error) {
        showError(error.message);
    } finally {
        actionInFlight = false;
        setBusy(false);
        await poll();
    }
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
    reviewRequestId = null;
    editingType = false;
    $("type-change-hint").textContent = "Файл сохранён. Проверь тип перед отправкой.";
    $("type-notice").hidden = true;
    $("review-actions").hidden = true;
    $("document-title").hidden = true;
    $("field-card").hidden = true;
    $("validation-result").hidden = true;
    $("ai-panel").hidden = true;
    $("ai-answer").textContent = "";
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

async function openMessagePhoto(message, body) {
    try {
        const response = await fetch(`/api/messages/${message.id}/photo`, { credentials: "same-origin" });
        if (!response.ok) {
            const result = await response.json();
            throw new Error(result.error || "Не удалось открыть вложение.");
        }
        const blob = await response.blob();
        const item = requestsById.get(message.request_id);
        if (item && !item.photo_expired) {
            clearFile();
            setMode(item.mode, false);
            viewerPinned = true;
            showDocument(item);
            $("preview-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
        } else {
            if (dialogUrl) URL.revokeObjectURL(dialogUrl);
            dialogUrl = URL.createObjectURL(blob);
            if (blob.type.startsWith("image/")) {
                $("dialog-photo").src = dialogUrl;
                $("photo-dialog").showModal();
            } else {
                const link = document.createElement("a");
                link.href = dialogUrl;
                link.download = message.media_tag || "document.pdf";
                link.click();
            }
        }
    } catch (error) {
        let notice = body.querySelector(".expired-photo");
        if (!notice) { notice = document.createElement("p"); notice.className = "expired-photo"; body.append(notice); }
        notice.textContent = error.message;
    }
}

function addMessage(message) {
    const request = requestsById.get(message.request_id);
    if (request && request.mode !== mode) return;
    $("chat-empty").hidden = true;
    const container = $("chat-messages");
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 100;
    const entry = document.createElement("article");
    entry.className = `chat-message ${message.role}`;
    const author = document.createElement("span");
    author.className = "message-author";
    author.textContent = { student: "Ты", bot: "Онлайн-деканат", dean: "Ответ деканата", ai: "AI-помощник" }[message.role];
    const body = document.createElement("div");
    body.className = "message-body";
    if (message.photo) {
        const button = document.createElement("button");
        button.className = "message-photo";
        button.type = "button";
        button.setAttribute("aria-label", "Посмотреть документ и результаты анализа");
        button.textContent = "📄 Посмотреть документ";
        button.addEventListener("click", () => { if (!busy) openMessagePhoto(message, body); });
        body.append(button);
    }
    body.append(document.createTextNode(message.text));
    const time = document.createElement("span");
    time.className = "message-time";
    time.textContent = message.time;
    entry.append(author, body, time);
    container.append(entry);
    if (nearBottom || message.role === "student") container.scrollTop = container.scrollHeight;
}

function renderHistory() {
    $("chat-messages").replaceChildren($("chat-empty"));
    $("chat-empty").hidden = false;
    $("chat-empty").querySelector("h3").textContent = mode === "application_question" ? "Задай вопрос по документу" : "Начнём с твоего обращения";
    $("chat-empty").querySelector("p").textContent = mode === "application_question" ? "Здесь будут вопросы и проверенные ответы AI-помощника." : "Здесь будут твои сообщения и ответы сотрудников деканата.";
    messageHistory.forEach(addMessage);
}

async function poll() {
    if (polling) return;
    polling = true;
    clearTimeout(pollTimer);
    try {
        const result = await api(`/api/messages?after=${lastMessage}`);
        requestsById = new Map(result.requests.map((item) => [item.id, item]));
        if (result.document_types) {
            const types = result.document_types.map((item) => ({ value: item.key, label: item.label, modes: item.modes }));
            if (JSON.stringify(types) !== JSON.stringify(allDocumentTypes)) { allDocumentTypes = types; renderDocumentTypes(); }
        }
        messageHistory.push(...result.messages);
        if (result.messages.length) lastMessage = result.messages.at(-1).id;
        if (result.messages.length) renderHistory();
        const processing = result.requests.some((item) => item.status === "processing" || item.ai_status === "processing");
        setBusy(processing || actionInFlight || aiSubmitting);
        $("connection-status").textContent = "Чат подключён. Ответы появятся автоматически.";
        $("chat-status").textContent = processing ? "Обрабатываем…" : result.requests.some((item) => item.status === "needs_review") ? "Нужно подтверждение" : "Ответы здесь";
        const latestDocument = viewerPinned ? requestsById.get(displayedRequest) : [...result.requests].reverse().find((item) => item.mode === mode && item.mode !== "question");
        if (latestDocument && !selectedFile) showDocument(latestDocument);
        const current = result.requests.find((item) => item.id === activeRequest);
        if (current && ["sent", "answered", "checked", "failed"].includes(current.status) && completedRequest !== current.id) {
            completedRequest = current.id;
            if (current.status === "failed") showError(current.failure_message || "Не удалось отправить документ.");
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
$("change-document-type").addEventListener("click", () => {
    if (busy || !reviewRequestId) return;
    editingType = true;
    $("type-notice").hidden = true;
    $("type-change-hint").textContent = "Выбери тип документа в списке. Файл сохранён.";
    setBusy(false);
    const select = $("document-type");
    select.focus();
    try { select.showPicker?.(); } catch (_) {}
});
$("document-type").addEventListener("change", async () => {
    if (busy || !reviewRequestId) return;
    actionInFlight = true;
    setBusy(true);
    try { await applyStoredType(false); showError(""); }
    catch (error) { showError(error.message); }
    finally { actionInFlight = false; setBusy(false); await poll(); }
});
$("override-document-type").addEventListener("click", () => confirmStoredDocument(true));
$("confirm-document").addEventListener("click", () => confirmStoredDocument(false));
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
        if (window.Telegram?.WebApp?.initData) { window.Telegram.WebApp.close(); return; }
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
    if (reviewRequestId && mode !== "question" && !selectedFile) return confirmStoredDocument();
    const text = mode === "application" ? "" : $("question").value.trim();
    if (mode !== "application" && (!text || text.startsWith("/"))) return showError("Напиши вопрос обычным текстом.");
    const retained = requestsById.get(displayedRequest);
    if (mode === "application_question" && !selectedFile && retained?.mode === mode && retained.status === "checked") {
        aiSubmitting = true;
        setBusy(true);
        showError("");
        try {
            await api(`/api/requests/${encodeURIComponent(retained.id)}/ask`, {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: text }),
            });
            $("question").value = "";
        } catch (error) { showError(error.message); }
        finally { aiSubmitting = false; setBusy(false); await poll(); }
        return;
    }
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
