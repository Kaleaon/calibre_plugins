"""Builders for sample Reddit Atom feeds, shared by the Reddit tests."""
import html


def md(body):
    return ('&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;' + html.escape(body) + '&lt;/div&gt;&lt;!-- SC_ON --&gt; &amp;#32; submitted by &amp;#32; '
            '&lt;a href="https://www.reddit.com/user/someone"&gt; /u/someone &lt;/a&gt; &lt;br/&gt; &lt;span&gt;&lt;a href="x"&gt;[link]&lt;/a&gt;&lt;/span&gt; '
            '&amp;#32; &lt;span&gt;&lt;a href="y"&gt;[comments]&lt;/a&gt;&lt;/span&gt;')


def entry(pid, title, body, author='/u/someone', stamp='2026-10-08T10:00:00+00:00', kind='t3'):
    content = f'<content type="html">{md(body)}</content>' if body is not None else (
        '<content type="html">&lt;table&gt;&lt;tr&gt;&lt;td&gt;&lt;a href="z"&gt;&lt;img src="t.jpg"/&gt;&lt;/a&gt;&lt;/td&gt;&lt;td&gt; &amp;#32; submitted by &amp;#32; '
        '&lt;a href="u"&gt; /u/someone &lt;/a&gt;&lt;/td&gt;&lt;/tr&gt;&lt;/table&gt;</content>')
    return (f'<entry><author><name>{author}</name><uri>https://www.reddit.com/user/x</uri></author><category term="HFY" label="r/HFY"/>{content}'
            f'<id>{kind}_{pid}</id><link href="https://www.reddit.com/r/HFY/comments/{pid}/slug/"/><updated>{stamp}</updated>'
            f'<published>{stamp}</published><title>{title}</title></entry>')


def feed(*entries):
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom"><category term="HFY" label="r/HFY"/>'
            '<id>/r/HFY/new.rss</id><title>HFY</title>' + ''.join(entries) + '</feed>')
