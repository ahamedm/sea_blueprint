#!/usr/bin/env python3
"""
Entry point for the SEA Platform Web Application.

Host/port/debug are read from the environment so the same entry point works for
a local session and for a container, without editing the file:

    SEA_HOST=0.0.0.0 SEA_PORT=8080 uv run sea-app
"""

import os

from app import create_app


def main():
    app = create_app()
    app.run(
        host=os.environ.get("SEA_HOST", "127.0.0.1"),
        port=int(os.environ.get("SEA_PORT", "5000")),
        debug=os.environ.get("SEA_DEBUG", "1") == "1",
    )


if __name__ == '__main__':
    main()
