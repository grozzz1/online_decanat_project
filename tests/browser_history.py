import json
import time
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from tests.fixtures import encoded, page
from tests.test_document_integration import WebIntegrationTests
from tests.test_telegram_auth import signed_data
from webapp.app import WebRequestHandler


def run():
    fixture = WebIntegrationTests()
    fixture.setUp()
    storage = fixture.service.store.storage
    storage.media_limit = 2
    data = encoded(page()[0])
    tag = storage.save_media(123, data, "image/png")
    fixture.service.store.add_message(
        123, "student", "Сообщение и фото из Telegram", media_tag=tag, source="telegram"
    )
    fixture.service.store.add_message(123, "bot", "Выбери пункт меню", source="telegram")
    fixture.service.store.add_message(123, "student", "/start", source="telegram")
    fixture.service.store.add_message(123, "ai", "Ответ AI сохранён", source="telegram")
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True, request_handler=WebRequestHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        code = fixture.service.store.create_login(fixture.service.store.get_user(123))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            tab = browser.new_page()
            errors = []
            tab.on("pageerror", lambda error: errors.append(str(error)))
            tab.goto(f"{base}/login?code={code}")
            expect(tab.locator("#chat-messages")).to_contain_text("Сообщение и фото из Telegram")
            expect(tab.locator("#chat-messages")).to_contain_text("Ответ AI сохранён")
            expect(tab.locator("#chat-messages")).not_to_contain_text("Выбери пункт меню")
            expect(tab.locator("#chat-messages")).not_to_contain_text("/start")
            tab.locator(".message-photo").click()
            expect(tab.locator("#photo-dialog")).to_be_visible()
            tab.wait_for_function("() => document.getElementById('dialog-photo').naturalWidth === 800")
            tab.locator("#close-photo").click()
            storage.save_media(456, data, "image/png")
            storage.save_media(456, data, "image/png")
            tab.locator(".message-photo").click()
            expect(tab.locator(".expired-photo")).to_contain_text("1000")
            tab.reload()
            expect(tab.locator("#chat-messages")).to_contain_text("Сообщение и фото из Telegram")
            tab.locator(".message-photo").click()
            expect(tab.locator(".expired-photo")).to_contain_text("срок хранения")
            key = fixture.service.store.types.add("Заявление на перевод", ["application_question"], 42)
            tab.locator('[data-mode="application_question"]').click()
            expect(tab.locator(f'#document-type option[value="{key}"]')).to_have_count(1)
            tab.locator('[data-mode="application"]').click()
            expect(tab.locator(f'#document-type option[value="{key}"]')).to_have_count(0)
            fixture.service.store.types.delete(key)
            tab.locator('[data-mode="application_question"]').click()
            expect(tab.locator(f'#document-type option[value="{key}"]')).to_have_count(0)
            auth = {"user_id": 123}
            tab.route(
                "https://telegram.org/js/telegram-web-app.js",
                lambda route: route.fulfill(
                    content_type="application/javascript",
                    body="window.Telegram={WebApp:{initData:"
                    + json.dumps(signed_data(user_id=auth["user_id"], auth_date=int(time.time())))
                    + ",ready(){},expand(){}}};",
                ),
            )
            with patch("webapp.app.get_token", return_value="123:test"):
                tab.goto(f"{base}/miniapp")
                expect(tab).to_have_url(base + "/")
                expect(tab.locator("#chat-messages")).to_contain_text("Сообщение и фото из Telegram")
                fixture.service.store.add_message(456, "student", "История второго аккаунта")
                auth["user_id"] = 456
                tab.goto(f"{base}/miniapp")
                expect(tab).to_have_url(base + "/")
                expect(tab.locator("#chat-messages")).to_contain_text("История второго аккаунта")
                expect(tab.locator("#chat-messages")).not_to_contain_text("Сообщение и фото из Telegram")
            assert not errors, errors
            browser.close()
        print(
            "Browser history OK: Telegram messages, retention, types and automatic Mini App login with account change."
        )
    finally:
        server.shutdown()
        server.server_close()
        fixture.tearDown()


if __name__ == "__main__":
    run()
