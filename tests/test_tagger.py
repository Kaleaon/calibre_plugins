import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tagger'))

spec = importlib.util.spec_from_file_location('rules', Path(__file__).resolve().parents[1] / 'tagger/rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def book(i, **kw):
    base = {'id': i, 'title': f'Book {i}', 'authors': ['A'], 'tags': [], 'identifiers': {}, 'publisher': '', 'comments': '', 'series': ''}
    base.update(kw)
    return base


class DetectTests(unittest.TestCase):
    def test_identifier_key_value_and_publisher(self):
        s = rules.DEFAULT_SITES
        self.assertEqual(rules.detect_sites(book(1, identifiers={'metabods': 'x'}), s), ['Metabods'])
        self.assertEqual(rules.detect_sites(book(1, identifiers={'url': 'https://www.royalroad.com/fiction/1/x'}), s), ['Royal Road'])
        self.assertEqual(rules.detect_sites(book(1, publisher='archiveofourown.org'), s), ['Archive of Our Own'])
        self.assertEqual(rules.detect_sites(book(1, identifiers={'isbn': '123'}), s), [])

    def test_host_must_be_whole_word_and_comments_opt_in(self):
        s = rules.DEFAULT_SITES
        self.assertEqual(rules.detect_sites(book(1, identifiers={'url': 'https://notnifty.org.evil.com/'}), s), [])
        text = book(1, comments='<p>originally on royalroad.com</p>')
        self.assertEqual(rules.detect_sites(text, s), [])
        self.assertEqual(rules.detect_sites(text, s, scan_comments=True), ['Royal Road'])


class FanFicFareTests(unittest.TestCase):
    def test_fanficfare_identifier_form(self):
        # FanFicFare stores url identifiers with ':' turned into '|', and the publisher is the site's domain.
        s = rules.DEFAULT_SITES
        rr = book(1, identifiers={'url': 'https|//www.royalroad.com/fiction/21220'}, publisher='www.royalroad.com')
        self.assertEqual(rules.detect_sites(rr, s), ['Royal Road'])
        sb = book(2, identifiers={'url': 'https|//forums.spacebattles.com/threads/x.1/'})
        self.assertEqual(rules.detect_sites(sb, s), ['SpaceBattles'])
        self.assertEqual(rules.detect_sites(book(3, identifiers={'url': 'https|//archiveofourown.org/works/1'}), s), ['Archive of Our Own'])
        self.assertEqual(rules.detect_sites(book(4, identifiers={'uri': 'https|//www.scribblehub.com/series/1/'}), s), ['Scribble Hub'])

    def test_table_has_no_duplicate_hosts(self):
        hosts = [h for _, hs, _ in rules.DEFAULT_SITES for h in hs]
        self.assertEqual(len(hosts), len(set(hosts)))
        self.assertGreater(len(rules.DEFAULT_SITES), 100)
        names = [n for n, _, _ in rules.DEFAULT_SITES]
        self.assertEqual(names.count('Metabods'), 1)


class ParseTests(unittest.TestCase):
    def test_rules_and_errors(self):
        parsed, errors = rules.parse_rules('# c\ntag:=Cock Growth => Theme.Growth, Growth\nlitrpg => LitRPG\nre:(( => Bad\nnonsense\ntitle: => x\n')
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].tags, ['Theme.Growth', 'Growth'])
        self.assertEqual(len(errors), 3)

    def test_sites(self):
        sites, errors = rules.parse_sites('storiesonline.net, sol.net => Stories Online\nbroken')
        self.assertEqual(sites, [('Stories Online', ['storiesonline.net', 'sol.net'], [])])
        self.assertEqual(len(errors), 1)


