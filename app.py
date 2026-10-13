import os
import io
import tempfile
import webbrowser
import threading
from datetime import timedelta
from flask import Flask, request, jsonify, send_file, render_template, session, redirect, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from parser import parse_docx
from exporter import create_excel_manifest
from notion_service import fetch_manifest_cases, update_case_status_in_notion
from auth import login_required, verify_credentials, is_rate_limited

load_dotenv()

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

# App security & session configuration
app.secret_key = os.getenv('SECRET_KEY', 'default-dev-secret-key-manifest-2026')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=int(os.getenv('SESSION_TTL_HOURS', 12)))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if os.getenv('SESSION_COOKIE_SECURE', 'false').lower() in ('true', '1', 'yes'):
    app.config['SESSION_COOKIE_SECURE'] = True

# Trust Traefik / reverse proxy headers in Dokploy (Hostinger VPS)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# Set max upload size to 16MB
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

@app.route('/health')
def health():
    """Health check endpoint for container monitoring and Dokploy VPS."""
    return jsonify({'status': 'healthy', 'service': 'blueprint-manifest'}), 200

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('authenticated'):
        return redirect(url_for('index'))
        
    error = None
    next_url = request.args.get('next') or request.form.get('next') or url_for('index')
    
    if request.method == 'POST':
        ip = request.remote_addr or '127.0.0.1'
        if is_rate_limited(ip, 'login', limit=5, window_seconds=60):
            return render_template(
                'login.html',
                error="Too many login attempts. Please wait 1 minute before trying again.",
                next_url=next_url
            ), 429
            
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        
        if verify_credentials(username, password):
            session.permanent = True
            session['authenticated'] = True
            session['user'] = username
            return redirect(next_url)
        else:
            error = "Invalid username or password."
            
    return render_template('login.html', error=error, next_url=next_url)

@app.route('/logout', methods=['GET', 'POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', user=session.get('user', 'Admin'))

@app.route('/api/parse', methods=['POST'])
@login_required
def api_parse():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
        
    uploaded_file = request.files['file']
    if uploaded_file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if not uploaded_file.filename.lower().endswith('.docx'):
        return jsonify({'error': 'File must be in DOCX format'}), 400
        
    try:
        file_stream = io.BytesIO(uploaded_file.read())
        result = parse_docx(file_stream)
        cases = result.get('cases', [])
        docx_rows = result.get('row_count', 0)
        return jsonify({'cases': cases, 'docx_rows': docx_rows, 'parsed_rows': len(cases)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to parse DOCX: {str(e)}'}), 500

@app.route('/api/notion/status', methods=['GET'])
@login_required
def api_notion_status():
    has_key = bool(os.getenv('NOTION_API_KEY') or os.getenv('NOTION_TOKEN'))
    has_db = bool(os.getenv('NOTION_DATABASE_ID') or os.getenv('NOTION_PARTICIPANT_DB_ID') or os.getenv('NOTION_CASES_DB_ID'))
    return jsonify({
        'configured': has_key and has_db,
        'has_api_key': has_key,
        'has_database_id': has_db
    })

@app.route('/api/notion/fetch', methods=['GET', 'POST'])
@login_required
def api_notion_fetch():
    req_data = request.get_json(silent=True) or {}
    lab_filter = req_data.get('lab') or request.args.get('lab')
    filter_mode = req_data.get('filter_mode') or request.args.get('filter_mode') or 'manifest_batch'
    force_refresh = req_data.get('force_refresh', False) or request.args.get('force', '').lower() == 'true'
    
    ip = request.remote_addr or '127.0.0.1'
    if is_rate_limited(ip, 'notion_fetch', limit=20, window_seconds=60):
        return jsonify({'error': 'Request limit exceeded. Please wait a moment.'}), 429

    try:
        result = fetch_manifest_cases(
            filter_mode=filter_mode,
            lab_filter=lab_filter,
            force_refresh=force_refresh
        )
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to fetch from Notion: {str(e)}'}), 500

@app.route('/api/export', methods=['POST'])
@login_required
def api_export():
    data = request.get_json()
    if not data or 'cases' not in data:
        return jsonify({'error': 'Missing cases data'}), 400
        
    cases = data['cases']
    try:
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            tmp_path = tmp.name
            
        try:
            create_excel_manifest(cases, tmp_path)
            with open(tmp_path, 'rb') as f:
                excel_bytes = f.read()
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
        return send_file(
            io.BytesIO(excel_bytes),
            as_attachment=True,
            download_name="finalised_manifest.xlsx",
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to generate Excel: {str(e)}'}), 500

@app.route('/api/notion/update-status', methods=['POST'])
@login_required
def api_notion_update_status():
    data = request.get_json(silent=True) or {}
    case_page_ids = list(data.get('case_page_ids', []))
    cases = data.get('cases', [])
    target_status = data.get('target_status', 'Dispatched to Laboratory')
    
    # If cases array is provided, extract case_page_ids from cases
    if not case_page_ids and cases:
        for c in cases:
            for cid in c.get('case_page_ids', []):
                if cid and cid not in case_page_ids:
                    case_page_ids.append(cid)

    if not case_page_ids:
        return jsonify({
            'success': True,
            'message': 'No Notion case IDs provided to update.',
            'total': 0,
            'updated': 0,
            'failed': 0,
            'failed_cases': []
        }), 200

    ip = request.remote_addr or '127.0.0.1'
    if is_rate_limited(ip, 'notion_update_status', limit=30, window_seconds=60):
        return jsonify({'error': 'Update rate limit exceeded. Please wait a moment.'}), 429

    try:
        result = update_case_status_in_notion(
            case_page_ids=case_page_ids,
            target_status=target_status
        )
        return jsonify({
            'success': result['failed'] == 0,
            **result
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Failed to update Notion statuses: {str(e)}'}), 500

def start_server():
    port = int(os.getenv('PORT', 5000))
    app.run(host="127.0.0.1", port=port, debug=True)

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    start_server()
