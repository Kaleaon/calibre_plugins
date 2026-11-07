import http.server
import importlib.util
import json
import socket
import threading
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "fs", Path(__file__).resolve().parents[1] / "fanfic/flaresolverr.py"
)
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)


class FakeFlareSolverr(http.server.BaseHTTPRequestHandler):
    mode = "ok"

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mode = type(self).mode
        if mode == "html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>not flaresolverr</html>")
            return
        if mode == "http500":
            self.send_response(500)
            self.end_headers()
            return
        if body["cmd"] == "sessions.list":
            reply = {"status": "ok", "sessions": [], "version": "3.3.21"}
        elif mode == "ok":
            reply = {
                "status": "ok",
                "solution": {
                    "status": 200,
                    "response": "<html>" + "x" * 3000 + "</html>",
                },
            }
        elif mode == "challenged":
            reply = {
                "status": "ok",
                "solution": {
                    "status": 403,
                    "response": "<title>Just a moment...</title>",
                },
            }
        elif mode == "challenged200":
            reply = {
                "status": "ok",
                "solution": {
                    "status": 200,
                    "response": "<title>Just a moment...</title>",
                },
            }
        else:
            reply = {
                "status": "error",
                "message": "Error solving the challenge. Timeout after 60.0 seconds.",
            }
        data = json.dumps(reply).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)


class IniTests(unittest.TestCase):
    def test_settings_precedence(self):
        self.assertEqual(
            fs.settings_from_ini(""),
            {"address": "localhost", "port": "8191", "protocol": "http"},
        )
        text = "[defaults]\nflaresolverr_proxy_port:9000\n[www.royalroad.com]\nflaresolverr_proxy_address:192.168.1.5\nflaresolverr_proxy_port:9100\n"
        self.assertEqual(
            fs.settings_from_ini(text),
            {"address": "192.168.1.5", "port": "9100", "protocol": "http"},
        )
        self.assertEqual(
            fs.settings_from_ini("this is [not valid\n=="),
            {"address": "localhost", "port": "8191", "protocol": "http"},
        )
        self.assertEqual(
            fs.endpoint(fs.settings_from_ini("")), "http://localhost:8191/v1"
        )

    def test_enable_empty_and_idempotent(self):
        text, changed = fs.enable_in_ini("")
        self.assertEqual(
            (text, changed),
            ("[www.royalroad.com]\nuse_flaresolverr_proxy:true\n", True),
        )
        self.assertTrue(fs.is_enabled(text))
        again, changed = fs.enable_in_ini(text)
        self.assertEqual((again, changed), (text, False))

    def test_enable_preserves_other_settings(self):
        original = "# my notes\n[defaults]\nuser_agent:x\n\n[archiveofourown.org]\nis_adult:true\n"
        text, changed = fs.enable_in_ini(original)
        self.assertTrue(changed)
        self.assertTrue(text.startswith(original.rstrip("\n")))
        self.assertIn("[www.royalroad.com]\nuse_flaresolverr_proxy:true", text)

    def test_enable_inside_existing_section(self):
        original = "[www.royalroad.com]\nusername:me\n[defaults]\nfoo:bar\n"
        text, _ = fs.enable_in_ini(original)
        self.assertEqual(
            text,
            "[www.royalroad.com]\nuse_flaresolverr_proxy:true\nusername:me\n[defaults]\nfoo:bar\n",
        )
        self.assertEqual(text.count("[www.royalroad.com]"), 1)

    def test_false_becomes_true_without_duplicating(self):
        text, changed = fs.enable_in_ini(
            "[WWW.RoyalRoad.com]\nuse_flaresolverr_proxy: false\nusername:me\n"
        )
        self.assertTrue(changed)
        self.assertEqual(text.count("use_flaresolverr_proxy"), 1)
        self.assertIn("use_flaresolverr_proxy:true", text)
        self.assertTrue(fs.is_enabled(text))

    def test_key_in_a_different_section_does_not_count(self):
        original = "[archiveofourown.org]\nuse_flaresolverr_proxy:true\n"
        self.assertFalse(fs.is_enabled(original))
        text, changed = fs.enable_in_ini(original)
        self.assertTrue(changed)
        self.assertEqual(text.count("use_flaresolverr_proxy:true"), 2)

    def test_docker_command_matches_the_guide(self):
        guide = (
            Path(__file__).resolve().parents[1] / "docs" / "royalroad-flaresolverr.md"
        ).read_text(encoding="utf-8")
        self.assertIn("-p 127.0.0.1:8191:8191", fs.DOCKER_COMMAND)
        self.assertIn(fs.DOCKER_COMMAND, guide)

    def test_is_cloudflare_domain(self):
        self.assertTrue(fs.is_cloudflare_domain("royalroad.com"))
        self.assertTrue(fs.is_cloudflare_domain("www.royalroad.com"))
        self.assertTrue(fs.is_cloudflare_domain("scribblehub.com"))
        self.assertFalse(fs.is_cloudflare_domain("archiveofourown.org"))

    def test_is_403_error(self):
        self.assertTrue(fs.is_403_error("HTTP Error 403: Forbidden"))
        self.assertTrue(fs.is_403_error("Cloudflare bot challenge detected"))
        self.assertFalse(fs.is_403_error("HTTP Error 404: Not Found"))
        self.assertFalse(fs.is_403_error(None))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeFlareSolverr)
        cls.settings = {
            "address": "127.0.0.1",
            "port": str(cls.server.server_address[1]),
            "protocol": "http",
        }
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def mode(self, mode):
        FakeFlareSolverr.mode = mode

    def test_running(self):
        self.mode("ok")
        ok, message = fs.check_server(self.settings)
        self.assertTrue(ok)
        self.assertIn("3.3.21", message)

    def test_not_running(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = str(s.getsockname()[1])
        ok, message = fs.check_server(
            {"address": "127.0.0.1", "port": port, "protocol": "http"}, timeout=2
        )
        self.assertFalse(ok)
        self.assertIn("Nothing answered", message)

    def test_something_else_on_the_port(self):
        self.mode("html")
        ok, message = fs.check_server(self.settings)
        self.assertFalse(ok)
        self.assertIn("did not reply like FlareSolverr", message)
        self.mode("http500")
        ok, message = fs.check_server(self.settings)
        self.assertFalse(ok)
        self.assertIn("HTTP 500", message)

    def test_fetch_success(self):
        self.mode("ok")
        ok, message = fs.fetch_through(self.settings, max_timeout=5000)
        self.assertTrue(ok, message)
        self.assertIn("HTTP 200", message)

    def test_fetch_still_challenged(self):
        for mode in ("challenged", "challenged200"):
            self.mode(mode)
            ok, message = fs.fetch_through(self.settings, max_timeout=5000)
            self.assertFalse(ok)
            self.assertIn("bot challenge", message)

    def test_fetch_error_reason_is_passed_on(self):
        self.mode("error")
        ok, message = fs.fetch_through(self.settings, max_timeout=5000)
        self.assertFalse(ok)
        self.assertIn("Timeout after 60.0 seconds", message)


if __name__ == "__main__":
    unittest.main()
