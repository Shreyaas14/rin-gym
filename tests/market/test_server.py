"""Loopback HTTP integration: isolation, commands, and input boundaries."""

import http.client
import json
import threading
import unittest

from app.market.server import MarketServer


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = MarketServer(('127.0.0.1', 0))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def request(self, method='GET', path='/api/state', body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            kind = response.getheader('Content-Type', '')
            payload = json.loads(raw) if kind.startswith('application/json') else raw
            return response.status, dict(response.getheaders()), payload
        finally:
            connection.close()

    def test_cookie_isolates_two_clients_and_human_orders(self):
        status, first_headers, first = self.request()
        self.assertEqual(status, 200)
        _, second_headers, second = self.request()
        cookie_one = first_headers['Set-Cookie'].split(';')[0]
        cookie_two = second_headers['Set-Cookie'].split(';')[0]
        self.assertNotEqual(cookie_one, cookie_two)
        self.assertNotEqual(first['epoch'], second['epoch'])
        self.assertIn('HttpOnly', first_headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', first_headers['Set-Cookie'])
        body = {'epoch': first['epoch'], 'command_id': 'http-order', 'action': 'order',
                'outcome': 'YES', 'side': 'buy', 'price': 99, 'size': 3, 'tif': 'IOC'}
        status, _, filled = self.request('POST', '/api/command', json.dumps(body),
                                        {'Content-Type': 'application/json', 'Cookie': cookie_one,
                                         'Origin': f'http://127.0.0.1:{self.port}'})
        self.assertEqual(status, 200)
        self.assertEqual(filled['exchange']['accounts']['user']['position'], 3)
        _, _, isolated = self.request(headers={'Cookie': cookie_two})
        self.assertEqual(isolated['exchange']['accounts']['user']['position'], 0)
        self.assertEqual(isolated['exchange']['trades'], [])
        _, _, retried = self.request('POST', '/api/command', json.dumps(body),
                                     {'Content-Type': 'application/json', 'Cookie': cookie_one})
        self.assertEqual(retried['exchange'], filled['exchange'])
        _, _, journal = self.request(path='/api/journal', headers={'Cookie': cookie_one})
        self.assertEqual(journal['schema'], 'market-session/v1')
        self.assertTrue(any(e['type'] == 'fill' for e in journal['exchange']))

    def test_host_and_origin_boundaries(self):
        status, _, _ = self.request(headers={'Host': f'evil.example:{self.port}'})
        self.assertEqual(status, 403)
        for headers in ({'Origin': 'https://evil.example'}, {'Host': '127.0.0.1:1'}):
            status, _, _ = self.request('POST', '/api/command', '{}',
                                        {'Content-Type': 'application/json', **headers})
            self.assertEqual(status, 403)

    def test_json_body_validation_and_unknown_commands(self):
        for body, headers, expected in (
            ('{}', {'Content-Type': 'text/plain'}, 415),
            ('[]', {'Content-Type': 'application/json'}, 400),
            ('not-json', {'Content-Type': 'application/json'}, 400),
            ('', {'Content-Type': 'application/json'}, 400),
            ('{}', {'Content-Type': 'application/json', 'Content-Length': '65537'}, 400),
        ):
            with self.subTest(body=body, headers=headers):
                status, _, _ = self.request('POST', '/api/command', body, headers)
                self.assertEqual(status, expected)
        _, headers, state = self.request()
        cookie = headers['Set-Cookie'].split(';')[0]
        body = {'epoch': state['epoch'], 'command_id': 'invalid', 'action': 'unknown'}
        status, _, result = self.request('POST', '/api/command', json.dumps(body),
                                         {'Content-Type': 'application/json', 'Cookie': cookie})
        self.assertEqual(status, 400)
        self.assertEqual(result['command_error'], 'Unknown command')
        self.assertEqual(result['exchange']['accounts']['user']['cash'], 100_000)

    def test_static_allowlist_rejects_path_traversal(self):
        for path in ('/../server.py', '/%2e%2e/server.py', '/static/../server.py', '/app/market/server.py'):
            with self.subTest(path=path):
                status, _, payload = self.request(path=path)
                self.assertEqual(status, 404)
                self.assertEqual(payload, {'error': 'Not found'})
        status, headers, payload = self.request(path='/')
        self.assertEqual(status, 200)
        self.assertIn(b'<!', payload)
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])

    def test_server_refuses_public_binding(self):
        with self.assertRaises(ValueError):
            MarketServer(('0.0.0.0', 0))


if __name__ == '__main__':
    unittest.main()
