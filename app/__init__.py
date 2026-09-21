"""
SEA Platform Web Application.

A lightweight Flask shell for the Living System Knowledge Graph.
Uses HTMX for dynamic interactions and D3.js for visualization.
"""

import json
from pathlib import Path
from flask import Flask, render_template, jsonify, request

from agents.knowledge import graph_from_extraction
from app.views import project_review_table, project_c4_context, project_gap_report


def create_app(test_config=None):
    app = Flask(__name__, 
                static_folder='static',
                template_folder='templates')

    # In a real scenario, this would load from a persistent store
    # For MVP, we'll use a simple in-memory cache or file-based loading
    current_graph = None

    @app.route('/')
    def index():
        return render_template('base.html')

    @app.route('/review')
    def review():
        """Human Review Gate: Table view of assertions."""
        # TODO: Load the actual graph from persistence
        # For now, we'll use a placeholder
        return render_template('review.html', rows=[])

    @app.route('/api/graph/c4')
    def api_c4():
        """API endpoint for C4 Context visualization."""
        # TODO: Load graph and project
        return jsonify({"nodes": [], "links": []})

    @app.route('/api/gaps')
    def api_gaps():
        """API endpoint for Gap Report."""
        # TODO: Load graph and project
        return jsonify({"unresolved_count": 0, "references": []})

    return app
