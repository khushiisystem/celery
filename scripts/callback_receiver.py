import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class CallbackHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else b"{}"

        try:
            parsed = json.loads(body.decode("utf-8"))
            pretty_body = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pretty_body = body.decode("utf-8", errors="replace")

        print("\n--- CALLBACK RECEIVED ---")
        print(f"path: {self.path}")
        print(pretty_body)
        print("-------------------------\n")

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status": "ok"}')


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 9000), CallbackHandler)
    print("Callback receiver listening on http://0.0.0.0:9000/callback")
    server.serve_forever()
