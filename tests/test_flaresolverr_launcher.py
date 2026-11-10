import importlib.util
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import textwrap
import time
import unittest

spec = importlib.util.spec_from_file_location('fs', Path(__file__).resolve().parents[1] / 'fanfic/flaresolverr.py')
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)

POSIX = not sys.platform.startswith('win')

SERVER = textwrap.dedent('''\
    #!{python}
    import http.server, json, os, subprocess, sys
    open({envfile!r}, 'w').write(os.environ.get('HOST', '') + ' ' + os.environ.get('PORT', ''))
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(300)'])  # stands in for the browser FlareSolverr starts
    open({childfile!r}, 'w').write(str(child.pid))
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = json.dumps({{'status': 'ok', 'sessions': [], 'version': 'fake'}}).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(data)
    print('fake flaresolverr listening', flush=True)
    http.server.HTTPServer((os.environ['HOST'], int(os.environ['PORT'])), H).serve_forever()
''')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return str(s.getsockname()[1])


def alive(pid):
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        return p.status() != psutil.STATUS_ZOMBIE
    except Exception:
        pass

    if sys.platform.startswith('win'):
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            success = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            ctypes.windll.kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return bool(success and exit_code.value == STILL_ACTIVE)
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        proc_stat = Path(f'/proc/{pid}/stat')
        if proc_stat.exists():
            try:
                return proc_stat.read_text().split()[2] != 'Z'
            except OSError:
                return True
        try:
            import subprocess
            res = subprocess.run(['ps', '-p', str(pid), '-o', 'stat'], capture_output=True, text=True)
            if res.returncode != 0:
                return False
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            if not lines:
                return False
            state_line = lines[-1] if len(lines) > 1 else lines[0]
            if state_line.upper().startswith(('STAT', 'STATE')):
                return False
            return 'Z' not in state_line.upper()
        except Exception:
            return True


def wait_gone(pid, seconds=10):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


class ValidationTests(unittest.TestCase):
    def test_validate_and_name_guard(self):
        self.assertIn('No FlareSolverr', fs.validate_executable(''))
        self.assertIn('does not exist', fs.validate_executable('/nonexistent/flaresolverr'))
        with tempfile.TemporaryDirectory() as d:
            name = 'flaresolverr.exe' if sys.platform.startswith('win') else 'flaresolverr'
            path = Path(d) / name
            path.write_text('#!/bin/sh\n')
            if POSIX:
                path.chmod(0o644)
                self.assertIn('chmod +x', fs.validate_executable(str(path)))
                path.chmod(0o755)
                self.assertEqual(fs.validate_executable(str(path)), '')
            else:
                self.assertEqual(fs.validate_executable(str(path)), '')
            self.assertIn('does not exist', fs.validate_executable(d))  # a folder is not a program
        self.assertTrue(fs.looks_like_flaresolverr('/x/FlareSolverr.exe'))
        self.assertFalse(fs.looks_like_flaresolverr('/x/some-other-tool'))


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.envfile, self.childfile = self.dir / 'env.txt', self.dir / 'child.txt'
        self.settings = {'address': 'localhost', 'port': free_port(), 'protocol': 'http'}
        self.launchers = []

    def tearDown(self):
        for launcher in self.launchers:
            launcher.stop()
        try:
            self.tmp.cleanup()
        except OSError:
            import time; time.sleep(0.5)
            try:
                self.tmp.cleanup()
            except OSError:
                pass

    def launcher(self):
        launcher = fs.Launcher()
        self.launchers.append(launcher)
        return launcher

    def program(self, py_body, name=None):
        if name is None:
            name = 'flaresolverr.bat' if sys.platform.startswith('win') else 'flaresolverr'
        path = self.dir / name
        if sys.platform.startswith('win'):
            py_path = self.dir / f'{path.stem}_script.py'
            py_path.write_text(py_body)
            path.write_text(f'@echo off\n"{sys.executable}" "{py_path.resolve()}" %*\nexit /b %errorlevel%\n')
        else:
            if not py_body.startswith('#!'):
                py_body = f'#!{sys.executable}\n' + py_body
            path.write_text(py_body)
            path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def server_program(self):
        return self.program(SERVER.format(python=sys.executable, envfile=str(self.envfile), childfile=str(self.childfile)))

    def test_start_answer_and_stop_kills_the_whole_tree(self):
        launcher = self.launcher()
        ok, message = launcher.start(self.server_program(), self.settings, wait=30)
        self.assertTrue(ok, message)
        self.assertTrue(launcher.running())
        self.assertTrue(fs.check_server(self.settings)[0])
        child = int(self.childfile.read_text())
        self.assertTrue(alive(child))
        self.assertEqual(self.envfile.read_text(), f"127.0.0.1 {self.settings['port']}")  # bound to this computer only
        self.assertIn('stopped', launcher.stop())
        self.assertFalse(launcher.running())
        self.assertFalse(fs.check_server(self.settings, timeout=2)[0])
        self.assertTrue(wait_gone(child), 'the helper process it started was left running')

    def test_does_not_start_a_second_copy(self):
        first, second = self.launcher(), self.launcher()
        self.assertTrue(first.start(self.server_program(), self.settings, wait=30)[0])
        ok, message = second.start(self.server_program(), self.settings, wait=30)
        self.assertTrue(ok)
        self.assertIn('already answering', message)
        self.assertIsNone(second.process)
        self.assertIn('already started', first.start(self.server_program(), self.settings)[1])

    def test_remote_address_is_refused(self):
        ok, message = self.launcher().start(self.server_program(), dict(self.settings, address='192.168.1.9'))
        self.assertFalse(ok)
        self.assertIn('another machine', message)
        self.assertFalse(self.envfile.exists())  # nothing was launched

    def test_bad_path_is_refused(self):
        ok, message = self.launcher().start(str(self.dir / 'missing'), self.settings)
        self.assertFalse(ok)
        self.assertIn('does not exist', message)

    def test_program_that_exits_reports_its_output(self):
        launcher = self.launcher()
        code = 'import sys\nprint("boom: no chrome found")\nsys.exit(3)\n'
        ok, message = launcher.start(self.program(code), self.settings, wait=20)
        self.assertFalse(ok)
        self.assertIn('code 3', message)
        self.assertIn('boom: no chrome found', message)
        self.assertFalse(launcher.running())

    def test_program_that_never_answers_is_stopped(self):
        launcher = self.launcher()
        code = 'import time\ntime.sleep(300)\n'
        ok, message = launcher.start(self.program(code), self.settings, wait=2)
        self.assertFalse(ok)
        self.assertIn('did not start answering', message)
        self.assertFalse(launcher.running())

    def test_cancel_stops_it(self):
        launcher = self.launcher()
        code = 'import time\ntime.sleep(300)\n'
        ok, message = launcher.start(self.program(code), self.settings, wait=30, cancelled=lambda: True)
        self.assertFalse(ok)
        self.assertIn('Cancelled', message)
        self.assertFalse(launcher.running())

    def test_stop_with_nothing_running(self):
        self.assertIn('no running FlareSolverr', self.launcher().stop())


if __name__ == '__main__':
    unittest.main()