class PlanTests(unittest.TestCase):
    def test_source_tags_additive_and_case_insensitive(self):
        books = [book(1, identifiers={'nifty': 'a'}, tags=['source.nifty', 'Keep']), book(2, identifiers={'metabods': 'b'}, tags=['X']),
                 book(3)]
        plan = rules.plan_changes(books)
        self.assertEqual([p['id'] for p in plan], [2])  # book 1 already has the tag (any case); book 3 has no source
        self.assertEqual(plan[0]['tags'], ['X', 'Source.Metabods'])

    def test_rules_fields_and_series(self):
        parsed, _ = rules.parse_rules('tag:=Muscle Growth => Theme.Growth\nsource:Royal => Web serial\ntitle:re:^The\\b => Starts with The\nlitrpg => LitRPG')
        books = [book(1, tags=['Muscle Growth', 'Other'], title='The End', series='Saga', identifiers={'royalroad': '1'}),
                 book(2, tags=['Muscle Growth Extra'], title='Zed')]
        plan = {p['id']: p['add'] for p in rules.plan_changes(books, series_prefix='Series.', rules=parsed)}
        self.assertEqual(plan[1], ['Source.Royal Road', 'Series.Saga', 'Theme.Growth', 'Web serial', 'Starts with The'])
        self.assertNotIn(2, plan)  # '=' means exact tag match, so "Muscle Growth Extra" does not trigger

    def test_idempotent(self):
        b = book(1, identifiers={'nifty': 'a'})
        first = rules.plan_changes([b])
        b['tags'] = first[0]['tags']
        self.assertEqual(rules.plan_changes([b]), [])

    def test_merge_sites_deduplication(self):
        extra_text = 'archiveofourown.org => ao3\nao3 => ao3_key\nmycustomsite.org => My Custom Site'
        extra, _ = rules.parse_sites(extra_text)
        merged = rules.merge_sites(rules.DEFAULT_SITES, extra)
        names = [n for n, _, _ in merged]
        self.assertNotIn('ao3', names)
        self.assertNotIn('ao3_key', names)
        self.assertIn('My Custom Site', names)

    def test_plan_changes_with_overlapping_extra_sites(self):
        extra, _ = rules.parse_sites('archiveofourown.org => ao3\nroyalroad.com => rr')
        merged = rules.merge_sites(rules.DEFAULT_SITES, extra)
        ao3_book = book(1, publisher='archiveofourown.org')
        rr_book = book(2, identifiers={'url': 'https://www.royalroad.com/fiction/1/test'})
        plan = rules.plan_changes([ao3_book, rr_book], sites=merged)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]['add'], ['Source.Archive of Our Own'])
        self.assertEqual(plan[1]['add'], ['Source.Royal Road'])


class JsonExportImportTests(unittest.TestCase):
    def test_export_package_format(self):
        pkg = rules.export_rule_set_package('tag:=A => B', 'example.com => Ex', True, 'Source.', 'Series.', True)
        self.assertEqual(pkg['version'], 1)
        self.assertEqual(pkg['plugin'], 'Story Collection Tagger')
        self.assertEqual(pkg['rules'], 'tag:=A => B')
        self.assertEqual(pkg['sites'], 'example.com => Ex')
        self.assertTrue(pkg['add_source'])
        self.assertEqual(pkg['source_prefix'], 'Source.')
        self.assertEqual(pkg['series_prefix'], 'Series.')
        self.assertTrue(pkg['scan_comments'])

    def test_merge_text_lines_deduplicates_exact_rules_and_preserves_comments(self):
        cur = '# Current\ntag:=A => B\nsource:Royal => RR'
        imp = '# Imported\ntag:=A => B\ntag:=C => D'
        merged = rules.merge_text_lines(cur, imp)
        lines = merged.splitlines()
        self.assertIn('tag:=A => B', lines)
        self.assertIn('tag:=C => D', lines)
        self.assertEqual(lines.count('tag:=A => B'), 1)
        self.assertIn('# Current', lines)
        self.assertIn('# Imported', lines)

    def test_validate_rule_set_returns_errors(self):
        valid_errs = rules.validate_rule_set('tag:=A => B', 'example.com => Ex')
        self.assertEqual(valid_errs, [])

        invalid_errs = rules.validate_rule_set('re:(( => Bad', 'broken_site')
        self.assertEqual(len(invalid_errs), 2)


class TaggerDialogTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        def make_plain_text_edit(text=''):
            m = MagicMock()
            m._text = str(text or '')
            m.toPlainText.side_effect = lambda: m._text
            m.setPlainText.side_effect = lambda t: setattr(m, '_text', str(t or ''))
            return m

        def make_line_edit(text=''):
            m = MagicMock()
            m._text = str(text or '')
            m.text.side_effect = lambda: m._text
            m.setText.side_effect = lambda t: setattr(m, '_text', str(t or ''))
            return m

        def make_check_box(text=''):
            m = MagicMock()
            m._checked = False
            m.isChecked.side_effect = lambda: m._checked
            m.setChecked.side_effect = lambda c: setattr(m, '_checked', bool(c))
            return m

        qt_mod.core.QPlainTextEdit.side_effect = lambda *args: make_plain_text_edit(args[0] if args else '')
        qt_mod.core.QLineEdit.side_effect = lambda *args: make_line_edit(args[0] if args else '')
        qt_mod.core.QCheckBox.side_effect = lambda *args: make_check_box(args[0] if args else '')

        tagger_pkg = types.ModuleType('calibre_plugins.story_collection_tagger')
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': '', 'sites': ''})
        tagger_pkg.rules = rules
        error_handler_mod = MagicMock()
        tagger_pkg.error_handler = error_handler_mod
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules
        sys.modules['calibre_plugins.story_collection_tagger.error_handler'] = error_handler_mod

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_show_dialog_sets_word_wrap_on_labels(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.show_dialog()

        qt_core = sys.modules['qt.core']
        self.assertGreaterEqual(qt_core.QLabel.return_value.setWordWrap.call_count, 4)

    def test_export_rule_set_writes_json_file(self):
        import tempfile
        import json
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tf:
            export_path = tf.name

        qt_core = sys.modules['qt.core']
        qt_core.QFileDialog.getSaveFileName.return_value = (export_path, 'JSON files (*.json)')

        action.show_dialog()

        # Find the do_export click connection or invoke it through export button
        # Search for QPushButton created with 'Export Rule Set'
        export_btn = None
        for call in qt_core.QPushButton.call_args_list:
            if call[0] and call[0][0] == 'Export Rule Set':
                export_btn = call
                break
        self.assertIsNotNone(export_btn)

        # Connect callback was registered on export_btn.clicked.connect
        connect_call = qt_core.QPushButton.return_value.clicked.connect.call_args_list
        # Trigger export callback
        do_export = None
        for c in connect_call:
            if 'do_export' in getattr(c[0][0], '__name__', ''):
                do_export = c[0][0]
                break
        if do_export is None and connect_call:
            do_export = connect_call[0][0][0]

        do_export()

        with open(export_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        self.assertEqual(data['plugin'], 'Story Collection Tagger')
        self.assertIn('rules', data)
        self.assertIn('sites', data)

    def test_import_rule_set_replace_mode(self):
        import tempfile
        import json
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        import_data = {
            'rules': 'tag:=New => TagNew',
            'sites': 'newsite.org => New Site',
            'add_source': False,
            'source_prefix': 'Src.',
            'series_prefix': 'Ser.',
            'scan_comments': True,
        }
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', encoding='utf-8', delete=False) as tf:
            json.dump(import_data, tf)
            import_path = tf.name

        qt_core = sys.modules['qt.core']
        qt_core.QFileDialog.getOpenFileName.return_value = (import_path, 'JSON files (*.json)')

        # Mock QMessageBox clickedButton to simulate clicking 'Replace'
        msg_mock = MagicMock()
        qt_core.QMessageBox.return_value = msg_mock

        # Setup buttons
        btn_count = [0]
        def add_button_mock(*args, **kwargs):
            btn_count[0] += 1
            btn = MagicMock()
            btn.name = args[0] if args else f'btn{btn_count[0]}'
            return btn

        msg_mock.addButton = add_button_mock
        # The second button added in do_import is replace_btn
        def exec_mock():
            # clicked button will be replace_btn (second added)
            msg_mock.clickedButton.return_value = msg_mock.addButton.call_args_list[1][0][0]

        # Let's collect buttons added
        added_btns = []
        msg_mock.addButton = lambda name, role=None: added_btns.append(name) or name

        def get_clicked():
            # Return replace_btn
            return added_btns[1] if len(added_btns) > 1 else 'Replace'

        msg_mock.clickedButton.side_effect = get_clicked

        action.show_dialog()

        # Find do_import callback
        connect_call = qt_core.QPushButton.return_value.clicked.connect.call_args_list
        do_import = connect_call[1][0][0] if len(connect_call) > 1 else None
        self.assertIsNotNone(do_import)

        do_import()

        prefs = sys.modules['calibre_plugins.story_collection_tagger.config'].prefs
        self.assertEqual(prefs['rules'], 'tag:=New => TagNew')
        self.assertEqual(prefs['sites'], 'newsite.org => New Site')

    def test_import_rule_set_merge_mode(self):
        import tempfile
        import json
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        prefs = sys.modules['calibre_plugins.story_collection_tagger.config'].prefs
        prefs['rules'] = 'tag:=Existing => TagOld'
        prefs['sites'] = 'oldsite.org => Old Site'

        import_data = {
            'rules': 'tag:=New => TagNew',
            'sites': 'newsite.org => New Site',
        }
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', encoding='utf-8', delete=False) as tf:
            json.dump(import_data, tf)
            import_path = tf.name

        qt_core = sys.modules['qt.core']
        qt_core.QFileDialog.getOpenFileName.return_value = (import_path, 'JSON files (*.json)')

        msg_mock = MagicMock()
        qt_core.QMessageBox.return_value = msg_mock
        added_btns = []
        msg_mock.addButton = lambda name, role=None: added_btns.append(name) or name
        # Return merge_btn (first added button)
        msg_mock.clickedButton.side_effect = lambda: added_btns[0] if added_btns else 'Merge'

        action.show_dialog()

        connect_call = qt_core.QPushButton.return_value.clicked.connect.call_args_list
        do_import = connect_call[1][0][0]

        do_import()

        self.assertIn('tag:=Existing => TagOld', prefs['rules'])
        self.assertIn('tag:=New => TagNew', prefs['rules'])
        self.assertIn('oldsite.org => Old Site', prefs['sites'])
        self.assertIn('newsite.org => New Site', prefs['sites'])

    def test_import_rule_set_syntax_error_does_not_corrupt_prefs(self):
        import tempfile
        import json
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        bad_data = {
            'rules': 're:(( => InvalidRegex',
            'sites': 'broken_site_line',
        }
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', encoding='utf-8', delete=False) as tf:
            json.dump(bad_data, tf)
            import_path = tf.name

        qt_core = sys.modules['qt.core']
        qt_core.QFileDialog.getOpenFileName.return_value = (import_path, 'JSON files (*.json)')

        msg_mock = MagicMock()
        qt_core.QMessageBox.return_value = msg_mock
        added_btns = []
        msg_mock.addButton = lambda name, role=None: added_btns.append(name) or name
        msg_mock.clickedButton.side_effect = lambda: added_btns[1] if len(added_btns) > 1 else 'Replace'

        action.show_dialog()

        connect_call = qt_core.QPushButton.return_value.clicked.connect.call_args_list
        do_import = connect_call[1][0][0]

        prefs = sys.modules['calibre_plugins.story_collection_tagger.config'].prefs
        original_rules = prefs['rules']

        do_import()

        # Prefs should remain unchanged
        self.assertEqual(prefs['rules'], original_rules)

    def test_import_rule_set_invalid_json_does_not_corrupt_prefs(self):
        import tempfile
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = []

        with tempfile.NamedTemporaryFile(suffix='.json', mode='w', encoding='utf-8', delete=False) as tf:
            tf.write("THIS IS NOT VALID JSON")
            import_path = tf.name

        qt_core = sys.modules['qt.core']
        qt_core.QFileDialog.getOpenFileName.return_value = (import_path, 'JSON files (*.json)')

        action.show_dialog()

        connect_call = qt_core.QPushButton.return_value.clicked.connect.call_args_list
        do_import = connect_call[1][0][0]

        prefs = sys.modules['calibre_plugins.story_collection_tagger.config'].prefs
        original_rules = prefs['rules']

        do_import()

        # Prefs should remain unchanged
        self.assertEqual(prefs['rules'], original_rules)


if __name__ == '__main__':
    unittest.main()
