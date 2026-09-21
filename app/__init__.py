"""
SEA Platform Web Application.

A lightweight Flask shell for the Living System Knowledge Graph.
Uses HTMX for dynamic interactions and D3.js for visualization.
"""

import json
from pathlib import Path
from flask import Flask, render_template, jsonify, request, redirect, url_for, flash

from agents.knowledge import graph_from_extraction
from agents.knowledge_extraction.agent import create_knowledge_extraction_agent
from app.views import project_review_table, project_c4_context, project_gap_report

# In-memory store for the current graph state (MVP)
# In production, this would be a database or persistent file store
current_graph = None
current_initiative_id = "INIT-MVP-001"

def create_app(test_config=None):
    app = Flask(__name__, 
                static_folder='static',
                template_folder='templates')
    app.secret_key = 'sea-platform-mvp-secret'

    @app.route('/')
    def index():
        return render_template('base.html')

    @app.route('/ingest', methods=['GET', 'POST'])
    def ingest():
        if request.method == 'POST':
            if 'document' not in request.files:
                flash('No document part')
                return redirect(request.url)
            
            file = request.files['document']
            doc_type = request.form.get('type', 'requirements') # requirements or architecture
            
            if file.filename == '':
                flash('No selected file')
                return redirect(request.url)
                
            if file:
                document_text = file.read().decode('utf-8')
                
                # Run Extraction Agent
                agent = create_knowledge_extraction_agent()
                try:
                    result = agent.run({
                        'document': document_text,
                        'domain': 'payment_processing', # Hardcoded for MVP
                    })
                    
                    output = result.model_dump()
                    
                    # Ingest into Canonical Model
                    global current_graph
                    current_graph, run = graph_from_extraction(
                        output, 
                        metadata=result.metadata if hasattr(result, 'metadata') else {},
                        document_ref=file.filename,
                        initiative_id=current_initiative_id
                    )
                    
                    flash(f'Successfully ingested {file.filename} as {doc_type}')
                    return redirect(url_for('review'))
                    
                except Exception as e:
                    flash(f'Extraction failed: {str(e)}')
                    return redirect(request.url)

        return render_template('ingest.html')

    @app.route('/review')
    def review():
        """Human Review Gate: Table view of assertions."""
        if not current_graph:
            flash('No graph loaded. Please ingest a document first.')
            return redirect(url_for('ingest'))
            
        rows = project_review_table(current_graph)
        return render_template('review.html', rows=rows, initiative_id=current_initiative_id)

    @app.route('/api/graph/c4')
    def api_c4():
        """API endpoint for C4 Context visualization."""
        if not current_graph:
            return jsonify({"nodes": [], "links": []})
        return jsonify(project_c4_context(current_graph))

    @app.route('/api/gaps')
    def api_gaps():
        """API endpoint for Gap Report."""
        if not current_graph:
            return jsonify({"unresolved_count": 0, "references": []})
        return jsonify(project_gap_report(current_graph))

    @app.route('/api/verify/<assertion_id>', methods=['POST'])
    def api_verify(assertion_id):
        """HTMX endpoint to verify an assertion."""
        if current_graph and assertion_id in current_graph.assertions:
            from agents.knowledge.model import STATUS_VERIFIED, SOURCE_HUMAN_REVIEWER, Provenance
            a = current_graph.assertions[assertion_id]
            a.status = STATUS_VERIFIED
            a.provenance.source_type = SOURCE_HUMAN_REVIEWER
            return f'<span style="padding: 4px 8px; border-radius: 4px; background: #d4edda; color: #155724;">VERIFIED</span>'
        return '', 404

    return app
