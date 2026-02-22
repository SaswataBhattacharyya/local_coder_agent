import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from agent.inference_backend import RemoteOpenAIBackend


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        data = json.loads(body)
        content = "ok"
        if data.get("messages"):
            content = data["messages"][-1].get("content", "ok")
        resp = {"choices": [{"message": {"content": f"echo:{content}"}}]}
        out = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args, **kwargs):
        return


def _start_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_remote_backend_chat():
    server = _start_server()
    host, port = server.server_address
    backend = RemoteOpenAIBackend(base_url=f"http://{host}:{port}", model="test-model")
    out = backend.chat([{"role": "user", "content": "hi"}])
    assert out == "echo:hi"
    server.shutdown()


def test_remote_backend_chat_with_images():
    server = _start_server()
    host, port = server.server_address
    backend = RemoteOpenAIBackend(base_url=f"http://{host}:{port}", model="test-model")
    out = backend.chat_with_images([{"role": "user", "content": "img"}], images=[{"data": "data:image/png;base64,AAA"}])
    assert out == "echo:img"
    server.shutdown()
