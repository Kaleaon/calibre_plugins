# -*- coding: utf-8 -*-
"""Calibre Link interface action: owns the bridge server's lifecycle
and the integrated Tailscale (tsnet) sidecar.

- initialization_complete: calibre has a library open -> start the
  bridge, then start the Tailscale node (if enabled).
- library_changed: both keep working (the adapter asks for the
  current db on every request); just refresh the connection file.
- shutting_down: stop the sidecar, then the bridge.
"""
import os
import subprocess
import threading

from calibre.gui2.actions import InterfaceAction
from calibre_plugins.calibre_link import adapter as adapter_mod
from calibre_plugins.calibre_link.adapter import CalibreAdapter
from calibre_plugins.calibre_link.config import ConfigWidget, get_token, prefs
from calibre_plugins.calibre_link.server import BridgeServer


def _sidecar_resource():
    """Zip path of the bundled tsnet sidecar for this OS, or None."""
    from calibre.constants import islinux, iswindows
    if iswindows:
        return "tailscale/calibre-tsnet-windows-amd64.exe"
    if islinux:
        return "tailscale/calibre-tsnet-linux-amd64"
    return None  # not bundled for this platform in this build


class CalibreLinkAction(InterfaceAction):
    name = "Calibre Link"
    action_spec = ("Calibre Link", None,
                   "Show Calibre Link bridge status / connection info",
                   None)

    # -- lifecycle ------------------------------------------------------
    def genesis(self):
        self.server = None
        self.sidecar = None
        self._sidecar_stopping = False
        self.ts_status = {"state": "off", "auth_url": None, "ip": None,
                          "dns": None, "bridge": False, "error": None}
        self.adapter = CalibreAdapter(lambda: self.gui.current_db)
        self.qaction.triggered.connect(self.show_status)

    def initialization_complete(self):
        self.start_server()
        self.start_sidecar()

    def library_changed(self, db):
        self._write_connection_file()

    def shutting_down(self):
        self.stop_sidecar()
        self.stop_server()

    # -- bridge server ----------------------------------------------------
    def start_server(self):
        self.stop_server()
        self.server = BridgeServer(prefs.get("host", "0.0.0.0"),
                                   int(prefs.get("port", 8765)),
                                   get_token(),
                                   self.adapter)
        self.server.start()
        self._write_connection_file()

    def stop_server(self):
        if self.server is not None:
            self.server.stop()
            self.server = None

    # -- Tailscale sidecar --------------------------------------------------
    def _resource_bytes(self, resource):
        """Return the raw bytes of a file inside the plugin ZIP, or None.

        Fixed 2026-10-26 after browser-checking the calibre docs:
        ``load_resources()`` returns ``{name: file_contents}`` — bytes,
        NOT filesystem paths (manual.calibre-ebook.com/gl/plugins.html),
        and it lives on the Plugin base class, not on InterfaceAction.
        GUI plugin modules instead get a builtin ``get_resources()``
        from calibre's plugin loader, which likewise returns raw bytes
        for a single name (Plugin tutorial, "Getting resources from
        the plugin ZIP file"). The old code treated the returned value
        as a path and called os.path.exists() on 22 MB of binary, so
        the sidecar could never be found in a real calibre install.
        Try every documented route, in order.
        """
        # 1) Loader builtin injected into this module's globals.
        try:
            fn = globals().get("get_resources")
            if fn is not None:
                out = fn(resource)
                if isinstance(out, (bytes, bytearray)) and out:
                    return bytes(out)
                if isinstance(out, dict):
                    data = out.get(resource)
                    if isinstance(data, (bytes, bytearray)) and data:
                        return bytes(data)
                out = fn([resource])
                if isinstance(out, dict):
                    data = out.get(resource)
                    if isinstance(data, (bytes, bytearray)) and data:
                        return bytes(data)
        except Exception:
            pass
        # 2) load_resources(), if this calibre version exposes it here
        #    or on the underlying plugin object. It returns bytes too.
        for owner in (self, getattr(self, "plugin", None)):
            fn = getattr(owner, "load_resources", None) if owner else None
            if fn is None:
                continue
            try:
                out = fn([resource])
                if isinstance(out, dict):
                    data = out.get(resource)
                    if isinstance(data, (bytes, bytearray)) and data:
                        return bytes(data)
            except Exception:
                pass
        # 3) Read the plugin ZIP directly with zipfile.
        try:
            import zipfile
            candidates = []
            loader = globals().get("__loader__")
            archive = getattr(loader, "archive", None)
            if archive:
                candidates.append(archive)
            # __file__ may be <zip>/calibre_plugins/... or a real path.
            parts = os.path.abspath(__file__).split(".zip")
            if len(parts) > 1:
                candidates.append(parts[0] + ".zip")
            try:
                from calibre.utils.config import config_dir
                pdir = os.path.join(config_dir, "plugins")
                if os.path.isdir(pdir):
                    for name in os.listdir(pdir):
                        if name.lower().endswith(".zip"):
                            candidates.append(os.path.join(pdir, name))
            except Exception:
                pass
            for cand in candidates:
                try:
                    if cand and os.path.exists(cand):
                        with zipfile.ZipFile(cand) as zf:
                            if resource in zf.namelist():
                                return zf.read(resource)
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _sidecar_path(self):
        resource = _sidecar_resource()
        if resource is None:
            return None
        basename = resource.rsplit("/", 1)[-1]
        # Dev / unpacked fallback: a real file next to this module.
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 *resource.split("/"))
        data = self._resource_bytes(resource)
        if data is None:
            return candidate if os.path.exists(candidate) else None
        # Extract the bytes to a stable, executable file under the
        # calibre config dir. Cache by content hash so the 22 MB
        # binary is not rewritten on every calibre start.
        import hashlib
        digest = hashlib.sha256(data).hexdigest()
        bin_dir = os.path.join(self._ts_dir(), "bin")
        os.makedirs(bin_dir, exist_ok=True)
        dest = os.path.join(bin_dir, basename)
        marker = dest + ".sha256"
        try:
            if os.path.exists(dest) and os.path.exists(marker):
                with open(marker, encoding="utf-8") as f:
                    if f.read().strip() == digest and \
                            os.path.getsize(dest) == len(data):
                        if os.name != "nt":
                            os.chmod(dest, 0o755)
                        return dest
        except Exception:
            pass
        tmp = dest + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        # Bytes written from a ZIP carry no executable bit — set it
        # here, not only in start_sidecar, so the extracted file is
        # runnable as-is (found by the headless extraction test).
        if os.name != "nt":
            try:
                os.chmod(dest, 0o755)
            except Exception:
                pass
        with open(marker, "w", encoding="utf-8") as f:
            f.write(digest)
        return dest

    def _ts_dir(self):
        from calibre.utils.config import config_dir
        path = os.path.join(config_dir, "calibre_link")
        os.makedirs(path, exist_ok=True)
        return path

    def start_sidecar(self):
        self.stop_sidecar()
        self._sidecar_stopping = False
        if not prefs.get("ts_enabled", True):
            self.ts_status = {"state": "off", "auth_url": None, "ip": None,
                              "dns": None, "bridge": False, "error": None}
            self._write_connection_file()
            return
        self.ts_status = {"state": "starting", "auth_url": None,
                          "ip": None, "dns": None, "bridge": False,
                          "error": None}
        try:
            path = self._sidecar_path()
        except Exception as e:
            path = None
            self.ts_status.update(
                state="error",
                error="could not extract Tailscale sidecar: %s" % e)
            self._write_connection_file()
            return
        if path is None:
            self.ts_status.update(
                state="error",
                error="no Tailscale sidecar found for this platform "
                      "(extraction from the plugin ZIP failed — "
                      "see tsnet.log / reinstall the plugin)")
            self._write_connection_file()
            return
        try:
            if os.name != "nt":
                os.chmod(path, 0o755)
        except Exception:
            pass
        port = int(prefs.get("port", 8765))
        env = dict(os.environ)
        env["CLTS_HOSTNAME"] = prefs.get("ts_hostname", "calibre-link")
        env["CLTS_STATE_DIR"] = os.path.join(self._ts_dir(), "tsnet-state")
        env["CLTS_LISTEN"] = ":%d" % port
        env["CLTS_TARGET"] = "http://127.0.0.1:%d" % port
        authkey = (prefs.get("ts_authkey") or "").strip()
        if authkey:
            env["CLTS_AUTHKEY"] = authkey
        kwargs = {}
        if os.name == "nt":
            # No console window flashing up behind calibre.
            kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.sidecar = subprocess.Popen(
                [path], env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1, **kwargs)
        except Exception as e:
            self.ts_status.update(state="error",
                                  error="could not launch sidecar: %s" % e)
            self._write_connection_file()
            return
        threading.Thread(target=self._sidecar_reader,
                         args=(self.sidecar,),
                         name="calibre-link-tsnet-log",
                         daemon=True).start()
        self._write_connection_file()

    def stop_sidecar(self):
        proc, self.sidecar = self.sidecar, None
        if proc is not None:
            self._sidecar_stopping = True
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _sidecar_reader(self, proc=None):
        """Mirror sidecar output to tsnet.log and track its markers."""
        if proc is None:
            proc = self.sidecar
        if proc is None or proc.stdout is None:
            return
        try:
            log_file = open(os.path.join(self._ts_dir(), "tsnet.log"),
                            "a", encoding="utf-8")
        except Exception:
            log_file = None
        try:
            for line in proc.stdout:
                if log_file is not None:
                    try:
                        log_file.write(line)
                        log_file.flush()
                    except Exception:
                        pass
                self._sidecar_line(line.strip())
        except Exception:
            pass
        finally:
            if log_file is not None:
                log_file.close()
        if not self._sidecar_stopping:
            self.ts_status.update(
                state="error",
                error="Tailscale node exited (code %s) — see tsnet.log"
                      % getattr(proc, "returncode", "?"))
            self._write_connection_file()

    def _sidecar_line(self, line):
        # tsnet's own auth loop (verified in tailscale.com v1.104.2
        # tsnet.go printAuthURLLoop) logs this line via UserLogf; treat
        # it as an auth marker too, in case our status polling misses it.
        if "go to: https://" in line and "tsnet" in line.lower():
            url = line.rsplit("go to:", 1)[-1].strip()
            if url:
                self.ts_status.update(state="auth", auth_url=url)
                self._write_connection_file()
            return
        if line.startswith("CALIBRE_TSNET auth_url="):
            self.ts_status.update(
                state="auth", auth_url=line.split("=", 1)[1].strip())
            self._write_connection_file()
        elif line.startswith("CALIBRE_TSNET ip="):
            fields = {}
            for part in line.split()[1:]:
                if "=" in part:
                    k, v = part.split("=", 1)
                    fields[k] = v
            self.ts_status.update(state="up", ip=fields.get("ip"),
                                  dns=fields.get("dns"))
            self._write_connection_file()
        elif line.startswith("CALIBRE_TSNET bridge=ok"):
            self.ts_status.update(bridge=True)
            self._write_connection_file()

    # -- connection file ----------------------------------------------------
    def _ts_connection_lines(self):
        port = int(prefs.get("port", 8765))
        st = self.ts_status
        if st["state"] == "up":
            lines = ["Integrated Tailscale: CONNECTED — this calibre is "
                     "on your tailnet as its own device."]
            if st.get("dns"):
                lines.append("  Tailnet name: http://%s:%d"
                             % (st["dns"], port))
            if st.get("ip"):
                lines.append("  Tailnet IP:   http://%s:%d"
                             % (st["ip"], port))
            lines.append("That is the address Tinker uses — reachable "
                         "from anywhere on your tailnet. The token "
                         "above is still required.")
            return lines
        if st["state"] == "auth" and st.get("auth_url"):
            return ["Integrated Tailscale: WAITING FOR ONE-TIME LOGIN.",
                    "Open this link in your browser and approve the "
                    "device:",
                    "  %s" % st["auth_url"],
                    "You only do this once. Afterwards this file will "
                    "show the tailnet address Tinker uses."]
        if st["state"] == "starting":
            return ["Integrated Tailscale: starting — reopen this file "
                    "in a few seconds."]
        if st["state"] == "error":
            return ["Integrated Tailscale: not running — %s"
                    % (st.get("error") or "unknown error")]
        return ["Integrated Tailscale: off (enable it in Preferences "
                "-> Plugins -> Calibre Link)."]

    def _write_connection_file(self):
        try:
            adapter_mod.write_connection_file(
                self.adapter, prefs.get("host", "0.0.0.0"),
                int(prefs.get("port", 8765)), get_token(),
                extra_lines=self._ts_connection_lines())
        except Exception:
            pass  # connection file is a convenience, never fatal

    # -- config -----------------------------------------------------------
    def config_widget(self):
        return ConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save_settings()
        # Apply immediately so Joe does not have to restart calibre.
        self.start_server()
        self.start_sidecar()

    # -- status dialog ------------------------------------------------------
    def show_status(self):
        from qt.core import (QDialog, QDialogButtonBox, QPushButton,
                             QTextEdit, QVBoxLayout)
        host = prefs.get("host", "0.0.0.0")
        port = int(prefs.get("port", 8765))
        lines = ["Calibre Link", ""]
        if self.server is not None and self.server.running:
            lines.append("Bridge: RUNNING on %s:%d" % (host, port))
        elif self.server is not None and self.server.last_error:
            lines.append("Bridge: FAILED TO START — %s"
                         % self.server.last_error)
        else:
            lines.append("Bridge: not running")
        lines.append("Token: %s" % get_token())
        lines.append("")
        st = self.ts_status
        lines.append("Tailscale (integrated):")
        if st["state"] == "up":
            lines.append("  CONNECTED as %s" % (st.get("dns") or "?"))
            if st.get("ip"):
                lines.append("  Tailnet IP: %s" % st["ip"])
            lines.append("  Local bridge link: %s"
                         % ("OK" if st.get("bridge") else "pending"))
        elif st["state"] == "auth":
            lines.append("  WAITING FOR ONE-TIME LOGIN — open:")
            lines.append("  %s" % st.get("auth_url"))
        elif st["state"] == "starting":
            lines.append("  starting…")
        elif st["state"] == "error":
            lines.append("  ERROR: %s" % st.get("error"))
        else:
            lines.append("  off (Preferences -> Plugins -> Calibre Link)")
        lines.append("")
        try:
            info = self.adapter.health()
            lines.append("Library: %s" % info["library"])
            lines.append("Books: %s" % info["book_count"])
        except Exception as e:
            lines.append("Library info unavailable: %s" % e)
        lines += [
            "",
            "The same details are in CalibreLink-connection.txt in "
            "your library folder.",
        ]
        dlg = QDialog(self.gui)
        dlg.setWindowTitle("Calibre Link status")
        layout = QVBoxLayout(dlg)
        text = QTextEdit("\n".join(lines))
        text.setReadOnly(True)
        layout.addWidget(text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.reject)
        restart = QPushButton("Restart bridge")
        restart.clicked.connect(
            lambda: (self.start_server(), dlg.accept()))
        buttons.addButton(restart, QDialogButtonBox.ButtonRole.ActionRole)
        restart_ts = QPushButton("Restart Tailscale")
        restart_ts.clicked.connect(
            lambda: (self.start_sidecar(), dlg.accept()))
        buttons.addButton(restart_ts,
                          QDialogButtonBox.ButtonRole.ActionRole)
        layout.addWidget(buttons)
        dlg.resize(560, 380)
        dlg.exec()
