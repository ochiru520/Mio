from __future__ import annotations

import unittest

from app.secret_store import protect_secret, unprotect_secret


class SecretStoreTests(unittest.TestCase):
    def test_dpapi_round_trip_does_not_embed_plaintext(self) -> None:
        secret = "sk-local-test-secret"
        protected = protect_secret(secret)

        self.assertTrue(protected.startswith("dpapi:"))
        self.assertNotIn(secret, protected)
        self.assertEqual(unprotect_secret(protected), secret)


if __name__ == "__main__":
    unittest.main()
