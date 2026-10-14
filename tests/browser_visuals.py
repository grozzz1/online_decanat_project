"""Optional browser check against a local Flask instance; never contacts Telegram."""
from io import BytesIO
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from test_document_cv import encoded, page
from test_document_integration import WebIntegrationTests
from utils.document_cv import analyze_document_visuals


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
            tab.goto(f"{base}/login?code={code}")
            tab.locator('[data-mode="application"]').click()
            tab.locator("#preview-page").wait_for(state="visible")
            tab.wait_for_function("() => document.getElementById('preview-image').complete && document.getElementById('preview-image').naturalWidth > 0")
            tab.locator("#preview-page").select_option("2")
            tab.wait_for_function("() => document.getElementById('preview-image').getAttribute('src').includes('/pages/2/photo') && document.getElementById('preview-image').complete")
            assert tab.locator("#original-document").get_attribute("href").endswith(f"/{pdf_response.json['request_id']}/photo")
            tab.locator(".message-photo").first.click()
            tab.wait_for_function("() => document.getElementById('visual-results').textContent.includes('Подпись студента: найдены')")
            tab.wait_for_function("() => document.getElementById('preview-image').complete && document.getElementById('preview-image').naturalWidth === 800")
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
                    return {green, blue, painted, width: image.clientWidth, height: image.clientHeight,
                        canvasWidth: canvas.clientWidth, canvasHeight: canvas.clientHeight};
                }""")
            tab.wait_for_function("() => document.getElementById('visual-overlay').width > 0")
            initial = overlay()
            assert initial["green"] > 0 and initial["blue"] > 0, initial
            assert initial["width"] == initial["canvasWidth"] and initial["height"] == initial["canvasHeight"], initial
            tab.locator("#zoom-in").click()
            enlarged = overlay()
            assert enlarged["width"] > initial["width"] and enlarged["green"] > 0 and enlarged["blue"] > 0, enlarged
            tab.locator("#show-visuals").uncheck()
            assert overlay()["painted"] == 0
            tab.locator("#show-visuals").check()
            tab.set_viewport_size({"width": 520, "height": 1000})
            tab.wait_for_timeout(200)
            mobile = overlay()
            assert mobile["green"] > 0 and mobile["width"] == mobile["canvasWidth"], mobile
            tab.set_viewport_size({"width": 1440, "height": 1000})
            tab.locator("#zoom-out").click()
            tab.wait_for_timeout(200)
            Path(".tmp").mkdir(exist_ok=True)
            tab.screenshot(path=".tmp/cv-viewer.png", full_page=True)
            # Changing the selected document must clear all stale boxes immediately.
            tab.locator("#document").set_input_files({"name": "new.png", "mimeType": "image/png", "buffer": encoded(page()[0])})
            assert overlay()["painted"] == 0
            assert not errors, errors
            browser.close()
        print("Browser OK: PDF pages, reopening a document, overlay colours, zoom, mobile resize, toggle and stale-box reset.")
    finally:
        if server:
            server.shutdown()
            server.server_close()
        fixture.tearDown()


if __name__ == "__main__":
    run()
