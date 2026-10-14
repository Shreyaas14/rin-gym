"""Loopback-only, dependency-free demo server with isolated browser sessions."""
import argparse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import urlsplit

from .agents import Valuations
from .catalog import Catalog
from .session import Session


STATIC = Path(__file__).parent / 'static'


class MarketServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address=('127.0.0.1', 8765), recorded=None, model='jev-1.13.0', dataset=None):
        if address[0] not in ('127.0.0.1', 'localhost'):
            raise ValueError('The paper demo binds to loopback only')
        self.catalog = Catalog(dataset)
        self.fixture = self.catalog.load(self.catalog.default_id)
        if recorded:
            try:
                source_hash = json.loads(Path(recorded).read_text())['source_hash']
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ValueError('Recording file is missing or malformed') from exc
            matching = [fixture for game in self.catalog.list_games()
                        if (fixture := self.catalog.load(game['id'])).source_hash == source_hash]
            if not matching:
                raise ValueError('Recording does not match an available selected game')
            self.fixture = matching[0]
        self.recorded, self.model = recorded, model
        self.sessions = {}
        self.registry_lock = threading.Lock()
        super().__init__(address, Handler)

    def session(self, cookie):
        try:
            parsed = SimpleCookie(cookie or '')
            token = parsed['rin_market'].value if 'rin_market' in parsed else None
        except Exception:
            token = None
        with self.registry_lock:
            if token not in self.sessions:
                if len(self.sessions) >= 32:
                    # Preserve existing demos rather than silently resetting one.
                    raise ValueError('Demo session capacity reached. Restart the server to clear sessions.')
                token = secrets.token_urlsafe(32)
                provider = Valuations(self.fixture.source_hash, self.recorded, self.model)
                self.sessions[token] = Session(self.fixture, provider, catalog=self.catalog)
            return token, self.sessions[token]


class Handler(BaseHTTPRequestHandler):
    server_version = 'RinMarket/1'

    def log_message(self, *_args):
        # No request bodies, cookies, provider errors or credentials in logs.
        pass

    def respond(self, status, data, kind='application/json; charset=utf-8', cookie=None):
        payload = json.dumps(data, allow_nan=False).encode() if not isinstance(data, bytes) else data
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Referrer-Policy', 'no-referrer')
        if cookie:
            self.send_header('Set-Cookie', f'rin_market={cookie}; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'localhost:{port}', f'127.0.0.1:{port}')

    def do_GET(self):
        if not self.allowed_host():
            self.respond(HTTPStatus.FORBIDDEN, {'error': 'Use the local demo URL'})
            return
        path = urlsplit(self.path).path
        if path == '/api/games':
            self.respond(HTTPStatus.OK, {'games': self.server.catalog.list_games(),
                                        'candidate_count': getattr(self.server.catalog, 'candidate_count',
                                                                   len(self.server.catalog.list_games())),
                                        'default_id': self.server.catalog.default_id})
            return
        if path in ('/api/state', '/api/journal', '/api/recording'):
            try:
                token, session = self.server.session(self.headers.get('Cookie'))
                with session.lock:
                    session.tick()
                    value = (session.export() if path == '/api/journal' else
                             session.valuations.recording() if path == '/api/recording' else session.snapshot())
                self.respond(HTTPStatus.OK, value, cookie=token)
            except ValueError as exc:
                self.respond(HTTPStatus.BAD_REQUEST, {'error': str(exc)})
            return
        files = {'/': ('index.html', 'text/html; charset=utf-8'),
                 '/style.css': ('style.css', 'text/css; charset=utf-8'),
                 '/app.js': ('app.js', 'text/javascript; charset=utf-8')}
        if path in files:
            filename, kind = files[path]
            self.respond(HTTPStatus.OK, (STATIC / filename).read_bytes(), kind)
        elif path == '/favicon.ico':
            self.respond(HTTPStatus.NO_CONTENT, b'', 'image/x-icon')
        else:
            self.respond(HTTPStatus.NOT_FOUND, {'error': 'Not found'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.allowed_host() or (origin and origin != 'http://' + self.headers.get('Host', '')):
            self.respond(HTTPStatus.FORBIDDEN, {'error': 'Use the local demo page'})
            return
        if urlsplit(self.path).path != '/api/command':
            self.respond(HTTPStatus.NOT_FOUND, {'error': 'Not found'})
            return
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            self.respond(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {'error': 'Send application/json'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536:
                raise ValueError('Command size must be between 1 and 65536 bytes')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Command must be a JSON object')
            token, session = self.server.session(self.headers.get('Cookie'))
            with session.lock:
                try:
                    session.command(body)
                    self.respond(HTTPStatus.OK, session.snapshot(), cookie=token)
                except ValueError as exc:
                    state = session.snapshot()
                    state['command_error'] = str(exc)
                    self.respond(HTTPStatus.BAD_REQUEST, state, cookie=token)
        except (ValueError, json.JSONDecodeError) as exc:
            self.respond(HTTPStatus.BAD_REQUEST, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser(description='Original match replay with an interactive paper order book')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--model', default='jev-1.13.0', help='Pinned TypeSafe model version')
    parser.add_argument('--recorded', type=Path, help='Compatible market-valuations/v1 JSON recording')
    parser.add_argument('--dataset', type=Path, help='Anonymous Parquet catalog file (defaults to data/processed/market-demo.parquet)')
    args = parser.parse_args()
    server = MarketServer(('127.0.0.1', args.port), args.recorded, args.model, args.dataset)
    print(f'Trade the Match: http://127.0.0.1:{server.server_port}', flush=True)
    print('Local simulation starts paused. Live Jev requires TYPESAFE_API_KEY in the server environment.', flush=True)
    print(f'{len(server.catalog.list_games())} anonymous games available.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
