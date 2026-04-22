import os
import re
import json
import hashlib
import time
import threading
import shutil
from flask import Flask, render_template, request, jsonify, session, send_file
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeError, PleaseWaitFewMinutes, ReloginAttemptExceeded
import yt_dlp
from pathlib import Path
import requests
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
import zipfile
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-this-in-production-12345'
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max file size

# Create necessary directories
DOWNLOAD_DIR = Path("downloads")
SESSION_DIR = Path("sessions")
Path("downloads").mkdir(exist_ok=True)
Path("sessions").mkdir(exist_ok=True)

# Store user passwords temporarily (in production, use a proper database)
user_credentials = {}

# ==================== Helper Functions ====================

def get_session_path(username: str) -> str:
    """Generate session file path from username"""
    safe_username = hashlib.md5(username.encode()).hexdigest()
    return os.path.join("sessions", f"{safe_username}.json")

def login_to_instagram(username: str, password: str):
    """Login to Instagram and save session"""
    cl = Client()
    session_file = get_session_path(username)
    
    # Try to load existing session
    if os.path.exists(session_file):
        try:
            cl.load_settings(session_file)
            cl.login(username, password)
            cl.get_timeline_feed()
            return cl
        except Exception:
            pass
    
    # Normal login
    try:
        cl.login(username, password)
        cl.dump_settings(session_file)
        return cl
    except Exception as e:
        print(f"Login failed: {e}")
        return None

def get_instagram_client(username: str, password: str = None):
    """Get Instagram client for logged in user"""
    if password:
        return login_to_instagram(username, password)
    elif username in user_credentials:
        return login_to_instagram(username, user_credentials[username])
    return None

def download_instagram_media(url: str, output_dir: str = "downloads") -> dict:
    """Download public Instagram media using yt-dlp"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ydl_opts = {
        'outtmpl': os.path.join(output_dir, f'instagram_{timestamp}_%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            return {
                'success': True,
                'filename': os.path.basename(filename),
                'full_path': filename,
                'title': info.get('title', 'Untitled'),
                'url': url
            }
    except Exception as e:
        return {
            'success': False,
            'error': str(e),
            'url': url
        }

def download_profile_picture(username: str, output_dir: str = "downloads") -> dict:
    """Download Instagram profile picture"""
    cl = Client()
    try:
        user_id = cl.user_id_from_username(username)
        user_info = cl.user_info(user_id)
        profile_pic_url = user_info.profile_pic_url_hd or user_info.profile_pic_url
        
        response = requests.get(profile_pic_url, stream=True)
        if response.status_code == 200:
            filename = os.path.join(output_dir, f"{username}_profile_{int(time.time())}.jpg")
            with open(filename, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            return {
                'success': True,
                'filename': os.path.basename(filename),
                'full_path': filename,
                'username': username
            }
        return {'success': False, 'error': 'Failed to download image'}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def download_user_stories(username: str, cl: Client, output_dir: str = "downloads") -> list:
    """Download user stories"""
    results = []
    try:
        user_id = cl.user_id_from_username(username)
        stories = cl.user_stories(user_id)
        
        for idx, story in enumerate(stories):
            try:
                cl.story_download(story.pk, folder=output_dir)
                results.append({
                    'success': True,
                    'story_id': story.pk,
                    'type': 'video' if story.media_type == 2 else 'photo'
                })
            except Exception as e:
                results.append({
                    'success': False,
                    'story_id': story.pk,
                    'error': str(e)
                })
    except Exception as e:
        results.append({'success': False, 'error': str(e)})
    return results

def download_user_medias(username: str, cl: Client, amount: int = 20, output_dir: str = "downloads") -> list:
    """Download user media posts"""
    results = []
    try:
        user_id = cl.user_id_from_username(username)
        medias = cl.user_medias(user_id, amount)
        
        for media in medias:
            try:
                if media.media_type == 1:  # Photo
                    cl.photo_download(media.pk, folder=output_dir)
                    results.append({
                        'success': True,
                        'media_id': media.pk,
                        'type': 'photo'
                    })
                elif media.media_type == 2:  # Video/Reel
                    cl.video_download(media.pk, folder=output_dir)
                    results.append({
                        'success': True,
                        'media_id': media.pk,
                        'type': 'video'
                    })
            except Exception as e:
                results.append({
                    'success': False,
                    'media_id': media.pk,
                    'error': str(e)
                })
    except Exception as e:
        results.append({'success': False, 'error': str(e)})
    return results

def create_zip_file(files_to_zip: list, zip_name: str = None) -> str:
    """Create a zip file from a list of files"""
    if not zip_name:
        zip_name = f"instagram_download_{int(time.time())}.zip"
    
    zip_path = os.path.join(DOWNLOAD_DIR, zip_name)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in files_to_zip:
            if os.path.exists(file_path):
                zipf.write(file_path, os.path.basename(file_path))
    
    return zip_path

def cleanup_old_files(directory: str = "downloads", age_hours: int = 24):
    """Delete files older than specified hours"""
    try:
        current_time = time.time()
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                file_age = current_time - os.path.getmtime(filepath)
                if file_age > age_hours * 3600:
                    os.remove(filepath)
    except Exception as e:
        print(f"Cleanup error: {e}")

# Run cleanup every 6 hours
def schedule_cleanup():
    cleanup_old_files()
    threading.Timer(21600, schedule_cleanup).start()

# Start cleanup thread
schedule_cleanup()

# ==================== Flask Routes ====================

@app.route('/')
def index():
    """Render main page"""
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    """Handle user login"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'})
    
    cl = login_to_instagram(username, password)
    if cl:
        session['username'] = username
        user_credentials[username] = password
        return jsonify({'success': True, 'username': username})
    else:
        return jsonify({'success': False, 'error': 'Login failed. Please check credentials.'})

