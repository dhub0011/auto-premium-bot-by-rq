from flask import Flask, render_template, request, jsonify, send_file, Response, stream_with_context
from flask_socketio import SocketIO, emit
from flask_cors import CORS
import json
import os
import csv
import time
import threading
import requests
import random
from datetime import datetime
import queue
import asyncio
from playwright.async_api import async_playwright

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")
CORS(app)

# ======= DATA STORAGE =======
DATA_DIR = "data"
PROXIES_FILE = os.path.join(DATA_DIR, "proxies.json")
SITES_FILE = os.path.join(DATA_DIR, "sites.json")
BINS_FILE = os.path.join(DATA_DIR, "bins.json")
SUCCESS_FILE = os.path.join(DATA_DIR, "success.csv")

os.makedirs(DATA_DIR, exist_ok=True)

# ======= DATA LOADERS =======
def load_proxies():
    if os.path.exists(PROXIES_FILE):
        with open(PROXIES_FILE, 'r') as f:
            return json.load(f)
    return []

def save_proxies(proxies):
    with open(PROXIES_FILE, 'w') as f:
        json.dump(proxies, f, indent=2)

def load_sites():
    if os.path.exists(SITES_FILE):
        with open(SITES_FILE, 'r') as f:
            return json.load(f)
    return []

def save_sites(sites):
    with open(SITES_FILE, 'w') as f:
        json.dump(sites, f, indent=2)

