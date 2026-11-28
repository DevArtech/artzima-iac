# SPDX-License-Identifier: MIT
# Account-free source adapted from a4k-openproject/a4kSubtitles,
# services/opensubtitles_quick.py (MIT). See LICENSE.a4kSubtitles.
"""Bazarr adapter for a4kSubtitles' public OpenSubtitles mirror.

Normal tracks are never relabelled forced. Forced candidates must have an explicit
provider flag or a forced/foreign-only filename. Missing coverage stays missing.
"""
import re
from urllib.parse import urlparse

import requests
from guessit import guessit
from subliminal import Episode, Movie
from subliminal.exceptions import ServiceUnavailable
from subliminal_patch.exceptions import TooManyRequests
from subliminal_patch.providers import Provider
from subliminal_patch.subtitle import Subtitle, guess_matches
from subzero.language import Language

API = 'https://opensubtitles-v3.strem.io'
MAX_DOWNLOAD = 2 * 1024 * 1024
FORCED = re.compile(r'(?:^|[\s._\-\[(])(?:forced|foreign[ ._-]*(?:parts[ ._-]*only|only))(?=$|[\s._\-\])])', re.I)
HI = re.compile(r'(?:^|[\s._\-\[(])(?:sdh|hi)(?=$|[\s._\-\])])', re.I)


def safe_download_url(url):
    """Same HTTPS host family as a4k's quick source, with no credentials/ports."""
    try:
        p = urlparse(url)
        return (p.scheme == 'https' and bool(p.hostname)
                and p.hostname.endswith('.strem.io') and not p.username
                and not p.password and p.port in (None, 443))
    except (ValueError, TypeError):
        return False


def is_forced(row):
    if any(row.get(k) in (True, 1, '1', 'true') for k in
           ('forced', 'foreignPartsOnly', 'SubForeignPartsOnly')):
        return True
    return bool(FORCED.search(' '.join(str(row.get(k) or '') for k in
                                      ('subtitleFileName', 'movieReleaseName'))))


def lookup(video):
    if isinstance(video, Episode):
        imdb = getattr(video, 'series_imdb_id', None)
        if not imdb or not re.fullmatch(r'tt\d+', str(imdb)):
            return None
        if not isinstance(video.season, int) or not isinstance(video.episode, int):
            return None
        return str(imdb), 'series/%s:%d:%d' % (imdb, video.season, video.episode)
    imdb = getattr(video, 'imdb_id', None)
    if isinstance(video, Movie) and imdb and re.fullmatch(r'tt\d+', str(imdb)):
        return str(imdb), 'movie/' + str(imdb)
    return None


class A4kAccountlessSubtitle(Subtitle):
    provider_name = 'a4k_accountless'
    hash_verifiable = False

    def __init__(self, row, identity, video):
        forced = is_forced(row)
        name = row.get('subtitleFileName') or row.get('movieReleaseName') or ''
        impaired = bool(HI.search(name))
        language = Language.rebuild(Language('eng'), forced=forced, hi=impaired)
        super().__init__(language, hearing_impaired=impaired)
        self.forced = forced
        self.row = row
        self.release_info = name
        self.page_link = 'https://www.opensubtitles.com/'
        self.identity = identity
        self.episode = (video.season, video.episode) if isinstance(video, Episode) else None

    @property
    def id(self):
        return str(self.row.get('id') or self.row['url'])

    def get_matches(self, video):
        found = lookup(video)
        if not found or found[0] != self.identity:
            return set()
        episode = isinstance(video, Episode)
        if episode and self.episode != (video.season, video.episode):
            return set()
        matches = ({'series_imdb_id', 'series', 'season', 'episode', 'year'}
                   if episode else {'imdb_id', 'title', 'year'})
        matches |= guess_matches(video, guessit(self.release_info,
                                {'type': 'episode' if episode else 'movie'}))
        # The mirror did not verify this file's hash, even if a parser infers one.
        matches.discard('hash')
        return matches


class A4kAccountlessProvider(Provider):
    languages = {Language.rebuild(Language('eng'), forced=f, hi=h)
                 for f in (False, True) for h in (False, True)}
    video_types = (Episode, Movie)
    subtitle_class = A4kAccountlessSubtitle

    def initialize(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'a4kSubtitles-Bazarr/1.0',
                                     'Accept': 'application/json'})

    def terminate(self):
        if self.session:
            self.session.close()

    @staticmethod
    def check_status(response):
        if response.status_code == 429:
            raise TooManyRequests('Account-free subtitle source rate limit')
        if response.status_code >= 400:
            raise ServiceUnavailable('Account-free subtitle source HTTP %d' % response.status_code)

    def list_subtitles(self, video, languages):
        key = lookup(video)
        if key is None or not any(x.alpha3 == 'eng' for x in languages):
            return []
        try:
            response = self.session.get(API + '/subtitles/' + key[1] + '.json',
                                        timeout=(5, 12), allow_redirects=False)
            if response.status_code == 404:
                return []
            self.check_status(response)
            payload = response.json()
            rows = payload.get('subtitles')
            if not isinstance(rows, list):
                raise ServiceUnavailable('Malformed account-free subtitle response')
        except (requests.RequestException, ValueError) as exc:
            raise ServiceUnavailable('Account-free subtitle lookup failed') from exc
        wanted = {bool(x.forced) for x in languages if x.alpha3 == 'eng'}
        result = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or row.get('lang') not in ('eng', 'en'):
                continue
            if not safe_download_url(row.get('url', '')) or is_forced(row) not in wanted:
                continue
            name = row.get('subtitleFileName') or ''
            if name and not name.lower().endswith('.srt'):
                continue
            sub = self.subtitle_class(row, key[0], video)
            if sub.id not in seen:
                seen.add(sub.id)
                result.append(sub)
        return result

    def download_subtitle(self, subtitle):
        url = subtitle.row['url']
        for _ in range(4):
            if not safe_download_url(url):
                raise ServiceUnavailable('Rejected unexpected subtitle download host')
            try:
                response = self.session.get(url, timeout=(5, 20), stream=True,
                                            allow_redirects=False)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        url = response.headers.get('Location', '')
                        continue
                    self.check_status(response)
                    chunks = bytearray()
                    for chunk in response.iter_content(65536):
                        chunks.extend(chunk)
                        if len(chunks) > MAX_DOWNLOAD:
                            raise ServiceUnavailable('Subtitle download exceeds size limit')
                    content = bytes(chunks)
                finally:
                    response.close()
            except requests.RequestException as exc:
                raise ServiceUnavailable('Account-free subtitle download failed') from exc
            text = content.decode('utf-8-sig', errors='replace')
            if not re.search(r'\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}', text):
                raise ServiceUnavailable('Source did not return an SRT subtitle')
            if '<html' in text[:1024].lower() or '<!doctype html' in text[:1024].lower():
                raise ServiceUnavailable('Source returned an HTML page')
            subtitle.content = content
            return
        raise ServiceUnavailable('Too many subtitle download redirects')
