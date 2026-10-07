#!/usr/bin/env python3
"""
Hyundai SmartReserve — One-Click Demo Launcher
Starts the FastAPI backend and the frontend static server simultaneously.

Usage:
    python run_demo.py

Then open:
    http://localhost:3000        → Demo hub
    http://localhost:3000/car    → In-cabin dashboard (open in browser tab 1)
    http://localhost:3000/kiosk  → Kiosk simulator (open in browser tab 2)
    http://localhost:8000/docs   → API documentation
"""
import os
import sys
import subprocess
import threading
import time
import signal

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(BASE_DIR, "smartreserve_backend")
FRONTEND_DIR = os.path.join(BASE_DIR, "smartreserve_frontend")


BANNER = """
╔══════════════════════════════════════════════════════════════════╗
║           HYUNDAI SMARTRESERVE — DEMO LAUNCHER v1.0            ║
║        AI-Powered In-Cabin EV Charging Co-Pilot System          ║
╠══════════════════════════════════════════════════════════════════╣
║  Track B · Technology & Prototype · Ideathon 2026               ║
╚══════════════════════════════════════════════════════════════════╝
"""

processes = []

def signal_handler(sig, frame):
    print("\n\n🛑 Shutting down SmartReserve demo...")
    for p in processes:
        p.terminate()
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def run_backend():
    """Start FastAPI + Uvicorn on port 8000."""
    cmd = [sys.executable, "-m", "uvicorn", "main:app",
           "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
    p = subprocess.Popen(cmd, cwd=BACKEND_DIR)
    processes.append(p)
    p.wait()

def run_frontend():
    """Start static file server on port 3000."""
    cmd = [sys.executable, "server.py"]
    p = subprocess.Popen(cmd, cwd=FRONTEND_DIR)
    processes.append(p)
    p.wait()

def check_deps():
    """Verify required packages are installed."""
    required = ['fastapi', 'uvicorn', 'pydantic']
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"⚠  Missing packages: {', '.join(missing)}")
        print(f"   Install with: pip install {' '.join(missing)}")
        print(f"   Or run: pip install -r requirements.txt")
        return False
    return True

def main():
    print(BANNER)

    if not check_deps():
        print("\n❌ Please install dependencies first. See README.md")
        sys.exit(1)

    print("🚀 Starting SmartReserve services...\n")

    # Start backend in a thread
    backend_thread = threading.Thread(target=run_backend, daemon=True)
    backend_thread.start()
    time.sleep(2)

    # Start frontend in a thread
    frontend_thread = threading.Thread(target=run_frontend, daemon=True)
    frontend_thread.start()
    time.sleep(1)

    print("\n" + "═" * 66)
    print("  ✅ SmartReserve is running!\n")
    print("  📺  In-Cabin Dashboard  →  http://localhost:3000/car")
    print("  🔌  Charger Kiosk       →  http://localhost:3000/kiosk?station=TG0001")
    print("  🌐  Demo Hub            →  http://localhost:3000")
    print("  📡  API Docs            →  http://localhost:8000/docs")
    print("  💓  Health Check        →  http://localhost:8000/health")
    print()
    print("  DEMO STEPS:")
    print("  1. Open /car in left browser window")
    print("  2. Open /kiosk in right browser window")
    print("  3. Select a station on the map and click Reserve")
    print("  4. Watch kiosk turn BLUE (locked)")
    print("  5. Type wrong PIN → see red lockout")
    print("  6. Type correct PIN → charging starts!")
    print()
    print("  Press Ctrl+C to stop all services")
    print("═" * 66 + "\n")

    # Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        signal_handler(None, None)


if __name__ == "__main__":
    main()
