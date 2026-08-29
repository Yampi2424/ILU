import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class ILUHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"I.L.U. online")


port = int(os.environ.get("PORT", "8000"))

server = HTTPServer(("0.0.0.0", port), ILUHandler)

print(f"I.L.U. iniciado en el puerto {port}")

server.serve_forever()
