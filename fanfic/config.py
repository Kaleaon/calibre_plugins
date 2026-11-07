from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/fanfic_downloader")
prefs.defaults["allow_adult"] = (
    False  # adult-only sites are refused, and adult confirmations are not given, unless on
)
prefs.defaults["extra_adult"] = (
    ""  # extra domains to treat as adult sites, one per line
)
prefs.defaults["skip_existing"] = True
prefs.defaults["combine_stories"] = False
prefs.defaults["advanced_ini"] = ""  # FanFicFare personal.ini text
prefs.defaults["flaresolverr_path"] = (
    ""  # FlareSolverr program the setup dialog may start
)
