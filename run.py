#!/usr/bin/env python3
"""
Entry point for the SEA Platform Web Application.
"""

from app import create_app

def main():
    app = create_app()
    app.run(debug=True, port=5000)

if __name__ == '__main__':
    main()
