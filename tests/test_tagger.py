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


class TaggerDialogTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        tagger_pkg = types.ModuleType('calibre_plugins.story_collection_tagger')
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': '', 'sites': ''})
        tagger_pkg.rules = rules
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules

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


class SelectionStateTests(unittest.TestCase):
    def setUp(self):
        self.books = [
            book(1, identifiers={'royalroad': '1'}, tags=['Existing1']),
            book(2, identifiers={'royalroad': '2'}, tags=['Existing2']),
            book(3, identifiers={'metabods': '3'}),
        ]
        parsed_rules, _ = rules.parse_rules('title:Book => LitRPG')
        self.plan = rules.plan_changes(self.books, rules=parsed_rules)

    def test_distinct_tags_and_counts(self):
        state = rules.TagSelectionState(self.plan)
        distinct = dict(state.get_distinct_tags())
        self.assertEqual(distinct['Source.Royal Road'], 2)
        self.assertEqual(distinct['Source.Metabods'], 1)
        self.assertEqual(distinct['LitRPG'], 3)

        assignments, affected = state.get_summary_counts()
        self.assertEqual(assignments, 6)
        self.assertEqual(affected, 3)

    def test_global_toggle_disables_tag_across_all_books(self):
        state = rules.TagSelectionState(self.plan)
        state.set_global_tag('Source.Royal Road', False)

        active = state.get_active_plan()
        active_dict = {p['id']: p['add'] for p in active}
        self.assertEqual(active_dict[1], ['LitRPG'])
        self.assertEqual(active_dict[2], ['LitRPG'])
        self.assertEqual(active_dict[3], ['Source.Metabods', 'LitRPG'])

        assignments, affected = state.get_summary_counts()
        self.assertEqual(assignments, 4)
        self.assertEqual(affected, 3)

    def test_book_override_does_not_change_global_state(self):
        state = rules.TagSelectionState(self.plan)
        state.set_book_tag(1, 'Source.Royal Road', False)

        self.assertTrue(state.global_tags['Source.Royal Road'])
        self.assertFalse(state.is_tag_active(1, 'Source.Royal Road'))
        self.assertTrue(state.is_tag_active(2, 'Source.Royal Road'))

        active = state.get_active_plan()
        active_dict = {p['id']: p['add'] for p in active}
        self.assertEqual(active_dict[1], ['LitRPG'])
        self.assertEqual(active_dict[2], ['Source.Royal Road', 'LitRPG'])

    def test_retoggling_global_tag_updates_all_books(self):
        state = rules.TagSelectionState(self.plan)
        state.set_book_tag(1, 'Source.Royal Road', False)
        self.assertFalse(state.is_tag_active(1, 'Source.Royal Road'))

        state.set_global_tag('Source.Royal Road', True)
        self.assertTrue(state.is_tag_active(1, 'Source.Royal Road'))
        self.assertTrue(state.is_tag_active(2, 'Source.Royal Road'))

    def test_active_plan_preserves_existing_tags(self):
        state = rules.TagSelectionState(self.plan)
        state.set_book_tag(1, 'LitRPG', False)

        active = state.get_active_plan()
        book1_plan = next(p for p in active if p['id'] == 1)
        self.assertEqual(book1_plan['tags'], ['Existing1', 'Source.Royal Road'])


class MockTreeItem:
    def __init__(self, text, check_state=2):
        self.text_val = text
        self.check_val = check_state
        self.children = []

    def checkState(self, col):
        return self.check_val

    def setCheckState(self, col, state):
        self.check_val = state

    def childCount(self):
        return len(self.children)

    def child(self, i):
        return self.children[i]

    def addChild(self, child):
        self.children.append(child)


class UiSyncTests(unittest.TestCase):
    def test_ui_tree_sync_simulation(self):
        # Simulates the tree synchronization logic in action.py
        books = [
            book(1, identifiers={'royalroad': '1'}),
            book(2, identifiers={'royalroad': '2'}),
        ]
        parsed_rules, _ = rules.parse_rules('title:Book => LitRPG')
        plan = rules.plan_changes(books, rules=parsed_rules)
        sel = rules.TagSelectionState(plan)

        # Build mock trees
        global_items = {}
        for tag, count in sel.get_distinct_tags():
            item = MockTreeItem(f'{tag} ({count})', check_state=2)
            item._tag_name = tag
            global_items[tag] = item

        book_nodes = []
        for p in plan:
            b_item = MockTreeItem(p['title'], check_state=2)
            b_item._book_id = p['id']
            for tag in p.get('add', []):
                t_item = MockTreeItem(tag, check_state=2)
                t_item._book_id = p['id']
                t_item._tag = tag
                b_item.addChild(t_item)
            book_nodes.append(b_item)

        # Simulate unchecking 'LitRPG' globally
        global_litrpg = global_items['LitRPG']
        global_litrpg.setCheckState(0, 0)  # Unchecked = 0
        sel.set_global_tag('LitRPG', False)

        for b_item in book_nodes:
            for j in range(b_item.childCount()):
                t_item = b_item.child(j)
                if getattr(t_item, '_tag', None) == 'LitRPG':
                    t_item.setCheckState(0, 0)

        # Verify all LitRPG child items in books tree became Unchecked
        for b_item in book_nodes:
            litrpg_children = [b_item.child(j) for j in range(b_item.childCount()) if getattr(b_item.child(j), '_tag', None) == 'LitRPG']
            for child in litrpg_children:
                self.assertEqual(child.checkState(0), 0)

        # Verify per-book override: check LitRPG back for Book 1
        book1_litrpg = next(b_item.child(j) for b_item in book_nodes if b_item._book_id == 1 for j in range(b_item.childCount()) if b_item.child(j)._tag == 'LitRPG')
        book1_litrpg.setCheckState(0, 2)
        sel.set_book_tag(1, 'LitRPG', True)

        # Global tag checkbox remains Unchecked
        self.assertEqual(global_litrpg.checkState(0), 0)
        # Selection state confirms Book 1 has LitRPG active, Book 2 does not
        self.assertTrue(sel.is_tag_active(1, 'LitRPG'))
        self.assertFalse(sel.is_tag_active(2, 'LitRPG'))


if __name__ == '__main__':
    unittest.main()


