import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class ILUHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )
        self.send_header(
            "Content-Length",
            str(len(body))
        )
        self.end_headers()

        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send_json(200, {
                "name": "I.L.U.",
                "status": "online",
                "version": "0.1.0"
            })

        elif self.path == "/healthz":
            self.send_json(200, {
                "status": "ok"
            })

        elif self.path == "/about":
            self.send_json(200, {
                "name": "I.L.U.",
                "description": "Inteligencia Local Unificada",
                "version": "0.1.0",
                "mode": "cloud-ready"
            })

        else:
            self.send_json(404, {
                "error": "not_found"
            })


port = int(os.environ.get("PORT", "8000"))

server = HTTPServer(
    ("0.0.0.0", port),
    ILUHandler
)

print(f"I.L.U. iniciado en el puerto {port}")

server.serve_forever()