@app.route('/api/logout', methods=['POST'])
def api_logout():
    """Handle user logout"""
    username = session.get('username')
    if username and username in user_credentials:
        del user_credentials[username]
    session.pop('username', None)
    return jsonify({'success': True})

@app.route('/api/status', methods=['GET'])
def api_status():
    """Get login status"""
    if 'username' in session:
        return jsonify({'logged_in': True, 'username': session['username']})
    return jsonify({'logged_in': False})

@app.route('/api/download/public', methods=['POST'])
def download_public():
    """Download public Instagram media"""
    data = request.get_json()
    url = data.get('url')
    
    if not url:
        return jsonify({'success': False, 'error': 'URL required'})
    
    result = download_instagram_media(url)
    return jsonify(result)

@app.route('/api/download/profile-pic', methods=['POST'])
def download_profile_pic():
    """Download profile picture"""
    data = request.get_json()
    username = data.get('username')
    
    if not username:
        return jsonify({'success': False, 'error': 'Username required'})
    
    result = download_profile_picture(username)
    return jsonify(result)

@app.route('/api/download/stories', methods=['POST'])
def download_stories():
    """Download user stories"""
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Please login first'})
    
    data = request.get_json()
    target_username = data.get('username')
    
    if not target_username:
        return jsonify({'success': False, 'error': 'Target username required'})
    
    cl = get_instagram_client(session['username'])
    if not cl:
        return jsonify({'success': False, 'error': 'Failed to authenticate'})
    
    results = download_user_stories(target_username, cl)
    return jsonify({'success': True, 'results': results})

@app.route('/api/download/medias', methods=['POST'])
def download_medias():
    """Download user media posts"""
    if 'username' not in session:
        return jsonify({'success': False, 'error': 'Please login first'})
    
    data = request.get_json()
    target_username = data.get('username')
    amount = data.get('amount', 20)
    
    if not target_username:
        return jsonify({'success': False, 'error': 'Target username required'})
    
    cl = get_instagram_client(session['username'])
    if not cl:
        return jsonify({'success': False, 'error': 'Failed to authenticate'})
    
    results = download_user_medias(target_username, cl, amount)
    return jsonify({'success': True, 'results': results})

@app.route('/api/download/zip', methods=['POST'])
def download_zip():
    """Create and download zip file of all downloaded content"""
    data = request.get_json()
    files = data.get('files', [])
    
    if not files:
        return jsonify({'success': False, 'error': 'No files specified'})
    
    # Filter only existing files
    existing_files = [f for f in files if os.path.exists(f)]
    
    if not existing_files:
        return jsonify({'success': False, 'error': 'No valid files found'})
    
    zip_path = create_zip_file(existing_files)
    
    return jsonify({
        'success': True,
        'zip_file': zip_path,
        'download_url': f'/api/download-file/{os.path.basename(zip_path)}'
    })

@app.route('/api/download-file/<filename>')
def download_file(filename):
    """Download a specific file"""
    filepath = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/list-downloads', methods=['GET'])
def list_downloads():
    """List all downloaded files"""
    files = []
    for filename in os.listdir(DOWNLOAD_DIR):
        filepath = os.path.join(DOWNLOAD_DIR, filename)
        if os.path.isfile(filepath):
            files.append({
                'name': filename,
                'size': os.path.getsize(filepath),
                'modified': os.path.getmtime(filepath),
                'path': filepath
            })
    return jsonify({'files': files})

if __name__ == '__main__':
    print("=" * 50)
    print("Instagram Downloader Started!")
    print(f"Open your browser and go to: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)
