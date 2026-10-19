from calibre.utils.config import JSONConfig

prefs = JSONConfig('plugins/gemini_library_fixer')
prefs.defaults['api_key'] = ''
prefs.defaults['model'] = 'gemini-2.5-flash'
prefs.defaults['fields'] = ['title', 'authors', 'series', 'tags', 'language', 'isbn']
prefs.defaults['min_confidence'] = 'medium'
prefs.defaults['batch_size'] = 10
prefs.defaults['tags_remove'] = False
prefs.defaults['excerpt'] = False
prefs.defaults['undo'] = {}
prefs.defaults['min_interval'] = 7
prefs.defaults['covers'] = False
prefs.defaults['active_template'] = 'Default Ebook Cleanup'
prefs.defaults['custom_templates'] = {}
prefs.defaults['batch_dir'] = ''
prefs.defaults['system_instruction_mode'] = 'append'
prefs.defaults['custom_system_instructions'] = ''