def load_bins():
    if os.path.exists(BINS_FILE):
        with open(BINS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_bins(bins):
    with open(BINS_FILE, 'w') as f:
        json.dump(bins, f, indent=2)

# ======= PROXY CHECKER =======
def check_proxy(proxy):
    """Check if proxy is alive"""
    try:
        test_url = "http://httpbin.org/ip"
        proxies = {"http": proxy, "https": proxy}
        response = requests.get(test_url, proxies=proxies, timeout=5)
        return response.status_code == 200
    except:
        return False

# ======= EMAIL GENERATOR =======
class EmailGenerator:
    def __init__(self):
        self.base_url = "https://api.mail.tm"
        self.session = requests.Session()
        
    def generate_email(self):
        try:
            response = self.session.post(
                f"{self.base_url}/accounts",
                json={
                    "address": f"{self.random_string(8)}@{self.random_domain()}",
                    "password": self.random_password()
                }
            )
            if response.status_code == 201:
                data = response.json()
                return {
                    "email": data["address"],
                    "password": data["password"],
                    "id": data["id"],
                    "token": data["token"]
                }
        except:
            pass
        return None
    
    def random_string(self, length=8):
        chars = "abcdefghijklmnopqrstuvwxyz0123456789"
        return ''.join(random.choices(chars, k=length))
    
    def random_domain(self):
        domains = ["gmail.com", "yahoo.com", "outlook.com"]
        return random.choice(domains)
    
    def random_password(self):
        chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
        return ''.join(random.choices(chars, k=12))

# ======= AUTOMATION ENGINE =======
class AutomationEngine:
    def __init__(self):
        self.email_gen = EmailGenerator()
        self.running = False
        self.current_task = None
        self.log_queue = queue.Queue()
        
    def log(self, message, type="info"):
        self.log_queue.put({"message": message, "type": type, "time": datetime.now().isoformat()})
        socketio.emit('log', {"message": message, "type": type})
        
    def log_success(self, site, email, password, plan, proxy, bin_used):
        self.log(f"✅ SUCCESS: {site} | {email} | {plan} | {proxy}", "success")
        
        # Save to CSV
        with open(SUCCESS_FILE, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                site,
                email,
                password,
                plan,
                proxy,
                bin_used,
                "SUCCESS"
            ])
        
        socketio.emit('success', {
            "site": site,
            "email": email,
            "password": password,
            "plan": plan,
            "proxy": proxy,
            "bin": bin_used
        })
    
    async def run_task(self, site, proxy, bin_data):
        """Run a single automation task"""
        self.log(f"🚀 Starting: {site['name']} with proxy {proxy}")
        
        # Generate email
        email_data = self.email_gen.generate_email()
        if not email_data:
            self.log(f"❌ Failed to generate email", "error")
            return False
        
        self.log(f"📧 Email: {email_data['email']}")
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox'
                ]
            )
            
            context = await browser.new_context(
                proxy={"server": proxy} if proxy else None,
                viewport={"width": 1280, "height": 720},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            try:
                # Navigate to site
                await page.goto(site['url'], timeout=30000)
                await page.wait_for_load_state('networkidle')
                self.log(f"📄 Loaded: {site['url']}")
                
                # Fill signup
                await page.fill('input[type="email"]', email_data['email'])
                await page.fill('input[type="password"]', email_data['password'])
                
                # Handle plan selection if specified
                if site.get('plan'):
                    await page.select_option('select#plan', site['plan'])
                
                await page.click('button[type="submit"]')
                self.log(f"📝 Signup submitted")
                
                # Wait for Stripe iframe
                await page.wait_for_selector('iframe[src*="stripe"]', timeout=15000)
                self.log(f"💳 Stripe detected - applying BIN: {bin_data[:6]}...")
                
                # Enter card details
                await page.fill('input#cardNumber', bin_data)
                await page.fill('input#expiry', '04/36')
                await page.fill('input#cvc', '123')
                await page.fill('input#name', 'DEEP BYPASSER')
                
                await page.click('button[type="submit"]')
                self.log(f"💳 BIN applied")
                
                # Wait for success
                await page.wait_for_selector('text="Thank you" or text="Success" or text="Order confirmed"', timeout=30000)
                
                # Log success
                self.log_success(
                    site['name'],
                    email_data['email'],
                    email_data['password'],
                    site.get('plan', 'Free Trial'),
                    proxy,
                    bin_data[:8] + "..."
                )
                
                await browser.close()
                return True
                
            except Exception as e:
                self.log(f"❌ Error: {str(e)}", "error")
                await browser.close()
                return False
    
    def start_automation(self, site_id, proxy_id, bin_id):
        """Start the automation process"""
        sites = load_sites()
        proxies = load_proxies()
        bins = load_bins()
        
        site = next((s for s in sites if s['id'] == site_id), None)
        proxy = next((p for p in proxies if p['id'] == proxy_id), None)
        bin_data = next((b for b in bins if b['id'] == bin_id), None)
        
        if not site or not proxy or not bin_data:
            self.log("❌ Missing required data", "error")
            return
        
        self.running = True
        self.log(f"▶️ Starting automation for {site['name']}")
        
        # Run async task
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(self.run_task(site, proxy['url'], bin_data['bin']))
        loop.close()
        
        self.running = False
        return result

engine = AutomationEngine()

# ======= ROUTES =======
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/proxies')
def get_proxies():
    return jsonify(load_proxies())

@app.route('/api/proxies', methods=['POST'])
def add_proxy():
    data = request.json
    proxies = load_proxies()
    proxy = {
        'id': len(proxies) + 1,
        'url': data['url'],
        'country': data.get('country', 'Unknown'),
        'status': 'pending'
    }
    proxies.append(proxy)
    save_proxies(proxies)
    return jsonify({'status': 'success', 'proxy': proxy})

@app.route('/api/proxies/check', methods=['POST'])
def check_proxies():
    proxies = load_proxies()
    for proxy in proxies:
        proxy['status'] = 'live' if check_proxy(proxy['url']) else 'dead'
    save_proxies(proxies)
    return jsonify({'status': 'success', 'proxies': proxies})

@app.route('/api/sites')
def get_sites():
    return jsonify(load_sites())

@app.route('/api/sites', methods=['POST'])
def add_site():
    data = request.json
    sites = load_sites()
    site = {
        'id': len(sites) + 1,
        'name': data['name'],
        'url': data['url'],
        'plan': data.get('plan', 'Free Trial'),
        'code': data.get('code', ''),
        'created_at': datetime.now().isoformat()
    }
    sites.append(site)
    save_sites(sites)
    return jsonify({'status': 'success', 'site': site})

@app.route('/api/bins')
def get_bins():
    return jsonify(load_bins())

@app.route('/api/bins', methods=['POST'])
def add_bin():
    data = request.json
    bins = load_bins()
    bin_data = {
        'id': len(bins) + 1,
        'bin': data['bin'],
        'label': data.get('label', ''),
        'active': True
    }
    bins.append(bin_data)
    save_bins(bins)
    return jsonify({'status': 'success', 'bin': bin_data})

@app.route('/api/start', methods=['POST'])
def start_automation():
    data = request.json
    threading.Thread(target=engine.start_automation, args=(data['site_id'], data['proxy_id'], data['bin_id'])).start()
    return jsonify({'status': 'started'})

@app.route('/api/stop', methods=['POST'])
def stop_automation():
    engine.running = False
    return jsonify({'status': 'stopped'})

@app.route('/api/success')
def get_success():
    if os.path.exists(SUCCESS_FILE):
        with open(SUCCESS_FILE, 'r') as f:
            reader = csv.reader(f)
            data = list(reader)
            return jsonify(data)
    return jsonify([])

@app.route('/api/success/export')
def export_success():
    if os.path.exists(SUCCESS_FILE):
        return send_file(SUCCESS_FILE, as_attachment=True)
    return jsonify({'error': 'No data'}), 404

@app.route('/api/lemur', methods=['POST'])
def add_lemur_key():
    data = request.json
    # Store Lemur API key securely
    with open(os.path.join(DATA_DIR, 'lemur_keys.json'), 'a') as f:
        json.dump(data, f)
        f.write('\n')
    return jsonify({'status': 'success'})

@app.route('/logs')
def get_logs():
    logs = []
    while not engine.log_queue.empty():
        logs.append(engine.log_queue.get())
    return jsonify(logs)

# ======= WEBSOCKET EVENTS =======
@socketio.on('connect')
def handle_connect():
    emit('connected', {'status': 'connected'})

@socketio.on('disconnect')
def handle_disconnect():
    pass

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
