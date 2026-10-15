"""Start a private local test server, check health, then clean up only its process."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parent.parent
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [sys.executable, "-m", "uvicorn", "backend.server:app", "--host",
               "127.0.0.1", "--port", str(port), "--no-access-log"]
    for cycle in range(2):
        process = subprocess.Popen(command, cwd=root, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Local smoke service exited before health check")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                        health = json.load(response)
                    assert health == {"status": "ok", "schema_version": 2}, health
                    print(f"LOCAL_SERVICE_CYCLE_{cycle + 1}_HEALTH_OK", flush=True)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Local smoke service health timeout")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    print("LOCAL_TEST_SERVICE_RESTART_AND_CLEANUP_OK")


if __name__ == "__main__":
    main()
