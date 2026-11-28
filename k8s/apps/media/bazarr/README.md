# Account-free Bazarr source

`a4k_accountless.py` adapts the public OpenSubtitles mirror used by
[a4kSubtitles](https://github.com/a4k-openproject/a4kSubtitles), specifically
`a4kSubtitles/services/opensubtitles_quick.py`. It does not install Kodi or use an
OpenSubtitles account. The upstream source is MIT; its licence is retained here.

Bazarr discovers this provider as `a4k_accountless`. The HelmRelease mounts it
read-only into Bazarr's provider directory. The ConfigMap comes from this folder.
When changing the provider, update `checksum/a4k-provider` in `bazarr.yaml` to the
SHA256 of this file so Bazarr restarts with the new code.

Enable it through Bazarr's settings API, preserving any existing providers:
`settings-general-enabled_providers=a4k_accountless`. The frontend's fixed provider
picker does not list custom providers; do not accidentally remove it when saving
that picker. Bazarr's normal scheduler handles searches and provider backoff.

The adapter supports English movie subtitles by IMDb identity and TV subtitles by
series IMDb identity plus season/episode. It claims no file-hash match. Scores
still depend on release matching. A result is forced only when its provider flag
or filename explicitly says so. Sparse subtitle files are not automatically
labelled forced. Normal/full tracks cannot satisfy forced-only searches. Some
unlabelled forced tracks therefore need separate verification; provider coverage
is not guaranteed.

HTTPS download hosts are restricted to the same `.strem.io` family used upstream.
Redirects, response sizes, content type/shape and subtitle syntax are checked.
Transport/rate-limit failures are reported to Bazarr instead of being reported as
successful empty searches. Audio/video never leaves the media server; searches
send only title identity and episode metadata to the public subtitle source.

Tests use real Bazarr dependencies. Inside the Bazarr image, put the provider and
`test_a4k_accountless.py` in the same directory and run the test file with Python.
The nine cases cover forced/full separation, identity/episode scoping, URL
restrictions, redirect escape, response validation, size limits and rate limits.
A live lookup and valid-SRT download were also checked without credentials.

Jellyfin should prefer English with Smart subtitle mode: use forced English over
English audio and full English over other audio. Existing verified forced tracks
must remain intact. AI transcription is not part of this provider.
