import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from .core import ILUCore


core = ILUCore()


class ILUHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
        body = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

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

    def do_POST(self):

        if self.path != "/ask":
            self.send_json(404, {
                "error": "not_found"
            })
            return

        try:
            content_length = int(
                self.headers.get("Content-Length", "0")
            )

            raw_body = self.rfile.read(content_length)

            data = json.loads(
                raw_body.decode("utf-8")
            )

            message = data.get("message", "")

            result = core.process(message)

            status = 200 if result["success"] else 400

            self.send_json(status, result)

        except json.JSONDecodeError:
            self.send_json(400, {
                "success": False,
                "error": "invalid_json"
            })

        except Exception as error:
            self.send_json(500, {
                "success": False,
                "error": "internal_error",
                "detail": str(error)
            })


port = int(
    os.environ.get("PORT", "8000")
)

server = HTTPServer(
    ("0.0.0.0", port),
    ILUHandler
)

print(
    f"I.L.U. iniciado en el puerto {port}"
)

server.serve_forever()
