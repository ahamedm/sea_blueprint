"""Capture the README screenshots from a running app.

Kept as a script rather than a folder of PNGs someone will have to guess about: a
screenshot that predates the UI it shows is worse than no screenshot, so refreshing
these should be one command. Run the app first, then:

    SEA_DATA_DIR=data/sea_home_x SEA_SCOPE=payments_v3 SEA_PORT=5099 \
        uv run python run.py &
    uv run --with playwright python scripts/capture_screenshots.py \
        --base-url http://127.0.0.1:5099

The browser is the one already in the Playwright cache; `--executable` overrides it.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

#: The four pages the README shows, in the order it shows them, with an optional
#: scroll. FORCE is the map capture on purpose: `payments_v3` is a requirements-only
#: ingest with no containment edges, so tree mode there is a flat shelf of 58 nodes
#: and says "0 hierarchy edge(s)" — honest, and a poor front page. Force is also the
#: default, so the README shows what a reader gets.
#:
#: Review scrolls past the gate summary, because the assertion table below it is the
#: part with provenance, `ref` tags and confidence in it.
PAGES = (
    ("map", "/map", 0),
    ("quality", "/quality", 0),
    ("ontology", "/ontology", 0),
    ("review", "/review", 520),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:5099")
    parser.add_argument("--out", default="docs/images")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--executable", default="")
    parser.add_argument("--settle-ms", type=int, default=1800,
                        help="time for the drawing to settle before the shot")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; run with: uv run --with playwright python ...",
              file=sys.stderr)
        return 2

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    launch = {"args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if args.executable:
        launch["executable_path"] = args.executable

    failures = 0
    with sync_playwright() as play:
        browser = play.chromium.launch(**launch)
        page = browser.new_page(viewport={"width": args.width, "height": args.height})
        for name, path, scroll in PAGES:
            response = page.goto(args.base_url + path, wait_until="networkidle")
            if response is None or response.status >= 400:
                print(f"  {name}: HTTP {response.status if response else 'no response'}",
                      file=sys.stderr)
                failures += 1
                continue
            # The canvases are drawn client-side, so networkidle is not enough.
            page.wait_for_timeout(args.settle_ms)
            if scroll:
                page.mouse.wheel(0, scroll)
                page.wait_for_timeout(400)
            target = out / f"{name}.png"
            page.screenshot(path=str(target))
            print(f"  {name:9s} {path:22s} -> {target} ({target.stat().st_size // 1024} KB)")
        browser.close()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
