# Nifty — metadata source

The `__init__.py` in this folder is the whole of this Calibre metadata source: a thin `Source` subclass that delegates to the `metadata_source` module of the plugin in `../nifty/`, which does the real searching with that plugin's own code and saved settings.

It ships as its own ZIP (`NiftyMetadataSource.zip`, built by `build_plugins.py`) because Calibre registers exactly one plugin per ZIP — the plugin in `../nifty/` is an InterfaceAction and cannot share `NiftyDownloader.zip` with a metadata source. See the *Metadata sources* section of the main README.
