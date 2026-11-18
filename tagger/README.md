# Story Collection Tagger

Install: download the ZIP from the [main page](../README.md), then in Calibre choose **Preferences → Plugins → Load plugin from file**, pick it, restart Calibre, and add its button under **Preferences → Toolbars & menus** if it is not visible. Calibre 6 or newer.

Built with `python build_plugins.py` from the repository root (see *Building and testing* on the [main page](../README.md)).

Adds tags so books from different sites can be organized together. Run **Auto-tag stories** on the selected books or the whole library; **Preview** shows exactly which books would get which tags, and nothing changes until **Apply**. It only adds tags, never removes or renames existing ones, and running it again adds nothing new.

- **Source tag**: each book is matched to a site by an identifier name (`metabods`, `nifty`, `royalroad`, `ao3`, ...), or by the site's address appearing in any identifier value or the publisher. Built in: Metabods, Nifty, and every site FanFicFare has an adapter for (110 in total, including Royal Road, Archive of Our Own, FanFiction.Net, Wattpad, Scribble Hub, Literotica, SpaceBattles, Sufficient Velocity), plus Webnovel, Wuxiaworld, Fur Affinity and Tapas. FanFicFare stores a story's address in the `url` identifier (Calibre writes `:` as `|`) and the site's domain as the publisher; both are recognised, so books added by FanFicFare's own Calibre plugin or by the Fanfic Site Downloader are tagged correctly. Add more as `host => Name` lines. A matching book gets, for example, `Source.Royal Road`; the prefix is editable. To see them as a nested tree, enable hierarchical display for tags in Calibre's *Look & feel → Tag browser* settings. Descriptions are only searched for addresses if you tick the option, since they can over-match.
- **Keyword rules**: lines of `[field:]pattern => Tag, Tag` map what is already on a book to your own cross-site tags. Fields: `tags`, `title`, `author`, `series`, `comments`, `publisher`, `source`, `any`; the default looks at title, tags and series. A plain pattern is a case-insensitive substring, `=text` must match exactly, and `re:...` is a regular expression. For example `tags:=Muscle Growth => Theme.Growth` gives Metabods and other sites' books a shared tag, and `source:Royal Road => Format.Web serial` tags by site.
- **Series tag**: optionally tag every book in a series (`Series.<name>`).
- Books from other tools are tagged if their identifiers or publisher carry the site's address; use the extra-sites box if one is missed.

## Metadata source

This plugin also ships as a Calibre metadata source in a separate ZIP, `StoryCollectionTaggerSource.zip` (Calibre allows only one plugin per ZIP). The source **Story Source Tagger** contributes tags only: for the book being identified it works out the `Source.<Site>` tags and keyword-rule tags the tagger would add (using the tagger's saved rules, sites and prefixes) and returns the book with those tags. Install it alongside the tagger and enable it in Calibre's Download metadata source list.
