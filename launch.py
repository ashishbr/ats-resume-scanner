"""Start the web UI on a free local port and open it in your browser."""
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def free_port(start: int = 8501) -> int:
    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> int:
    port = free_port()
    url = f"http://localhost:{port}"

    def open_browser():
        time.sleep(3)
        webbrowser.open(url)

    print(f"\n  ATS Resume Scanner running at {url}\n  Press Ctrl+C to stop.\n")
    threading.Thread(target=open_browser, daemon=True).start()
    app = Path(__file__).resolve().parent / "app.py"
    return subprocess.call([
        sys.executable, "-m", "streamlit", "run", str(app),
        "--server.headless", "true",
        "--server.port", str(port),
        "--server.address", "localhost",
        "--browser.gatherUsageStats", "false",
    ])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
