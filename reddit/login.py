"""Logging in to Reddit with OAuth ("Log in with Reddit"), without the plugin ever seeing a password.

The user's browser goes to Reddit's own authorization page; Reddit sends the browser back to a tiny server on this
computer (127.0.0.1 only), which receives a one-time code. The code is exchanged for a refresh token, which is all that is
kept. It is limited to reading, and can be revoked at any time (the Log out button does this, and so does
reddit.com/prefs/apps). Written to Reddit's documented OAuth2 flow; tested against a stand-in server, not verified live.

Reddit requires an app registered at reddit.com/prefs/apps for any of this, and may require approval for new apps."""
import base64
import http.server
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlparse

REDIRECT_PORT = 8844
REDIRECT_URI = f'http://127.0.0.1:{REDIRECT_PORT}/callback'
AUTHORIZE_URL = 'https://www.reddit.com/api/v1/authorize'
TOKEN_URL = 'https://www.reddit.com/api/v1/access_token'
REVOKE_URL = 'https://www.reddit.com/api/v1/revoke_token'
OAUTH = 'https://oauth.reddit.com'
SCOPES = 'read identity history'  # history is what Reddit requires for a user's list of submitted posts
LOGIN_USER_AGENT = 'calibre:reddit-follower:1.0 (login)'


class LoginError(Exception):
    """The login did not complete; the message says why in words for the user."""


def new_state():
    return secrets.token_urlsafe(16)


def authorize_url(client_id, state, redirect_uri=REDIRECT_URI, scope=SCOPES, base=None):
    query = urlencode({'client_id': client_id, 'response_type': 'code', 'state': state, 'redirect_uri': redirect_uri,
                       'duration': 'permanent', 'scope': scope})
    return f'{base or AUTHORIZE_URL}?{query}'


PAGE = ('<!doctype html><meta charset="utf-8"><title>Calibre Reddit Story Follower</title>'
        '<body style="font-family:sans-serif;margin:3em"><h2>{title}</h2><p>{text}</p></body>')


class CallbackServer:
    """Listens on 127.0.0.1 for Reddit's redirect. Use as a context manager so the port is always released."""

    def __init__(self, port=REDIRECT_PORT):
        if isinstance(port, (list, tuple)):
            self.ports = list(port)
        elif isinstance(port, int):
            if port == 0:
                self.ports = [0]
            else:
                self.ports = list(range(port, port + 11)) + [0]
        else:
            self.ports = [REDIRECT_PORT]

        self.port = self.ports[0] if self.ports else REDIRECT_PORT
        self._redirect_uri = None
        self.server, self.result, self.problem = None, None, ''
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != '/callback':
                    self.send_error(404)
                    return
                params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                if params.get('state') != outer.expected_state:
                    outer.problem = 'a request with the wrong security code was ignored'
                    self._reply(400, 'Not recognised', 'This request did not come from the login you started, so it was ignored.')
                    return
                outer.result = params
                self._reply(200, 'You can close this tab', 'Return to Calibre to finish logging in.')

            def _reply(self, code, title, text):
                data = PAGE.format(title=title, text=text).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._handler = Handler
        self.expected_state = None

    @property
    def redirect_uri(self):
        if getattr(self, '_redirect_uri', None):
            return self._redirect_uri
        return f'http://127.0.0.1:{self.port}/callback'

    @redirect_uri.setter
    def redirect_uri(self, val):
        self._redirect_uri = val

    def _create_server(self, port):
        server = http.server.HTTPServer(('127.0.0.1', port), self._handler, bind_and_activate=False)
        server.allow_reuse_address = False
        try:
            server.server_bind()
            server.server_activate()
        except BaseException:
            server.server_close()
            raise
        return server

    def __enter__(self):
        last_exc = None
        for p in self.ports:
            try:
                self.server = self._create_server(p)
                self.port = self.server.server_address[1]
                last_exc = None
                break
            except OSError as exc:
                last_exc = exc

        if self.server is None:
            tested = ', '.join(str(p) for p in self.ports)
            raise LoginError(f'Port on this computer is already in use ({last_exc}). Tested ports: {tested}. Close the program using it and try again.') from last_exc
        self.server.timeout = 0.25
        return self

    def __exit__(self, *exc_info):
        if self.server is not None:
            self.server.server_close()

    def wait(self, state, timeout=180, cancelled=lambda: False):
        """Wait for Reddit to send the browser back. Returns the authorization code."""
        self.expected_state = state
        deadline = time.monotonic() + timeout
        while self.result is None:
            if cancelled():
                raise LoginError('Login cancelled.')
            if time.monotonic() >= deadline:
                raise LoginError('Timed out waiting for you to approve the login in the browser.'
                                 + (f' (Meanwhile {self.problem}.)' if self.problem else ''))
            self.server.handle_request()
        if self.result.get('error'):
            reason = self.result['error']
            raise LoginError('You declined the login on Reddit.' if reason == 'access_denied' else f'Reddit refused the login ({reason}).')
        if not self.result.get('code'):
            raise LoginError('Reddit sent the browser back without a login code.')
        return self.result['code']


