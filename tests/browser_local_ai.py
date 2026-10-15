"""Browser smoke test: user-visible provenance, grounded answer and retained document."""

from threading import Thread

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from tests.fixtures import encoded, page
from tests.test_document_integration import WebIntegrationTests
from webapp.app import WebRequestHandler


def run():
    fixture = WebIntegrationTests()
    fixture.setUp()
    server = make_server("127.0.0.1", 0, fixture.app, threaded=True, request_handler=WebRequestHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        code = fixture.service.store.create_login(fixture.service.store.get_user(123))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            tab = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors = []
            tab.on("pageerror", lambda error: errors.append(str(error)))
            tab.goto(f"{base}/login?code={code}")
            tab.locator('[data-mode="application"]').click()
            data = encoded(page(signature=True)[0])
            tab.locator("#document").set_input_files(
                {"name": "document.png", "mimeType": "image/png", "buffer": data}
            )
            with tab.expect_response(
                lambda r: r.url.endswith("/api/requests") and r.request.method == "POST"
            ) as response:
                tab.locator("#send-request").click()
            request_id = response.value.json()["request_id"]
            tab.locator("#ai-panel").wait_for(state="visible")
            tab.locator("#field-card > summary").click()
            assert "из текста" in tab.locator("#field-results").inner_text()
            tab.locator("#ai-question").fill("Какой курс указан в документе?")
            with tab.expect_response(lambda r: r.url.endswith("/ask")) as response:
                tab.locator("#ask-ai").click()
            assert response.value.status == 202
            tab.wait_for_function(
                "() => document.getElementById('ai-answer').textContent.includes('field:course')"
            )
            assert "2" in tab.locator("#ai-answer").inner_text()
            assert (
                fixture.service.store.get_request(123, request_id)["ai_answer"]["diagnostics"]["llm_called"]
                is False
            )
            assert tab.request.get(f"{base}/api/requests/{request_id}/photo").body() == data
            tab.set_viewport_size({"width": 520, "height": 1000})
            tab.screenshot(path=".tmp/local-ai-mobile.png", full_page=True)
            tab.locator("#ai-question").fill("Какова стоимость обучения?")
            tab.locator("#ask-ai").click()
            tab.wait_for_function(
                "() => document.getElementById('ai-answer').textContent.includes('нет информации')"
            )
            assert not errors, errors
            browser.close()
        print(
            "Browser AI OK: provenance, Python answer, citations, refusal, retained file and mobile layout."
        )
    finally:
        server.shutdown()
        fixture.tearDown()


if __name__ == "__main__":
    run()
