"""Browser acceptance check for an actual registered native-plugin run.

Invoked by CI after the installed CLI smoke run. Playwright is a CI-only
verification dependency, not a runtime dependency of RPNH.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main() -> None:
    from playwright.sync_api import sync_playwright
    smoke = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    destination = Path(sys.argv[2]); destination.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(
        ["rpnh", "net", "--run", smoke["run_dir"], "--view", "--show-resources", "--no-open"],
        stdout=subprocess.PIPE, stderr=sys.stderr, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        line = server.stdout.readline()
        matched = re.search(r"http://127\.0\.0\.1:\d+/", line)
        if matched is None:
            raise RuntimeError("viewer did not start a loopback HTTP endpoint")
        url = matched.group()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 860})
            errors = []
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto(url)
            page.wait_for_selector('#paper[data-ready="true"]')
            node = page.locator('.node[data-id="plugin.run"]')
            assert "demo/add" in node.text_content()
            assert "已结算 1" in node.text_content() and "未结算 0" in node.text_content()
            assert page.locator('.node[data-id="plugin.capability"]').is_visible()
            node.click()
            details = page.locator("#detail").inner_text()
            assert "operation_result" in details and "firing_ref" in details
            page.screenshot(path=str(destination / "plugin-net.png"), full_page=True)
            page.locator("#resources").click()
            assert not page.locator('.node[data-id="plugin.capability"]').is_visible()
            page.locator("#resources").click()
            assert page.locator('.node[data-id="plugin.capability"]').is_visible()
            response = page.request.post(url + "api/v1/net", data="{}")
            assert response.status == 405 and not errors
            (destination / "browser-check.json").write_text(json.dumps({
                "passed": True, "browser_version": browser.version,
                "selector": smoke["selector"], "nodes": page.locator(".node").count(),
                "resource_toggle": True, "runtime_details": True,
                "post_status": response.status, "page_errors": errors,
            }, indent=2), encoding="utf-8")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill(); server.wait(timeout=5)
        server.stdout.close()


if __name__ == "__main__":
    main()