def _basic(client_id, client_secret):
    return 'Basic ' + base64.b64encode(f'{client_id}:{client_secret or ""}'.encode()).decode()


def _post(fetcher, url, fields, client_id, client_secret, user_agent=LOGIN_USER_AGENT):
    return fetcher.get(url, post=urlencode(fields).encode(), headers={
        'Authorization': _basic(client_id, client_secret), 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': user_agent})


def exchange_code(fetcher, client_id, client_secret, code, redirect_uri=REDIRECT_URI, token_url=None):
    """Trade the one-time code for tokens. Returns Reddit's reply (access_token, refresh_token, ...)."""
    try:
        reply = json.loads(_post(fetcher, token_url or TOKEN_URL, {'grant_type': 'authorization_code', 'code': code,
                                                                   'redirect_uri': redirect_uri}, client_id, client_secret))
    except ValueError as exc:
        raise LoginError('Reddit did not answer in a way we could read.') from exc
    if not reply.get('access_token'):
        raise LoginError(f"Reddit would not give a token ({reply.get('error') or reply.get('message') or 'no reason given'}). "
                         f"Check the client id and that the app's redirect uri is exactly {redirect_uri}")
    if not reply.get('refresh_token'):
        raise LoginError('Reddit gave a short-lived token but no lasting login. Make sure the app is an "installed app" or '
                         '"web app" and try again.')
    return reply


def fetch_identity(fetcher, access_token, base=None):
    """The account name the token belongs to ('' if it cannot be read)."""
    try:
        data = json.loads(fetcher.get((base or OAUTH) + '/api/v1/me', headers={'Authorization': f'bearer {access_token}', 'User-Agent': LOGIN_USER_AGENT}))
    except (ValueError, IOError):
        return ''
    return str(data.get('name', '')) if isinstance(data, dict) else ''


def revoke(fetcher, client_id, client_secret, refresh_token, revoke_url=None):
    """Ask Reddit to forget the refresh token. Returns True if Reddit accepted the request."""
    try:
        _post(fetcher, revoke_url or REVOKE_URL, {'token': refresh_token, 'token_type_hint': 'refresh_token'}, client_id, client_secret)
        return True
    except IOError:
        return False


def log_in(fetcher, client_id, client_secret, server, open_browser, cancelled=lambda: False, timeout=180,
           redirect_uri=None, authorize_base=None, token_url=None, base=None):
    """Run the whole login with an already-listening CallbackServer. open_browser(url) shows Reddit's page to the user.
    Returns {'refresh_token', 'username'}."""
    if not client_id:
        raise LoginError('Enter your Reddit app\'s client id first.')
    if redirect_uri is None or redirect_uri == REDIRECT_URI:
        redirect_uri = getattr(server, 'redirect_uri', f'http://127.0.0.1:{getattr(server, "port", REDIRECT_PORT)}/callback')
    state = new_state()
    server.expected_state = state
    open_browser(authorize_url(client_id, state, redirect_uri, base=authorize_base))
    code = server.wait(state, timeout, cancelled)
    tokens = exchange_code(fetcher, client_id, client_secret, code, redirect_uri, token_url)
    return {'refresh_token': tokens['refresh_token'], 'username': fetch_identity(fetcher, tokens['access_token'], base)}
