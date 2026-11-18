# FanFicFare Sites — metadata source

The `__init__.py` in this folder is the whole of this Calibre metadata source: a thin `Source` subclass that delegates to the `metadata_source` module of the plugin in `../fanfic/`, which does the real searching with that plugin's own code and saved settings.

It ships as its own ZIP (`FanficMetadataSource.zip`, built by `build_plugins.py`) because Calibre registers exactly one plugin per ZIP — the plugin in `../fanfic/` is an InterfaceAction and cannot share `FanficDownloader.zip` with a metadata source. See the *Metadata sources* section of the main README.
