import http.server
import importlib.util
import json
import sys
import threading
import unittest
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "common"))
ROOT = Path(__file__).resolve().parents[1] / "reddit"


def load(name):
    spec = importlib.util.spec_from_file_location("r" + name, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["r" + name] = module
    spec.loader.exec_module(module)
    return module


core = load("core")
login = load("login")
FAKE_ID, FAKE_SECRET = "test-client-id", "test-secret-not-real"


class StandIn(http.server.BaseHTTPRequestHandler):
    log = []
    token_reply = None

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        type(self).log.append((self.path, dict(self.headers), parse_qs(body)))
        reply = (
            {}
            if self.path == "/revoke"
            else (
                type(self).token_reply
                or {
                    "access_token": "access1",
                    "refresh_token": "refresh1",
                    "expires_in": 3600,
                    "token_type": "bearer",
                }
            )
        )
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        type(self).log.append((self.path, dict(self.headers), {}))
        data = json.dumps({"name": "Reader"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


def browser_that(params_for):
    """A fake browser: 'opens' Reddit's page by sending the redirect straight back to the local server."""

    def open_browser(url):
        query = parse_qs(urlparse(url).query)
        params = params_for({k: v[0] for k, v in query.items()})

        def visit():
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{open_browser.port}/callback?"
                    + "&".join(f"{k}={v}" for k, v in params.items())
                ).read()
            except OSError:
                pass  # the server answers 400 to a forged request

        threading.Thread(target=visit).start()

    return open_browser


class LoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stand_in = http.server.ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
        threading.Thread(target=cls.stand_in.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.stand_in.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.stand_in.shutdown()

    def setUp(self):
        StandIn.log, StandIn.token_reply = [], None

    def run_login(self, params_for, secret="", port=0, timeout=5):
        with login.CallbackServer(port) as server:
            opener = browser_that(params_for)
            opener.port = server.server.server_address[1]
            return login.log_in(
                core.Fetcher(min_interval=0),
                FAKE_ID,
                secret,
                server,
                opener,
                timeout=timeout,
                redirect_uri=f"http://127.0.0.1:{opener.port}/callback",
                token_url=self.base + "/token",
                base=self.base,
            )

    def test_authorize_url_asks_for_a_permanent_read_only_login(self):
        query = {
            k: v[0]
            for k, v in parse_qs(
                urlparse(login.authorize_url("abc", "st8")).query
            ).items()
        }
        self.assertEqual(
            (
                query["client_id"],
                query["state"],
                query["duration"],
                query["scope"],
                query["redirect_uri"],
            ),
            (
                "abc",
                "st8",
                "permanent",
                "read identity history",
                "http://127.0.0.1:8844/callback",
            ),
        )
        self.assertTrue(
            login.authorize_url("abc", "s").startswith(
                "https://www.reddit.com/api/v1/authorize?"
            )
        )

    def test_full_login_returns_refresh_token_and_name(self):
        result = self.run_login(
            lambda q: {"code": "thecode", "state": q["state"]}, secret=FAKE_SECRET
        )
        self.assertEqual(result, {"refresh_token": "refresh1", "username": "Reader"})
        path, headers, form = StandIn.log[0]
        self.assertEqual(form["grant_type"], ["authorization_code"])
        self.assertEqual(form["code"], ["thecode"])
        self.assertTrue(headers["Authorization"].startswith("Basic "))
        self.assertEqual(StandIn.log[1][1]["Authorization"], "bearer access1")

    def test_wrong_state_is_ignored_until_timeout(self):
        with self.assertRaises(login.LoginError) as ctx:
            self.run_login(lambda q: {"code": "evil", "state": "forged"}, timeout=1)
        self.assertIn("Timed out", str(ctx.exception))
        self.assertIn("wrong security code", str(ctx.exception))
        self.assertEqual(StandIn.log, [], "no code was ever exchanged")

    def test_declined_login(self):
        with self.assertRaises(login.LoginError) as ctx:
            self.run_login(lambda q: {"error": "access_denied", "state": q["state"]})
        self.assertIn("declined", str(ctx.exception))

    def test_reddit_refusing_the_code_explains_the_redirect_uri(self):
        StandIn.token_reply = {"error": "invalid_grant"}
        with self.assertRaises(login.LoginError) as ctx:
            self.run_login(lambda q: {"code": "x", "state": q["state"]})
        self.assertIn("invalid_grant", str(ctx.exception))
        self.assertIn("redirect uri", str(ctx.exception))

    def test_no_refresh_token_is_reported(self):
        StandIn.token_reply = {"access_token": "a"}
        with self.assertRaises(login.LoginError):
            self.run_login(lambda q: {"code": "x", "state": q["state"]})

    def test_cancel_stops_waiting(self):
        with login.CallbackServer(0) as server:
            with self.assertRaises(login.LoginError):
                server.wait("s", timeout=5, cancelled=lambda: True)

    def test_busy_port_is_explained(self):
        with login.CallbackServer(0) as first:
            with self.assertRaises(login.LoginError) as ctx:
                login.CallbackServer(first.server.server_address[1]).__enter__()
        self.assertIn("already in use", str(ctx.exception))

    def test_missing_client_id(self):
        with self.assertRaises(login.LoginError):
            login.log_in(None, "", "", None, None)

    def test_revoke(self):
        self.assertTrue(
            login.revoke(
                core.Fetcher(min_interval=0),
                FAKE_ID,
                "",
                "refresh1",
                self.base + "/revoke",
            )
        )
        self.assertEqual(StandIn.log[0][2]["token"], ["refresh1"])

    def test_saved_login_is_used_by_the_api_source(self):
        source = core.make_source(
            "api", FAKE_ID, "", "Reader", lambda: False, "refresh1"
        )
        self.assertEqual(source.refresh_token, "refresh1")
        api = core.ApiSource(
            core.Fetcher(min_interval=0),
            FAKE_ID,
            "",
            "Reader",
            token_url=self.base + "/token",
            base=self.base,
            refresh_token="refresh1",
        )
        api._authorize()
        self.assertEqual(StandIn.log[0][2]["grant_type"], ["refresh_token"])
        self.assertEqual(StandIn.log[0][2]["refresh_token"], ["refresh1"])


if __name__ == "__main__":
    unittest.main()
