"""Chrome checks against real Flask routes; OCR and Telegram transport are mocked."""

from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from telebot.asyncio_helper import ApiTelegramException
from werkzeug.serving import make_server

from tests.fixtures import TEXTS, encoded, page
from tests.test_document_integration import WebIntegrationTests


def run():
    fixture = WebIntegrationTests()
    fixture.setUp()
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        code = fixture.service.store.create_login(fixture.service.store.get_user(123))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            tab = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            tab.on("pageerror", lambda error: errors.append(str(error)))
            tab.goto(f"{base}/login?code={code}")
            tab.locator('[data-mode="application"]').click()
            image = encoded(page(signature=True)[0])

            def wait_status(request_id, status):
                tab.wait_for_function(
                    "([id, status]) => requestsById.get(id)?.status === status && !busy",
                    arg=[request_id, status],
                )

            def upload(ocr_text, status="needs_review"):
                tab.locator("#document").set_input_files(
                    {"name": "document.png", "mimeType": "image/png", "buffer": image}
                )
                with (
                    patch("utils.ocr.pytesseract.image_to_string", return_value=ocr_text),
                    tab.expect_response(
                        lambda r: r.url.endswith("/api/requests") and r.request.method == "POST"
                    ) as submitted,
                ):
                    tab.locator("#send-request").click()
                    request_id = submitted.value.json()["request_id"]
                    wait_status(request_id, status)
                return request_id

            # Detection must not silently change the student's selection.
            request_id = upload("СПРАВКА ОБ ОБУЧЕНИИ\n" + TEXTS["study_certificate"])
            assert tab.locator("#document-type option").count() == 3
            assert tab.locator("#confirm-document").is_disabled()
            assert tab.locator("#send-request").is_disabled()
            assert tab.locator("#apply-document-type").count() == 0
            tab.locator('[data-mode="question"]').click()
            assert tab.locator("#send-request").is_enabled()
            tab.locator('[data-mode="application"]').click()
            tab.locator("#change-document-type").click()
            assert tab.locator("#type-notice").is_hidden()
            assert tab.locator("#document-type").input_value() == "academic_leave_application"
            assert tab.locator("#document-type option").count() == 3
            tab.keyboard.press("Escape")
            assert tab.request.get(f"{base}/api/requests/{request_id}/photo").body() == image
            tab.locator("#confirm-document").click()
            tab.locator("#type-notice").wait_for(state="visible")
            fixture.bot.send_photo.assert_not_awaited()
            tab.set_viewport_size({"width": 520, "height": 1000})
            tab.screenshot(path=".tmp/type-review-mobile.png", full_page=True)
            with patch("utils.ocr.pytesseract.image_to_string", side_effect=AssertionError("OCR repeated")):
                tab.locator("#override-document-type").click()
                wait_status(request_id, "sent")
            fixture.bot.send_photo.assert_awaited_once()
            item = fixture.service.store.get_request(123, request_id)
            assert item["type_mismatch_confirmed"]
            assert item["selected_document_type"] == "academic_leave_application"
            assert item["detected_document_type"] == "study_certificate"
            tab.set_viewport_size({"width": 1440, "height": 1000})

            # Rechecking selection uses cached OCR, including another wrong choice.
            request_id = upload("СПРАВКА О ПРАКТИКЕ\n" + TEXTS["internship_certificate"])
            with patch("utils.ocr.pytesseract.image_to_string", side_effect=AssertionError("OCR repeated")):
                tab.locator("#change-document-type").click()
                tab.keyboard.press("Escape")
                with tab.expect_response(lambda r: r.url.endswith("/type")):
                    tab.locator("#document-type").select_option("internship_certificate")
                tab.locator("#type-notice").wait_for(state="hidden")
                tab.wait_for_function("() => !busy")
                assert tab.locator("#confirm-document").is_enabled()
                with tab.expect_response(lambda r: r.url.endswith("/type")):
                    tab.locator("#document-type").select_option("academic_leave_application")
                tab.locator("#type-notice").wait_for(state="visible")
                tab.wait_for_function("() => !busy")
                assert tab.locator("#confirm-document").is_disabled()
                with tab.expect_response(lambda r: r.url.endswith("/type")):
                    tab.locator("#document-type").select_option("internship_certificate")
                tab.wait_for_function("() => !busy && !document.getElementById('confirm-document').disabled")
                tab.locator("#confirm-document").click()
                wait_status(request_id, "sent")
            assert fixture.bot.send_photo.await_count == 2

            upload("Не удалось разобрать содержимое", "sent")
            assert "neutral" in tab.locator("#type-notice").get_attribute("class")
            assert tab.locator("#override-document-type").is_hidden()
            assert fixture.bot.send_photo.await_count == 3
            tab.locator("#document-type").select_option("academic_leave_application")
            upload("ЗАЯВЛЕНИЕ НА АКАДЕМИЧЕСКИЙ ОТПУСК\n" + TEXTS["academic_leave_application"], "sent")
            assert tab.locator("#type-notice").is_hidden()
            assert fixture.bot.send_photo.await_count == 4

            request_id = upload("СПРАВКА О ПРАКТИКЕ\n" + TEXTS["internship_certificate"])
            fixture.bot.send_photo.side_effect = ApiTelegramException(
                "sendPhoto", None, {"error_code": 429, "description": "retry later"}
            )
            tab.locator("#override-document-type").click()
            wait_status(request_id, "failed")
            assert tab.locator("#form-error").is_visible()
            assert tab.locator("#confirm-document").is_enabled()
            fixture.bot.send_photo.side_effect = None
            tab.locator("#confirm-document").click()
            wait_status(request_id, "sent")
            assert fixture.bot.send_photo.await_count == 6
            tab.locator('[data-mode="application_question"]').click()
            assert tab.locator("#question-field").is_visible()
            assert tab.locator("#ask-ai").count() == 0
            assert tab.locator(".chat-card").is_visible()
            tab.locator("#question").fill("Кто принимает окончательное решение?")
            tab.locator("#document-type").select_option("academic_leave_application")
            upload(TEXTS["academic_leave_application"], "checked")
            assert tab.locator("#field-card").is_visible()
            assert tab.locator("#validation-summary").inner_text()
            assert fixture.bot.send_photo.await_count == 6
            tab.locator("#ai-answer").filter(has_text="сотрудник деканата").wait_for()
            tab.locator("#question").fill("Какой курс указан?")
            with tab.expect_response(lambda r: r.url.endswith("/ask")) as asked:
                tab.locator("#send-request").click()
            assert asked.value.status == 202
            tab.wait_for_function(
                "() => !busy && document.getElementById('ai-answer').textContent.includes('field:course')"
            )
            assert not errors, errors
            browser.close()
        print(
            "Browser OK: override, retained selection/file, correct/wrong changes, unknown, matching, rejection/retry."
        )
    finally:
        server.shutdown()
        server.server_close()
        fixture.tearDown()


if __name__ == "__main__":
    run()
