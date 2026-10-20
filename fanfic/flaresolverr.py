"""Helpers for the Royal Road setup dialog: talk to a FlareSolverr server the user runs, and edit the settings text.

Nothing here contacts a website except through the user's own FlareSolverr, and only when they press a test button.
"""

import atexit
import configparser
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

SECTION = "www.royalroad.com"
KEY = "use_flaresolverr_proxy"
TEST_URL = "https://www.royalroad.com/"
DOCKER_COMMAND = (
    "docker run -d --name=flaresolverr -p 127.0.0.1:8191:8191 -e LOG_LEVEL=info "
    "--restart unless-stopped ghcr.io/flaresolverr/flaresolverr:latest"
)
CHALLENGE_MARKERS = (
    "just a moment",
    "cf-mitigated",
    "challenge-platform",
    "checking your browser",
)
CLOUDFLARE_DOMAINS = (
    "royalroad.com",
    "fimfiction.net",
    "scribblehub.com",
    "sufficientvelocity.com",
)


def is_cloudflare_domain(domain):
    """Check if domain is known to be Cloudflare protected."""
    domain = (domain or "").lower().strip()
    return any(domain == d or domain.endswith("." + d) for d in CLOUDFLARE_DOMAINS)


def is_403_error(exc):
    """Return True if exception indicates HTTP 403 / Cloudflare bot challenge."""
    if exc is None:
        return False
    text = str(exc)
    low = text.lower()
    return (
        "403" in text
        or "forbidden" in low
        or "cloudflare" in low
        or "bot challenge" in low
        or "cf-mitigated" in low
    )


def settings_from_ini(text, section=SECTION):
    """Address, port and protocol FanFicFare will use: the site's section, then [defaults], then FanFicFare's own defaults."""
    found = {"address": "localhost", "port": "8191", "protocol": "http"}
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text or "")
    except configparser.Error:
        return found
    for name in ("defaults", section):  # later wins
        if parser.has_section(name):
            for key in found:
                value = parser.get(
                    name, f"flaresolverr_proxy_{key}", fallback=""
                ).strip()
                if value:
                    found[key] = value
    return found


def endpoint(settings):
    return f"{settings['protocol']}://{settings['address']}:{settings['port']}/v1"


