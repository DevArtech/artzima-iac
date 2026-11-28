import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path[:0] = ['/app/bin/custom_libs', '/app/bin/libs']
spec = importlib.util.spec_from_file_location('a4k_accountless', str(Path(__file__).with_name('a4k_accountless.py')))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def movie(imdb='tt1234567'):
    return p.Movie('Example.2020.1080p.BluRay.x264.mkv', 'Example', year=2020, imdb_id=imdb)


def row(name='Example.2020.1080p.BluRay.x264.srt', **kwargs):
    return dict(id='123', lang='eng', subtitleFileName=name,
                url='https://subs5.strem.io/en/download/test', **kwargs)


class Tests(unittest.TestCase):
    def test_only_explicit_forced_is_accepted(self):
        self.assertTrue(p.is_forced(row('Example.en.forced.srt')))
        self.assertTrue(p.is_forced(row('Example.foreign-only.srt')))
        self.assertTrue(p.is_forced(row(forced=True)))
        self.assertFalse(p.is_forced(row(forced='false', cues=16)))
        self.assertFalse(p.is_forced(row('Example.SDH.srt')))

    def test_download_hosts(self):
        self.assertTrue(p.safe_download_url('https://subs5.strem.io/a'))
        for url in ['http://subs5.strem.io/a', 'https://strem.io.evil/a',
                    'https://evil/a', 'https://u:p' + '@' + 'subs5.strem.io/a',
                    'https://subs5.strem.io:444/a', 'https://127.0.0.1/a']:
            self.assertFalse(p.safe_download_url(url), url)

    def test_movie_identity_not_filename_guess(self):
        self.assertEqual(p.lookup(movie())[1], 'movie/tt1234567')
        self.assertIsNone(p.lookup(movie('tt123/evil')))
        s = p.A4kAccountlessSubtitle(row(), 'tt1234567', movie())
        self.assertNotIn('hash', s.get_matches(movie()))
        self.assertEqual(s.get_matches(movie('tt7654321')), set())

    def test_episode_scoping(self):
        v = p.Episode('Example.S01E02.mkv', 'Example', 1, 2, series_imdb_id='tt1234567')
        self.assertEqual(p.lookup(v)[1], 'series/tt1234567:1:2')
        s = p.A4kAccountlessSubtitle(row(), 'tt1234567', v)
        v.episode = 3
        self.assertEqual(s.get_matches(v), set())

    def test_forced_query_never_receives_full_track(self):
        provider = p.A4kAccountlessProvider()
        response = Mock(status_code=200)
        response.json.return_value = {'subtitles': [row(), row('Example.forced.srt')]}
        provider.session = Mock()
        provider.session.get.return_value = response
        forced = p.Language.rebuild(p.Language('eng'), forced=True)
        subs = provider.list_subtitles(movie(), {forced})
        self.assertEqual(len(subs), 1)
        self.assertTrue(subs[0].forced)

    def test_redirect_cannot_escape_allowed_host(self):
        provider = p.A4kAccountlessProvider()
        response = Mock(status_code=302, headers={'Location': 'https://evil/a'})
        provider.session = Mock()
        provider.session.get.return_value = response
        with self.assertRaises(p.ServiceUnavailable):
            provider.download_subtitle(p.A4kAccountlessSubtitle(row(), 'tt1234567', movie()))
        self.assertEqual(provider.session.get.call_count, 1)

    def test_non_subtitle_rejected(self):
        provider = p.A4kAccountlessProvider()
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'<html>error</html>']
        provider.session = Mock()
        provider.session.get.return_value = response
        with self.assertRaises(p.ServiceUnavailable):
            provider.download_subtitle(p.A4kAccountlessSubtitle(row(), 'tt1234567', movie()))

    def test_size_limit(self):
        provider = p.A4kAccountlessProvider()
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'x' * (p.MAX_DOWNLOAD + 1)]
        provider.session = Mock()
        provider.session.get.return_value = response
        with self.assertRaises(p.ServiceUnavailable):
            provider.download_subtitle(p.A4kAccountlessSubtitle(row(), 'tt1234567', movie()))

    def test_rate_limit_is_not_empty_search(self):
        with self.assertRaises(p.TooManyRequests):
            p.A4kAccountlessProvider.check_status(Mock(status_code=429))


if __name__ == '__main__':
    unittest.main()
