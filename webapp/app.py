from functools import wraps
from hmac import compare_digest
from io import BytesIO
from os import getenv
from secrets import token_hex

from flask import Flask, abort, g, jsonify, redirect, render_template, request, send_file, session, url_for
from flask.sessions import SecureCookieSessionInterface
from telebot.types import User
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.serving import WSGIRequestHandler

from utils.config import get_public_base_url, get_token, get_web_binding
from utils.documents import load_document_pages, preview_bytes, validate_document
from utils.keyboards import DEAN_QUESTION
from utils.storage import MEDIA_EXPIRED
from utils.telegram_auth import validate_init_data
from utils.users import get_name

from .service import WebService

MODES = {
    "question": DEAN_QUESTION,
    "application": "📝 Отправить заявление",
    "application_question": "🔎 Задать вопрос по заявлению",
}


class TelegramSessionInterface(SecureCookieSessionInterface):
    def get_cookie_partitioned(self, app):
        return bool(
            app.config["SESSION_COOKIE_SECURE"]
            and (session.get("telegram_mini_app") or getattr(g, "telegram_mini_app", False))
        )

    def get_cookie_samesite(self, app):
        return "None" if self.get_cookie_partitioned(app) else "Lax"


def create_app(bot, loop, dean_chat_id, storage=None):
    app = Flask(__name__)
    app.session_interface = TelegramSessionInterface()
    app.config.update(
        SECRET_KEY=token_hex(32),
        MAX_CONTENT_LENGTH=10 * 1024 * 1024 + 64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_NAME="decanat_session",
    )
    app.json.ensure_ascii = False
    service = WebService(bot, loop, dean_chat_id, storage)
    app.config["SECRET_KEY"] = getenv("FLASK_SECRET_KEY") or service.store.storage.secret_key()
    app.config["SESSION_COOKIE_SECURE"] = get_public_base_url().startswith("https://")
    app.extensions["decanat"] = service
    bot.web = service

    @app.before_request
    def remember_session_kind():
        g.telegram_mini_app = bool(session.get("telegram_mini_app"))

    @app.get("/healthz")
    def health():
        with service.store.storage.connection() as connection:
            connection.exec_driver_sql("SELECT 1").fetchone()
        return jsonify(ok=True)

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

                if not compare_digest(csrf.encode(), login["csrf"].encode()) or (
                    origin and origin != request.host_url.rstrip("/")
                ):
                    abort(403)

            return view(*args, **kwargs)

        return wrapped

    @app.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        miniapp = (
            request.path == "/miniapp"
            or session.get("telegram_mini_app", False)
            or getattr(g, "telegram_mini_app", False)
        )
        if not miniapp:
            response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' blob:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if miniapp:
            response.headers["Content-Security-Policy"] = (
                response.headers["Content-Security-Policy"]
                .replace("script-src 'self'", "script-src 'self' https://telegram.org")
                .replace(
                    "frame-ancestors 'none'",
                    "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org",
                )
            )

        if response.status_code in (400, 409) and getattr(g, "login", None) is not None:
            details = response.get_json(silent=True) or {}
            service.call(
                service.audit(
                    g.login["user_id"],
                    "Ошибка запроса на сайте",
                    f"{request.path}\n{details.get('error', '')}",
                )
            )

        return response

    @app.get("/miniapp")
    def miniapp_login():
        return render_template("miniapp.html")

    @app.post("/api/auth/telegram")
    def telegram_login():
        origin = request.headers.get("Origin")
        if origin and origin != request.host_url.rstrip("/"):
            abort(403)
        data = request.get_json(silent=True)
        try:
            profile = validate_init_data(
                data.get("init_data") if isinstance(data, dict) else None, get_token()
            )
        except ValueError as error:
            return jsonify(error=str(error)), 401
        service.store.logout(session.get("login"))
        token = service.store.create_session(User.de_json(profile))
        session.clear()
        session.update(login=token, telegram_mini_app=True)
        session.permanent = True
        return jsonify(ok=True)

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
        return render_template(
            "index.html",
            modes=MODES,
            document_types=service.store.types.list(),
            telegram_mini_app=session.get("telegram_mini_app", False),
        )

    @app.get("/api/session")
    @signed_in
    def session_info():
        user_id = g.login["user_id"]
        user = service.store.get_user(user_id)
        return jsonify(
            user={
                "name": get_name(user),
                "username": user.username,
                "photo": service.store.get_avatar(user_id) is not None,
            },
            csrf=g.login["csrf"],
        )

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
        if mode == "general_question":
            mode = "question"
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

            selected = service.store.types.resolve(application_type, mode)
            if selected is None:
                return jsonify(error="Выбери тип документа."), 400
            application_type = selected["key"]

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
            request_id = service.call(
                service.submit(
                    g.login["user_id"],
                    mode,
                    text,
                    application_type,
                    photo,
                    mimetype,
                    filename=uploaded.filename if mode != "question" else "",
                )
            )
        except ValueError as error:
            return jsonify(error=str(error)), 409

        return jsonify(request_id=request_id), 202

    @app.get("/api/requests/<request_id>/photo")
    @signed_in
    def request_photo(request_id):
        item = service.store.get_request(g.login["user_id"], request_id)

        if item is None:
            abort(404)
        if item["photo"] is None:
            return jsonify(error=MEDIA_EXPIRED), 410

        return send_file(BytesIO(item["photo"]), mimetype=item["mimetype"])

    @app.get("/api/messages/<int:message_id>/photo")
    @signed_in
    def message_photo(message_id):
        item = service.store.storage.get_message(g.login["user_id"], message_id)
        if item is None or not item["media_tag"]:
            abort(404)
        data = service.store.storage.read_media(item["media_tag"], g.login["user_id"])
        if data is None:
            return jsonify(error=MEDIA_EXPIRED), 410
        return send_file(BytesIO(data), mimetype=item["mimetype"], download_name=item["media_tag"])

    @app.post("/api/requests/<request_id>/type")
    @signed_in
    def change_request_type(request_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Укажи тип документа."), 400
        try:
            service.call(
                service.change_type(
                    g.login["user_id"],
                    request_id,
                    data.get("document_type"),
                    data.get("mode"),
                    data.get("text", ""),
                )
            )
        except LookupError:
            abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(request_id=request_id)

    @app.post("/api/requests/<request_id>/ask")
    @signed_in
    def ask_document(request_id):
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify(error="Напиши вопрос."), 400
        try:
            service.call(service.ask_ai(g.login["user_id"], request_id, data.get("question")))
        except LookupError:
            abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(request_id=request_id), 202

    @app.post("/api/requests/<request_id>/confirm")
    @signed_in
    def confirm_request(request_id):
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict) or type(data.get("type_mismatch_confirmed", False)) is not bool:
            return jsonify(error="Укажи подтверждение типа как true или false."), 400
        try:
            service.call(
                service.confirm(g.login["user_id"], request_id, data.get("type_mismatch_confirmed", False))
            )
        except LookupError:
            abort(404)
        except ValueError as error:
            return jsonify(error=str(error)), 409
        return jsonify(request_id=request_id), 202

    @app.get("/api/requests/<request_id>/pages/<int:page>/photo")
    @signed_in
    def request_page(request_id, page):
        item = service.store.get_request(g.login["user_id"], request_id)
        if item is None or page < 1:
            abort(404)
        if page <= len(item["previews"]):
            if item["photo"] is None:
                return jsonify(error=MEDIA_EXPIRED), 410
            images = load_document_pages(item["photo"])
            try:
                image = images[page - 1]
                pages = (item.get("visuals") or {}).get("pages", [])
                rotation = pages[page - 1].get("rotation", 0) if page <= len(pages) else 0
                if rotation:
                    with image.rotate(-rotation, expand=True) as oriented:
                        data = preview_bytes(oriented)
                else:
                    data = preview_bytes(image)
            finally:
                for image in images:
                    image.close()
            return send_file(BytesIO(data), mimetype="image/jpeg")
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
        server = create_server(app)
        loop.call_soon_threadsafe(bot.write_log, f"Сайт запущен: {get_public_base_url()}")
        server.run()
    except (OSError, SystemExit) as error:
        loop.call_soon_threadsafe(bot.log_error, error, "Ошибка запуска сайта")


def create_server(app):
    from waitress import create_server as make_http_server

    host, port = get_web_binding()
    options = {
        "host": host,
        "port": port,
        "threads": 8,
        "max_request_body_size": app.config["MAX_CONTENT_LENGTH"],
    }
    if getenv("TRUST_PROXY", "0") == "1":
        options.update(
            trusted_proxy="127.0.0.1",
            trusted_proxy_count=1,
            trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"},
        )
    return make_http_server(app, **options)
