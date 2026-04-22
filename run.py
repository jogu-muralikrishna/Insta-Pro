#!/usr/bin/env python3
import os
import sys
import subprocess

def check_dependencies():
    """Check if required packages are installed"""
    try:
        import flask
        import instagrapi
        import yt_dlp
        import requests
        return True
    except ImportError as e:
        print(f"Missing dependency: {e}")
        print("\nInstalling required packages...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
        return True

if __name__ == "__main__":
    print("Starting Instagram Downloader...")
    check_dependencies()
    
    # Run the main app
    from app import app
    app.run(debug=True, host='0.0.0.0', port=5000)
