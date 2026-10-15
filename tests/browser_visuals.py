"""Optional browser check against a local Flask instance; never contacts Telegram."""

from io import BytesIO
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from tests.fixtures import TEXTS, encoded, page
from tests.test_document_integration import WebIntegrationTests
from utils.document_cv import analyze_document_visuals
from utils.document_fields import DOCUMENT_SCHEMAS


def run():
    fixture = WebIntegrationTests()
    fixture.setUp()
    server = None
    try:
        image, words = page(stamp=True, signature=True)
        response = fixture.upload(encoded(image), "document.png")
        fixture.finish_processing()
        request_id = response.json["request_id"]
        # Include controlled OCR coordinates to test both confirmed overlay colours.
        visuals = analyze_document_visuals(image, words)
        fixture.service.store.update_request(request_id, visuals=visuals)
        first, _ = page(stamp=True)
        second, _ = page(signature=True)
        pdf = BytesIO()
        first.save(pdf, "PDF", save_all=True, append_images=[second], resolution=100)
        pdf_response = fixture.upload(pdf.getvalue(), "document.pdf")
        fixture.finish_processing()
        server = make_server("127.0.0.1", 0, fixture.app, threaded=True)
        Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        code = fixture.service.store.create_login(fixture.service.store.get_user(123))
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            tab = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            tab.on("pageerror", lambda error: errors.append(str(error)))
            tab.goto(f"{base}/login")
            link = tab.locator(".telegram-bot-link")
            assert link.inner_text() == "нашему боту в Telegram"
            assert link.get_attribute("href") == "https://t.me/OnlineDecanatBot"
            assert (
                tab.locator(".login-card > p").inner_text()
                == "Отправь /start нашему боту в Telegram и открой ссылку из его ответа. Она откроет твой личный кабинет."
            )
            tab.goto(f"{base}/login?code={code}")
            tab.locator('[data-mode="application"]').click()
            assert tab.locator("#document-type option").count() == 2
            assert tab.locator("#document-type").input_value() == "academic_leave_application"
            for key in DOCUMENT_SCHEMAS:
                tab.locator('[data-mode="application_question"]').click()
                tab.locator("#document-type").select_option(key)
                assert tab.locator("#document-type option").count() == 3
                tab.locator('[data-mode="application"]').click()
                assert tab.locator("#document-type option").count() == 2
                assert tab.locator("#document-type").input_value() == (
                    "academic_leave_application" if key == "study_certificate" else key
                )
            tab.locator("#preview-page").wait_for(state="visible")
            tab.wait_for_function(
                "() => document.getElementById('preview-image').complete && document.getElementById('preview-image').naturalWidth > 0"
            )
            tab.locator("#preview-page").select_option("2")
            tab.wait_for_function(
                "() => document.getElementById('preview-image').getAttribute('src').includes('/pages/2/photo') && document.getElementById('preview-image').complete"
            )
            assert (
                tab.locator("#original-document")
                .get_attribute("href")
                .endswith(f"/{pdf_response.json['request_id']}/photo")
            )
            tab.locator(".message-photo").first.click()
            tab.wait_for_function(
                "() => document.getElementById('visual-results').textContent.includes('Подпись студента: найдены')"
            )
            tab.wait_for_function(
                "() => document.getElementById('preview-image').complete && document.getElementById('preview-image').naturalWidth === 800"
            )

            def overlay():
                return tab.evaluate("""() => {
                    const image = document.getElementById('preview-image');
                    const canvas = document.getElementById('visual-overlay');
                    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                    let green = 0, blue = 0, painted = 0;
                    for (let i = 0; i < pixels.length; i += 4) {
                        if (!pixels[i+3]) continue;
                        painted++;
                        if (pixels[i+1] > pixels[i] * 1.5 && pixels[i+1] > pixels[i+2]) green++;
                        if (pixels[i+2] > pixels[i] * 1.5 && pixels[i+2] > pixels[i+1]) blue++;
                    }
                    // Verify blue border positions, not just presence, after zoom/resize.
                    const page = currentVisuals?.pages.find(p => p.page === previewPage);
                    const signature = currentVisuals?.detections.find(d => d.type === 'signature' && d.status === 'detected' && d.page === previewPage);
                    let signatureBorder = false;
                    if (signature) {
                        const [x1, y1, x2, y2] = signature.bbox;
                        const sampleBlue = (x, y) => {
                            for (let dy = -3; dy <= 3; dy++) for (let dx = -3; dx <= 3; dx++) {
                                const px = Math.round(x + dx), py = Math.round(y + dy);
                                if (px < 0 || py < 0 || px >= canvas.width || py >= canvas.height) continue;
                                const i = (py * canvas.width + px) * 4;
                                if (pixels[i+3] && pixels[i+2] > pixels[i] * 1.5 && pixels[i+2] > pixels[i+1]) return true;
                            }
                            return false;
                        };
                        signatureBorder = sampleBlue(x1 * canvas.width / page.width, (y1+y2)/2 * canvas.height/page.height)
                            && sampleBlue(x2 * canvas.width/page.width, (y1+y2)/2 * canvas.height/page.height)
                            && sampleBlue((x1+x2)/2 * canvas.width/page.width, y1 * canvas.height/page.height)
                            && sampleBlue((x1+x2)/2 * canvas.width/page.width, y2 * canvas.height/page.height);
                    }
                    return {green, blue, painted, width: image.clientWidth, height: image.clientHeight,
                        canvasWidth: canvas.clientWidth, canvasHeight: canvas.clientHeight, signatureBorder};
                }""")

            tab.wait_for_function("() => document.getElementById('visual-overlay').width > 0")
            initial = overlay()
            assert initial["green"] > 0 and initial["blue"] > 0, initial
            assert initial["signatureBorder"], initial
            assert (
                initial["width"] == initial["canvasWidth"] and initial["height"] == initial["canvasHeight"]
            ), initial
            tab.locator("#zoom-in").click()
            enlarged = overlay()
            assert enlarged["width"] > initial["width"] and enlarged["green"] > 0 and enlarged["blue"] > 0, (
                enlarged
            )
            assert enlarged["signatureBorder"], enlarged
            tab.locator("#show-visuals").uncheck()
            assert overlay()["painted"] == 0
            tab.locator("#show-visuals").check()
            tab.set_viewport_size({"width": 520, "height": 1000})
            tab.wait_for_timeout(200)
            mobile = overlay()
            assert mobile["green"] > 0 and mobile["width"] == mobile["canvasWidth"], mobile
            assert mobile["signatureBorder"], mobile
            tab.set_viewport_size({"width": 1440, "height": 1000})
            tab.locator("#zoom-out").click()
            tab.wait_for_timeout(200)
            Path(".tmp").mkdir(exist_ok=True)
            tab.screenshot(path=".tmp/cv-viewer.png", full_page=True)
            # Changing the selected document must clear all stale boxes immediately.
            tab.locator("#document").set_input_files(
                {"name": "new.png", "mimeType": "image/png", "buffer": encoded(page()[0])}
            )
            assert overlay()["painted"] == 0
            for key, schema in DOCUMENT_SCHEMAS.items():
                target_mode = "application_question" if key == "study_certificate" else "application"
                tab.locator(f'[data-mode="{target_mode}"]').click()
                if key == "study_certificate":
                    tab.locator("#question").fill("Содержит ли документ все обязательные сведения?")
                tab.locator("#document-type").select_option(key)
                tab.locator("#document").set_input_files(
                    {"name": "document.png", "mimeType": "image/png", "buffer": encoded(page()[0])}
                )
                with (
                    patch("utils.ocr.pytesseract.image_to_string", return_value=TEXTS[key]),
                    tab.expect_response(
                        lambda response: (
                            response.url.endswith("/api/requests") and response.request.method == "POST"
                        )
                    ) as submitted,
                ):
                    tab.locator("#send-request").click()
                    response = submitted.value
                    assert response.status == 202
                    new_id = response.json()["request_id"]
                    tab.wait_for_function("() => !document.getElementById('send-request').disabled")
                    items = tab.request.get(f"{base}/api/messages").json()["requests"]
                    item = next(item for item in items if item["id"] == new_id)
                    assert item["status"] == "sent"
                    assert item["document_type"] == key
                    assert item["application_type"] == schema["label"]
                    assert set(item["fields"]) == set(schema["fields"])
                    assert item["fields"]["student_name"] is not None
            assert not errors, errors
            browser.close()
        print(
            "Browser OK: all three document types submitted, login link, PDF pages, overlays, zoom, mobile resize and stale-box reset."
        )
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.tearDown()


if __name__ == "__main__":
    run()
