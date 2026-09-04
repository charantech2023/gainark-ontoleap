"""
Launch script for the Ontology Intelligence Web Dashboard and FastAPI server.
Automatically detects an available port (defaults to 8000, falls back to 8080/8001 if occupied).
"""
import os
import sys
import socket
import uvicorn

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except OSError:
            return True

def get_available_port(preferred: int = 8000) -> int:
    env_port = os.environ.get("PORT")
    if env_port:
        return int(env_port)
    
    # Check CLI arguments e.g. python dashboard.py --port 8080
    for i, arg in enumerate(sys.argv):
        if arg in ["--port", "-p"] and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1])

    fallbacks = [8000, 8080, 8001, 8002, 8501]
    for p in fallbacks:
        if not is_port_in_use(p):
            return p
    return preferred

def main():
    port = get_available_port(8000)
    print("=" * 65)
    print(" ONTOLOGY INTELLIGENCE & READINESS DASHBOARD")
    print("=" * 65)
    print(f" [Web Dashboard] : http://localhost:{port}/dashboard")
    print(f" [API Docs]      : http://localhost:{port}/docs")
    print(f" [Audit Endpoint]: POST http://localhost:{port}/api/audit")
    print(f" [Bench Endpoint]: POST http://localhost:{port}/api/benchmark")
    print("=" * 65)
    
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()