def _post(settings, payload, timeout):
    request = urllib.request.Request(
        endpoint(settings),
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def check_server(settings, timeout=10):
    """Ask FlareSolverr to list its sessions, which touches no website. Returns (ok, message)."""
    try:
        reply = _post(settings, {"cmd": "sessions.list"}, timeout)
    except urllib.error.HTTPError as exc:
        return (
            False,
            f"The server at {endpoint(settings)} answered HTTP {exc.code}, which is not what FlareSolverr does. Is something else using that port?",
        )
    except (urllib.error.URLError, OSError):
        return False, (
            f"Nothing answered at {endpoint(settings)}. Start FlareSolverr (step 1), and check the address and port."
        )
    except ValueError:
        return (
            False,
            f"Something answered at {endpoint(settings)} but it did not reply like FlareSolverr. Is another program using that port?",
        )
    if isinstance(reply, dict) and reply.get("status") == "ok":
        version = f" (version {reply['version']})" if reply.get("version") else ""
        return True, f"FlareSolverr is running{version} at {endpoint(settings)}."
    message = reply.get("message") if isinstance(reply, dict) else ""
    return False, f"FlareSolverr answered but reported a problem: {message or reply}"


def fetch_through(settings, url=TEST_URL, max_timeout=60000):
    """Load one page through FlareSolverr and say what happened. Returns (ok, message)."""
    try:
        reply = _post(
            settings,
            {"cmd": "request.get", "url": url, "maxTimeout": int(max_timeout)},
            max_timeout / 1000 + 15,
        )
    except urllib.error.HTTPError as exc:
        try:
            reply = json.loads(exc.read().decode("utf-8", "replace"))
        except ValueError:
            return False, f"FlareSolverr answered HTTP {exc.code}."
    except (urllib.error.URLError, OSError):
        return (
            False,
            f"Could not reach FlareSolverr at {endpoint(settings)}. Run the first test.",
        )
    except ValueError:
        return False, "FlareSolverr sent a reply that could not be read."
    if not isinstance(reply, dict) or reply.get("status") != "ok":
        message = reply.get("message") if isinstance(reply, dict) else ""
        return (
            False,
            f"FlareSolverr could not load the page: {message or 'no reason given'}",
        )
    solution = reply.get("solution") or {}
    status = solution.get("status")
    body = solution.get("response") or ""
    low = body[:6000].lower()
    if status != 200 or any(marker in low for marker in CHALLENGE_MARKERS):
        return False, (
            f"FlareSolverr got HTTP {status}, and the page still looks like a bot challenge. "
            "It may need an update, or this challenge may need a person; see the browser-cache alternative in the guide."
        )
    return (
        True,
        f"Royal Road loaded through FlareSolverr (HTTP 200, {len(body):,} characters). Downloads should work now.",
    )


def _section_bounds(lines, section):
    header = re.compile(r"^\s*\[" + re.escape(section) + r"\]\s*$", re.I)
    any_header = re.compile(r"^\s*\[.+\]\s*$")
    start = next((i for i, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        return None, None
    end = next(
        (i for i in range(start + 1, len(lines)) if any_header.match(lines[i])),
        len(lines),
    )
    return start, end


def is_enabled(text, section=SECTION):
    lines = (text or "").splitlines()
    start, end = _section_bounds(lines, section)
    if start is None:
        return False
    for line in lines[start + 1 : end]:
        m = re.match(r"^\s*" + KEY + r"\s*[:=]\s*(\S+)", line)
        if m:
            return m.group(1).lower() == "true"
    return False


def enable_in_ini(text, section=SECTION):
    """Turn FlareSolverr on for the site without disturbing the rest of the user's settings. Returns (text, changed)."""
    text = text or ""
    lines = text.splitlines()
    start, end = _section_bounds(lines, section)
    wanted = f"{KEY}:true"
    if start is None:
        block = [f"[{section}]", wanted]
        new = lines + ([""] if lines and lines[-1].strip() else []) + block
    else:
        for i in range(start + 1, end):
            if re.match(r"^\s*" + KEY + r"\s*[:=]", lines[i]):
                if re.match(r"^\s*" + KEY + r"\s*[:=]\s*true\s*$", lines[i], re.I):
                    return text, False
                new = lines[:i] + [wanted] + lines[i + 1 :]
                break
        else:
            new = lines[: start + 1] + [wanted] + lines[start + 1 :]
    return "\n".join(new) + "\n", True


# ---------------------------------------------------------------- launching a FlareSolverr executable the user chose
LOCAL_ADDRESSES = ("localhost", "127.0.0.1", "::1")
WINDOWS_PROGRAMS = (".exe", ".bat", ".cmd")


def validate_executable(path):
    """Return a problem description, or '' if the file looks launchable."""
    path = os.path.expanduser(path or "")
    if not path:
        return "No FlareSolverr program has been chosen."
    if not os.path.isfile(path):
        return f"That file does not exist: {path}"
    if sys.platform.startswith("win"):
        if not path.lower().endswith(WINDOWS_PROGRAMS):
            return "On Windows, choose the flaresolverr.exe file from the folder you unpacked."
    elif not os.access(path, os.X_OK):
        return f'That file is not marked executable. In a terminal: chmod +x "{path}"'
    return ""


def looks_like_flaresolverr(path):
    return "flaresolverr" in os.path.basename(path or "").lower()


def _log_tail(path, lines=12):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            tail = stream.read()[-4000:].strip().splitlines()[-lines:]
        return "\n".join(tail)
    except OSError:
        return ""


class Launcher:
    """Starts and stops one FlareSolverr process on this computer. Only ever runs the file it is given."""

    def __init__(self):
        self.process = None
        self.log_path = None
        self._registered = False

    def running(self):
        return self.process is not None and self.process.poll() is None

    def start(self, path, settings, wait=90, cancelled=lambda: False):
        """Launch the program and wait until it answers. Returns (ok, message)."""
        problem = validate_executable(path)
        if problem:
            return False, problem
        if settings["address"].lower() not in LOCAL_ADDRESSES:
            return False, (
                f"Your settings point FlareSolverr at {settings['address']}, which is another machine. "
                "Start it there, or change the address in Advanced settings back to localhost."
            )
        if self.running():
            return (
                True,
                f"FlareSolverr was already started by this plugin (process {self.process.pid}).",
            )
        ok, _ = check_server(settings, timeout=3)
        if ok:
            return (
                True,
                "Something is already answering as FlareSolverr at that address, so no second copy was started.",
            )
        path = os.path.abspath(os.path.expanduser(path))
        env = dict(
            os.environ, HOST="127.0.0.1", PORT=str(settings["port"])
        )  # reachable from this computer only
        env.setdefault("LOG_LEVEL", "info")
        handle, self.log_path = tempfile.mkstemp(prefix="flaresolverr-", suffix=".log")
        kwargs = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            ) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = (
                True  # its own process group, so stopping it also stops the browser it starts
            )
        try:
            with os.fdopen(handle, "wb") as log:
                self.process = subprocess.Popen(
                    [path],
                    cwd=os.path.dirname(path),
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    **kwargs,
                )
        except OSError as exc:
            self.process = None
            return False, f"Could not start {path}: {exc}"
        if not self._registered:
            atexit.register(
                self.stop
            )  # do not leave a browser server running after Calibre quits
            self._registered = True
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if cancelled():
                self.stop()
                return False, "Cancelled; FlareSolverr was stopped."
            code = self.process.poll()
            if code is not None:
                self.process = None
                tail = _log_tail(self.log_path)
                return False, f"FlareSolverr exited straight away (code {code})." + (
                    f" Its last output:\n{tail}" if tail else ""
                )
            ok, _ = check_server(settings, timeout=2)
            if ok:
                return (
                    True,
                    f"FlareSolverr started (process {self.process.pid}) and is answering at {endpoint(settings)}.",
                )
            time.sleep(0.5)
        tail = _log_tail(self.log_path)
        self.stop()
        return (
            False,
            f"FlareSolverr did not start answering within {wait} seconds and was stopped."
            + (f" Its last output:\n{tail}" if tail else ""),
        )

    def stop(self):
        """Stop the process this object started, including any browser it launched. Returns a message."""
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return "This plugin has no running FlareSolverr to stop (one started another way is left alone)."
        try:
            if sys.platform.startswith("win"):
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=20,
                )
            else:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
        except (OSError, subprocess.SubprocessError):
            process.kill()
        return "FlareSolverr stopped."


launcher = Launcher()
