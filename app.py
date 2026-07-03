from flask import Flask, request, jsonify, send_file, Response, stream_with_context, render_template
from flask_cors import CORS
import urllib.request
from bs4 import BeautifulSoup
import json
import pandas as pd
import io
import re
import uuid
import threading
import time
import os

app = Flask(__name__)
CORS(app)

# In-memory job store
jobs = {}

def normalize_phone(raw):
    digits = re.sub(r'\D', '', str(raw))
    return digits[-10:] if len(digits) >= 10 else digits

def scrape_in_background(job_id, base_url):
    job = jobs[job_id]
    if '?' in base_url:
        base_url = base_url.split('?')[0]

    all_agents = []
    page = 1

    while True:
        url = f"{base_url}?page={page}"
        job['current_url'] = url
        job['current_page'] = page

        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'
            }
        )
        try:
            html = urllib.request.urlopen(req, timeout=20).read()
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                break
            elif e.code == 403:
                job['status'] = 'error'
                job['error'] = 'Access forbidden (403). The site may be blocking requests.'
                return
            else:
                break
        except Exception as e:
            job['status'] = 'error'
            job['error'] = str(e)
            return

        soup = BeautifulSoup(html, 'html.parser')
        scripts = soup.find_all('script', type='application/ld+json')

        found_agent = False
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    items = data
                elif isinstance(data, dict):
                    if '@graph' in data:
                        items = data['@graph']
                    elif data.get('@type') == 'ItemList' and 'itemListElement' in data:
                        items = data['itemListElement']
                    else:
                        items = [data]
                else:
                    continue

                for item in items:
                    if item.get('@type') == 'RealEstateAgent':
                        found_agent = True
                        name = item.get('name', '')
                        email = item.get('email', '')
                        raw_phone = item.get('telephone', '')
                        phone = normalize_phone(raw_phone)
                        company = 'Compass'

                        parent = item.get('parentOrganization')
                        if parent and isinstance(parent, dict):
                            company = parent.get('name', 'Compass')

                        city = ''
                        state = ''
                        address = item.get('address')
                        if address and isinstance(address, dict):
                            city = address.get('addressLocality', '')
                            state = address.get('addressRegion', '')

                        city_state = f"{city}, {state}".strip(', ')

                        all_agents.append({
                            'Name': name,
                            'Email Address': email,
                            'Phone Number': phone,
                            'Company Name': company,
                            'City': city,
                            'State': state,
                            'City/State': city_state
                        })
            except (json.JSONDecodeError, AttributeError):
                continue

        job['agents_found'] = len(all_agents)

        if not found_agent:
            break

        page += 1

    if not all_agents:
        job['status'] = 'error'
        job['error'] = 'No agents found at that URL. Please check the link and try again.'
        return

    df = pd.DataFrame(all_agents)
    df.drop_duplicates(subset=['Name', 'Email Address'], inplace=True)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Agents')

    output.seek(0)
    job['excel_data'] = output.read()
    job['total_agents'] = len(df)
    job['total_pages'] = page - 1
    job['status'] = 'done'


# ---- Frontend Route ----
@app.route('/')
def index():
    return render_template('index.html')


# ---- API Routes ----
@app.route('/api/start', methods=['POST'])
def start_scrape():
    data = request.json
    url = data.get('url', '').strip()

    if not url or 'compass.com' not in url:
        return jsonify({'error': 'Please provide a valid Compass URL.'}), 400

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        'status': 'running',
        'current_page': 0,
        'agents_found': 0,
        'excel_data': None,
        'total_agents': 0,
        'total_pages': 0,
        'error': None,
        'current_url': url
    }

    thread = threading.Thread(target=scrape_in_background, args=(job_id, url), daemon=True)
    thread.start()

    return jsonify({'job_id': job_id})


@app.route('/api/progress/<job_id>')
def progress_stream(job_id):
    def event_stream():
        while True:
            if job_id not in jobs:
                yield f"data: {json.dumps({'status': 'error', 'error': 'Job not found.'})}\n\n"
                break

            job = jobs[job_id]
            payload = {
                'status': job['status'],
                'current_page': job['current_page'],
                'agents_found': job['agents_found'],
                'error': job.get('error')
            }

            if job['status'] == 'done':
                payload['total_agents'] = job['total_agents']
                payload['total_pages'] = job['total_pages']

            yield f"data: {json.dumps(payload)}\n\n"

            if job['status'] in ('done', 'error'):
                break

            time.sleep(0.8)

    return Response(
        stream_with_context(event_stream()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/api/download/<job_id>')
def download_file(job_id):
    if job_id not in jobs or jobs[job_id].get('excel_data') is None:
        return jsonify({'error': 'File not ready or job not found.'}), 404

    return send_file(
        io.BytesIO(jobs[job_id]['excel_data']),
        as_attachment=True,
        download_name='compass_agents.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, threaded=True)
