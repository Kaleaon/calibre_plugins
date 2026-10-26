# -*- coding: utf-8 -*-
"""Calibre Link — a bridge that lives inside calibre.

Starts a small token-protected HTTP server when calibre starts, so
Muse (Tinker) can list, audit, back up and fix metadata in the
library over Tailscale, using calibre's own database object for
every write. See README.md in the plugin zip for the full story.
"""
from calibre.customize import InterfaceActionBase


class CalibreLinkPlugin(InterfaceActionBase):
    name = "Calibre Link"
    description = ("Starts a small token-protected HTTP bridge inside "
                   "calibre so Muse can organize your library over "
                   "Tailscale. Starts automatically with calibre; all "
                   "writes go through calibre's own database.")
    supported_platforms = ["windows", "osx", "linux"]
    author = "Tinker, for Joe Graham"
    version = (1, 5, 0)
    minimum_calibre_version = (6, 0, 0)
    actual_plugin = "calibre_plugins.calibre_link.ui:CalibreLinkAction"
