#!/usr/bin/env python3
"""Registry publication fails closed for public, missing or unverified packages."""
from __future__ import annotations
import contextlib
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from verify_private_package import verify


class PrivatePackageTests(unittest.TestCase):
    def response(self, visibility="private", owner="example-owner", name="example-image"):
        return json.dumps({"visibility": visibility, "owner": {"login": owner}, "name": name}).encode()

    def check(self, payload):
        with patch("urllib.request.urlopen", return_value=io.BytesIO(payload)), \
                contextlib.redirect_stdout(io.StringIO()):
            verify("example-owner", "example-image", "fixture-token")

    def test_verified_private_package_is_allowed(self):
        self.check(self.response())

    def test_public_package_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.check(self.response(visibility="public"))

    def test_wrong_owner_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.check(self.response(owner="other-owner"))

    def test_wrong_name_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.check(self.response(name="other-image"))

    def test_missing_package_is_rejected(self):
        failure = urllib.error.HTTPError("https://example.test", 404, "not found", {}, None)
        with patch("urllib.request.urlopen", side_effect=failure), self.assertRaises(SystemExit):
            verify("example-owner", "example-image", "fixture-token")

    def test_invalid_response_is_rejected(self):
        with self.assertRaises(SystemExit):
            self.check(b"[]")

    def test_empty_token_is_rejected(self):
        with self.assertRaises(SystemExit):
            verify("example-owner", "example-image", "")


if __name__ == "__main__":
    unittest.main()
