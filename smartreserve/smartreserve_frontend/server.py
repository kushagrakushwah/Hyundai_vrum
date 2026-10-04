"""
Frontend static file server on port 3000
Serves:
  /car    → car_dashboard/index.html
  /kiosk  → kiosk_simulator/index.html  (use ?station=TG0001 for specific station)
  /       → demo landing page
"""
import os
import http.server
import socketserver
import urllib.parse


class SmartReserveHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip('/')
        qs = parsed.query

        base = os.path.dirname(__file__)

        if path in ('', '/'):
            self.serve_file(os.path.join(base, 'index.html'))
        elif path == '/car':
            self.serve_file(os.path.join(base, 'car_dashboard', 'index.html'))
        elif path.startswith('/car/'):
            rel = path[5:]
            self.serve_file(os.path.join(base, 'car_dashboard', rel))
        elif path == '/kiosk':
            content = open(os.path.join(base, 'kiosk_simulator', 'index.html'), 'rb').read()
            if qs:
                content = content.replace(b"window.location.search", f"'?{qs}'".encode())
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
        elif path.startswith('/kiosk/'):
            rel = path[7:]
            self.serve_file(os.path.join(base, 'kiosk_simulator', rel))
        else:
            self.send_error(404, 'Not found')

    def serve_file(self, filepath):
        if not os.path.exists(filepath):
            self.send_error(404, f'File not found: {filepath}')
            return
        ext = os.path.splitext(filepath)[1]
        content_types = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript',
            '.css': 'text/css',
            '.json': 'application/json',
            '.png': 'image/png',
            '.svg': 'image/svg+xml',
        }
        ct = content_types.get(ext, 'application/octet-stream')
        with open(filepath, 'rb') as f:
            content = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ct)
        self.send_header('Content-Length', len(content))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt, *args):
        print(f"  [HTTP] {self.address_string()} - {fmt % args}")


def run(port=3000):
    with socketserver.TCPServer(("", port), SmartReserveHandler) as httpd:
        httpd.allow_reuse_address = True
        print(f"  Frontend serving on http://localhost:{port}")
        print(f"  Car Dashboard → http://localhost:{port}/car")
        print(f"  Kiosk Sim     → http://localhost:{port}/kiosk?station=TG0001")
        httpd.serve_forever()


if __name__ == "__main__":
    run()
