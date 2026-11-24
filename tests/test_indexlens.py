#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for IndexLens (no network — all pure logic)."""
import unittest

from indexlens import (
    PageResult,
    RobotsRule,
    extract_canonical,
    extract_noindex,
    fix_hint,
    make_verdict,
    parse_robots,
    parse_sitemap,
    path_allowed,
    priority_rank,
    render_json,
    render_markdown,
)


class TestNoindex(unittest.TestCase):
    def test_meta_noindex(self):
        html = '<html><head><meta name="robots" content="noindex, follow"></head></html>'
        self.assertTrue(extract_noindex(html))

    def test_meta_index_follow(self):
        html = '<meta name="robots" content="index, follow">'
        self.assertFalse(extract_noindex(html))

    def test_header_noindex(self):
        self.assertTrue(extract_noindex("<html></html>", "noindex"))
        self.assertTrue(extract_noindex("", "NOINDEX, NOFOLLOW"))

    def test_clean_page(self):
        self.assertFalse(extract_noindex("<html><body>hi</body></html>", ""))

    def test_malformed_html_no_crash(self):
        self.assertFalse(extract_noindex("<<<not html", ""))


class TestCanonical(unittest.TestCase):
    def test_absolute(self):
        html = '<link rel="canonical" href="https://x.com/a">'
        self.assertEqual(extract_canonical(html, "https://x.com/b"), "https://x.com/a")

    def test_relative_resolved(self):
        html = '<link rel="canonical" href="/a">'
        self.assertEqual(extract_canonical(html, "https://x.com/b"), "https://x.com/a")

    def test_missing(self):
        self.assertIsNone(extract_canonical("<html></html>", "https://x.com"))


class TestRobots(unittest.TestCase):
    def test_disallow_all(self):
        rules = parse_robots("User-agent: Googlebot\nDisallow: /")
        self.assertFalse(path_allowed("/page", rules))

    def test_specific_group_beats_star(self):
        text = ("User-agent: *\nDisallow: /\n\n"
                "User-agent: Googlebot\nDisallow:")
        rules = parse_robots(text, agent="Googlebot")
        self.assertTrue(path_allowed("/page", rules))

    def test_allow_longer_pattern_wins(self):
        rules = parse_robots("User-agent: *\nDisallow: /private\nAllow: /private/public")
        self.assertTrue(path_allowed("/private/public", rules))
        self.assertFalse(path_allowed("/private/secret", rules))

    def test_wildcard_pattern(self):
        rules = parse_robots("User-agent: *\nDisallow: /*.png$")
        self.assertFalse(path_allowed("/img/a.png", rules))
        self.assertTrue(path_allowed("/img/a.jpg", rules))

    def test_empty_disallow_allows_all(self):
        rules = parse_robots("User-agent: *\nDisallow:")
        self.assertTrue(path_allowed("/anything", rules))

    def test_no_robots_file(self):
        self.assertTrue(path_allowed("/x", []))


class TestVerdict(unittest.TestCase):
    def test_indexed(self):
        self.assertEqual(make_verdict(200, False, False, "present"), "INDEXED")

    def test_noindex_priority_over_robots(self):
        self.assertEqual(make_verdict(200, True, True, "absent"), "BLOCKED:NOINDEX")

    def test_robots_block(self):
        self.assertEqual(make_verdict(200, False, True, "absent"), "BLOCKED:ROBOTS")

    def test_not_indexed(self):
        self.assertEqual(make_verdict(200, False, False, "absent"), "NOT_INDEXED")

    def test_404(self):
        self.assertEqual(make_verdict(404, False, False, "unknown"), "BLOCKED:HTTP_404")

    def test_unreachable(self):
        self.assertEqual(make_verdict(None, False, False, "unknown"), "ERROR:UNREACHABLE")

    def test_priority_ordering(self):
        self.assertLess(priority_rank("BLOCKED:NOINDEX"), priority_rank("INDEXED"))
        self.assertLess(priority_rank("ERROR:UNREACHABLE"), priority_rank("BLOCKED:NOINDEX"))

    def test_fix_hint(self):
        self.assertIn("noindex", fix_hint("BLOCKED:NOINDEX").lower())


class TestSitemap(unittest.TestCase):
    def test_parse_urlset(self):
        xml = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
               "<url><loc>https://a.com/1</loc></url>"
               "<url><loc>https://a.com/2</loc></url></urlset>")
        self.assertEqual(parse_sitemap(xml), ["https://a.com/1", "https://a.com/2"])

    def test_parse_index_fallback_regex(self):
        bad = "<urlset><url><loc>https://a.com/x</loc></url>"  # unclosed
        self.assertEqual(parse_sitemap(bad), ["https://a.com/x"])


class TestReporting(unittest.TestCase):
    def _sample(self):
        return [
            PageResult(url="https://a.com/good", status=200, verdict="INDEXED",
                       search="present"),
            PageResult(url="https://a.com/bad", status=200, noindex=True,
                       verdict="BLOCKED:NOINDEX", search="absent"),
        ]

    def test_markdown_contains_fix_list(self):
        md = render_markdown(self._sample(), "https://a.com/sitemap.xml")
        self.assertIn("Priority Fix List", md)
        self.assertIn("BLOCKED:NOINDEX", md)
        self.assertIn("© 2026 Baran Avisat", md)

    def test_json_serializable(self):
        import json
        data = json.loads(render_json(self._sample()))
        self.assertEqual(len(data["results"]), 2)
        self.assertIn("fix", data["results"][0])


class TestRobotsRule(unittest.TestCase):
    def test_rule_regex(self):
        r = RobotsRule(allow=False, pattern="/admin/*")
        self.assertTrue(r.matches("/admin/panel"))
        self.assertFalse(r.matches("/public"))


if __name__ == "__main__":
    unittest.main()
