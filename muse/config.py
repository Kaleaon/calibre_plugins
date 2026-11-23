from calibre.utils.config import JSONConfig

prefs = JSONConfig("plugins/muse_library_fixer")
prefs.defaults["exchange_dir"] = ""
prefs.defaults["model"] = "muse"
prefs.defaults["fields"] = ["title", "authors", "series", "tags", "language", "isbn"]
prefs.defaults["min_confidence"] = "medium"
prefs.defaults["batch_size"] = 25
prefs.defaults["tags_remove"] = False
prefs.defaults["excerpt"] = False
prefs.defaults["undo"] = {}
prefs.defaults["min_interval"] = 0
prefs.defaults["wait_minutes"] = 10
prefs.defaults["covers"] = False
prefs.defaults["embed_updated"] = True
prefs.defaults["active_template"] = "Default Ebook Cleanup"
prefs.defaults["custom_templates"] = {}
prefs.defaults["batch_dir"] = ""
prefs.defaults["system_instruction_mode"] = "append"
prefs.defaults["custom_system_instructions"] = ""
prefs.defaults["enable_fallback"] = False
prefs.defaults["fallback_model"] = "muse"
prefs.defaults["provider_metadata"] = True
