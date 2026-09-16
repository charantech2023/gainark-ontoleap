"""
A service with no API key does not start, and open mode has to be asked for by name.

TODO.md D2. The deployed service ran open for weeks behind nothing but a startup
warning, so the warning is now a refusal.

Run:  python test_fail_closed.py
"""

import os
import unittest

from fastapi.testclient import TestClient

AUTH_VARS = ("ONTOLEAP_API_KEY", "ONTOLEAP_ALLOW_UNAUTHENTICATED")


class FailClosedTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.get(k) for k in AUTH_VARS}
        for k in AUTH_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _app():
        import api
        return api.app

    def test_no_key_refuses_to_start(self):
        """The container fails its startup, so a bad revision never takes traffic."""
        with self.assertRaises(RuntimeError) as caught:
            with TestClient(self._app()):
                pass
        self.assertIn("ONTOLEAP_API_KEY", str(caught.exception))

    def test_a_key_starts(self):
        os.environ["ONTOLEAP_API_KEY"] = "a-configured-key"
        with TestClient(self._app()) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertEqual(client.get("/api/verticals").status_code, 401)
            self.assertNotEqual(
                client.get("/api/verticals", headers={"x-api-key": "a-configured-key"}).status_code, 401)

    def test_open_mode_is_asked_for_by_name(self):
        """A dev box may still run open - deliberately, and never by omission."""
        os.environ["ONTOLEAP_ALLOW_UNAUTHENTICATED"] = "1"
        with TestClient(self._app()) as client:
            self.assertEqual(client.get("/api/health").status_code, 200)
            self.assertNotEqual(client.get("/api/verticals").status_code, 401)

    def test_a_value_that_is_not_a_yes_is_not_consent(self):
        os.environ["ONTOLEAP_ALLOW_UNAUTHENTICATED"] = "0"
        with self.assertRaises(RuntimeError):
            with TestClient(self._app()):
                pass


if __name__ == "__main__":
    unittest.main()
