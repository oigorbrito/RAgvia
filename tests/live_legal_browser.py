"""Optional real HTTP/browser leg of synthetic acceptance; no API response replay."""
from __future__ import annotations

import asyncio
import socket

import uvicorn


async def verify_live_browser(app, *, code: str, token: str) -> None:
    from playwright.async_api import async_playwright

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(15):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("local API failed to start")
                await asyncio.sleep(0.05)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(permissions=["clipboard-read", "clipboard-write"])
                page = await context.new_page()
                responses = []
                page.on("response", lambda response: responses.append(response) if "/processes/" in response.url else None)
                await page.goto(f"http://127.0.0.1:{port}", wait_until="networkidle")
                if await page.locator("#auth-panel").is_hidden():
                    await page.locator("#access-trigger").click()
                await page.locator("#bearer-token").fill(token)
                await page.locator("#process-code").fill(code)
                await page.locator("#search-submit").click()
                await page.locator("#result").wait_for(state="visible")
                assert code in await page.locator("#summary-body").inner_text()
                assert any(response.status == 200 for response in responses)
                await page.locator("#copy-summary").click()
                await page.wait_for_function("document.querySelector('#copy-summary').textContent.includes('copiado')")
                text = await page.evaluate("navigator.clipboard.readText()")
                assert code in text and "10/04/2026" in text
                assert "<ProcessHeader" not in text and '"schema_version"' not in text
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)
        sock.close()
