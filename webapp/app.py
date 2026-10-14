from functools import wraps
from hmac import compare_digest
from io import BytesIO
from secrets import token_hex
from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.serving import WSGIRequestHandler, make_server
from utils.keyboards import APPLICATIONS, APPLICATION_QUESTIONS, APPLICATION_QUESTION, DEAN_QUESTION, SEND_APPLICATION
from utils.users import get_name
from utils.documents import validate_document
from .service import WebService


MODES = {"question": DEAN_QUESTION, "application": SEND_APPLICATION, "application_question": APPLICATION_QUESTION}


def create_app(bot, loop, dean_chat_id):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=token_hex(32),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024 + 64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="decanat_session",
    )
    app.json.ensure_ascii = False
    service = WebService(bot, loop, dean_chat_id)
    app.extensions["decanat"] = service
    bot.web = service

    def signed_in(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            login = service.store.get_session(session.get("login"))

            if login is None:
                session.clear()

                if request.path.startswith("/api/"):
                    return jsonify(error="Сессия завершена. Нажми /start в боте и открой новую ссылку."), 401

                return redirect(url_for("login"))

            g.login = login

            if request.method == "POST":
                csrf = request.headers.get("X-CSRF-Token", "")
                origin = request.headers.get("Origin")

                if not compare_digest(csrf.encode(), login["csrf"].encode()) or (origin and origin != request.host_url.rstrip("/")):
                    abort(403)

            return view(*args, **kwargs)

        return wrapped

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"

        if response.status_code in (400, 409) and getattr(g, "login", None) is not None:
            details = response.get_json(silent=True) or {}
            service.call(service.audit(g.login["user_id"], "Ошибка запроса на сайте", f"{request.path}\n{details.get('error', '')}"))

        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        code = (request.args.get("code") if request.method == "GET" else request.form.get("code", "")) or ""
        error = None

        if code:
            token = service.store.login(code.strip())

            if token is not None:
                session.clear()
                session["login"] = token
                session.permanent = True
                user_id = service.store.get_session(token)["user_id"]
                service.call(service.audit(user_id, "Вход на сайт"))
                return redirect(url_for("index"))

            error = "Этот код уже использован или недействителен. Нажми /start в боте и открой новую ссылку."

        return render_template("login.html", error=error)

    @app.get("/")
    @signed_in
    def index():
        service.call(service.audit(g.login["user_id"], "Открыта страница сайта"))
        return render_template("index.html", modes=MODES, applications=APPLICATIONS, application_questions=APPLICATION_QUESTIONS)

    @app.get("/api/session")
    @signed_in
    def session_info():
        user_id = g.login["user_id"]
        user = service.store.get_user(user_id)
        return jsonify(user={"name": get_name(user), "username": user.username, "photo": service.store.get_avatar(user_id) is not None}, csrf=g.login["csrf"])

    @app.get("/api/profile/photo")
    @signed_in
    def profile_photo():
        photo = service.store.get_avatar(g.login["user_id"])

        if photo is None:
            abort(404)

        return send_file(BytesIO(photo), mimetype="image/jpeg", download_name="profile.jpg")

    @app.get("/api/messages")
    @signed_in
    def messages():
        after = request.args.get("after", default=0, type=int)
        return jsonify(service.store.snapshot(g.login["user_id"], max(0, after)))

    @app.post("/api/action")
    @signed_in
    def action():
        data = request.get_json(silent=True)
        mode = data.get("mode") if isinstance(data, dict) else None

        if mode not in MODES:
            return jsonify(error="Выбери пункт меню."), 400

        service.call(service.audit(g.login["user_id"], "Нажатие кнопки на сайте", MODES[mode]))
        return jsonify(ok=True)

    @app.post("/api/requests")
    @signed_in
    def submit_request():
        mode = request.form.get("mode", "")
        text = request.form.get("text", "").strip()
        application_type = request.form.get("application_type", "")
        photo = None
        mimetype = None

        if mode not in MODES:
            return jsonify(error="Выбери пункт меню."), 400

        if mode != "application" and (not text or text.startswith("/") or len(text) > 4096):
            return jsonify(error="Напиши вопрос обычным текстом, до 4096 символов."), 400

        if mode != "question":
            if mode == "application":
                text = ""

            types = APPLICATIONS if mode == "application" else APPLICATION_QUESTIONS

            if application_type not in types:
                return jsonify(error="Выбери тип документа."), 400

            uploaded = request.files.get("photo")

            if uploaded is None:
                return jsonify(error="Прикрепи один документ JPG, PNG или PDF."), 400

            photo = uploaded.read()

            if not photo or len(photo) > 10 * 1024 * 1024:
                return jsonify(error="Документ должен быть не больше 10 МБ."), 400

            try:
                mimetype = validate_document(photo)
            except ValueError as error:
                return jsonify(error=str(error)), 400
        else:
            application_type = ""

        try:
            request_id = service.call(service.submit(g.login["user_id"], mode, text, application_type, photo, mimetype))
        except ValueError as error:
            return jsonify(error=str(error)), 409

        return jsonify(request_id=request_id), 202

    @app.get("/api/requests/<request_id>/photo")
    @signed_in
    def request_photo(request_id):
        item = service.store.get_request(g.login["user_id"], request_id)

        if item is None or item["photo"] is None:
            abort(404)

        return send_file(BytesIO(item["photo"]), mimetype=item["mimetype"])

    @app.get("/api/requests/<request_id>/pages/<int:page>/photo")
    @signed_in
    def request_page(request_id, page):
        item = service.store.get_request(g.login["user_id"], request_id)
        if item is None or page < 1:
            abort(404)
        if page <= len(item["previews"]):
            return send_file(BytesIO(item["previews"][page - 1]), mimetype="image/jpeg")
        if page == 1 and item["photo"] and item["mimetype"] != "application/pdf":
            return send_file(BytesIO(item["photo"]), mimetype=item["mimetype"])
        abort(404)

    @app.post("/api/logout")
    @signed_in
    def logout():
        service.call(service.audit(g.login["user_id"], "Выход с сайта"))
        service.store.logout(session.get("login"))
        session.clear()
        return jsonify(ok=True)

    @app.errorhandler(Exception)
    def handle_error(error):
        login = service.store.get_session(session.get("login"))
        service.call(service.report_error(error, login["user_id"] if login is not None else None))
        status = error.code if isinstance(error, HTTPException) else 500
        text = "Не удалось обработать запрос. Попробуй ещё раз чуть позже."

        if isinstance(error, RequestEntityTooLarge):
            text = "Документ должен быть не больше 10 МБ."
        elif status == 403:
            text = "Обнови страницу и попробуй ещё раз."
        elif status == 404:
            text = "Страница или фото не найдены."

        if request.path.startswith("/api/"):
            return jsonify(error=text), status

        return render_template("login.html", error=text), status

    return app


class WebRequestHandler(WSGIRequestHandler):
    def log(self, type, message, *args):
        pass


def run_server(app, bot, loop):
    try:
        server = make_server("127.0.0.1", 5000, app, threaded=True, request_handler=WebRequestHandler)
        loop.call_soon_threadsafe(bot.write_log, "Сайт запущен: http://localhost:5000")
        server.serve_forever()
    except (OSError, SystemExit) as error:
        loop.call_soon_threadsafe(bot.log_error, error, "Ошибка запуска сайта")
