"""End-to-end browser smoke test for the DVP Meeting Prep web app.

Not part of the automated pytest suite: it drives a real browser against a
running server, writes to the real local SQLite database configured in .env,
and hits the real Gemini Enterprise API (via Google Application Default
Credentials), so it costs money/quota and mutates data (uploads sample
files). Run it manually after standing up the server:

    python scripts/run_server.py
    python -m pip install -e ".[dev]"
    python -m playwright install chromium
    python tests/e2e_smoke_test.py

Requires an advisor named "Avery Benton" to already exist in Salesforce/Tableau
data (present after `python scripts/ingest_all.py`, or after uploading the
sample files once through the /upload page).
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE_URL = "http://127.0.0.1:8000"
REPO_ROOT = Path(__file__).resolve().parents[1]
TABLEAU_CSV = REPO_ROOT / "RelatedMaterials" / "Sample" / "Tableau - DummyData.csv"
SCORECARD_XLSX = REPO_ROOT / "RelatedMaterials" / "Sample" / "Consultant Scorecard 2026-03 - DummyData.xlsx"


def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dvp_e2e_") as tmp:
        scratch = Path(tmp)

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            console_errors: list[str] = []
            page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: console_errors.append(str(exc)))

            step("Load home page")
            page.goto(BASE_URL + "/", wait_until="networkidle")
            expect(page).to_have_title("DVP Meeting Prep")

            step("Type into advisor search box and check dropdown")
            search_input = page.locator("#advisor-input")
            search_input.click()
            search_input.type("av", delay=50)
            page.wait_for_selector("#advisor-list.open", timeout=5000)
            options = page.locator("#advisor-list li")
            texts = [options.nth(i).inner_text() for i in range(options.count())]
            print("dropdown options:", texts)
            assert any("benton" in t.lower() for t in texts), f"expected Avery Benton in matches, got {texts}"

            step("Select Avery Benton from dropdown")
            page.locator("#advisor-list li", has_text="Avery Benton").click()
            expect(page.locator("#selected-advisor")).to_contain_text("Avery Benton")
            expect(page.locator("#generate-btn")).to_be_enabled()

            step("Click generate + wait for docx download")
            with page.expect_download(timeout=60000) as download_info:
                page.locator("#generate-btn").click()
            download = download_info.value
            download_path = scratch / download.suggested_filename
            download.save_as(str(download_path))
            print("downloaded file:", download_path, "size:", download_path.stat().st_size)
            assert download_path.suffix == ".docx"
            assert download_path.stat().st_size > 1000

            step("Verify docx opens and has expected content")
            from docx import Document

            doc = Document(str(download_path))
            assert len(doc.paragraphs) > 5, "expected a substantial generated document"

            step("Navigate to /upload page")
            page.goto(BASE_URL + "/upload", wait_until="networkidle")
            expect(page).to_have_title("Upload Data - DVP Meeting Prep")

            step("Upload tableau CSV")
            tableau_section = page.locator('[data-uploader][data-source-type="tableau"]')
            tableau_section.locator('input[type="file"]').set_input_files(str(TABLEAU_CSV))
            expect(tableau_section.locator("[data-upload-btn]")).to_be_enabled()
            t0 = time.monotonic()
            with page.expect_response(lambda r: "/api/uploads/tableau" in r.url, timeout=90000):
                tableau_section.locator("[data-upload-btn]").click()
            print(f"tableau upload responded in {time.monotonic() - t0:.1f}s")
            expect(tableau_section.locator("[data-status]")).to_have_class("status-banner visible success", timeout=15000)
            print("tableau upload status:\n", tableau_section.locator("[data-status]").inner_text())

            step("Upload consultant scorecard xlsx")
            scorecard_section = page.locator('[data-uploader][data-source-type="consultant_scorecard"]')
            scorecard_section.locator('input[type="file"]').set_input_files(str(SCORECARD_XLSX))
            expect(scorecard_section.locator("[data-upload-btn]")).to_be_enabled()
            t0 = time.monotonic()
            with page.expect_response(lambda r: "/api/uploads/consultant-scorecard" in r.url, timeout=90000):
                scorecard_section.locator("[data-upload-btn]").click()
            print(f"scorecard upload responded in {time.monotonic() - t0:.1f}s")
            expect(scorecard_section.locator("[data-status]")).to_have_class("status-banner visible success", timeout=15000)
            print("scorecard upload status:\n", scorecard_section.locator("[data-status]").inner_text())

            step("Unknown-advisor search shows empty state, button stays disabled")
            page.goto(BASE_URL + "/", wait_until="networkidle")
            search_input2 = page.locator("#advisor-input")
            search_input2.click()
            search_input2.type("zzzznotreal", delay=30)
            page.wait_for_selector("#advisor-list.open", timeout=5000)
            empty_text = page.locator("#advisor-list li.empty-state").inner_text()
            assert "no matching" in empty_text.lower()
            expect(page.locator("#generate-btn")).to_be_disabled()

            step("Check for JS console errors across the whole run")
            assert not console_errors, f"Unexpected console errors: {console_errors}"

            browser.close()
            print("\nALL E2E CHECKS PASSED")


if __name__ == "__main__":
    main()
