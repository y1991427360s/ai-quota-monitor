import contextlib
import io
import unittest
from unittest.mock import patch
from daemon import codex_monitor as monitor


class MonitorTests(unittest.TestCase):
    def test_unknown_limit_never_claims_window_exhaustion(self):
        text = monitor.alert_description({"name": '<img src=x onerror="alert(1)">'})
        self.assertNotIn("<img", text)
        self.assertIn("&lt;img", text)
        self.assertIn("无法确认", text)
        self.assertNotIn("5H", text)
        self.assertNotIn("429", text)

    def test_failed_push_is_retried_and_new_incident_alerts(self):
        seen = set()
        with patch.object(monitor, "check_postgres_anomalies", return_value=[{"id": 7}]), patch.object(monitor, "push_wechat_card", side_effect=[False, True]) as push:
            monitor.check_once(seen)
            self.assertEqual(seen, set())
            monitor.check_once(seen)
            monitor.check_once(seen)
            self.assertEqual(push.call_count, 2)
        with patch.object(monitor, "check_postgres_anomalies", return_value=[]):
            monitor.check_once(seen)
        self.assertEqual(seen, set())

    def test_secret_bearing_exception_is_never_logged(self):
        stream = io.StringIO()
        with patch.object(monitor.urllib.request, "urlopen", side_effect=ValueError("https://example?access_token=SECRET")), contextlib.redirect_stderr(stream):
            monitor._token_cache.update(token="", expires_at=0)
            self.assertEqual(monitor.get_access_token(), "")
        self.assertNotIn("SECRET", stream.getvalue())
        self.assertIn("ValueError", stream.getvalue())

    def test_database_does_not_fetch_error_body(self):
        with patch.object(monitor.subprocess, "check_output", return_value=b"null") as run:
            self.assertIsNone(monitor.check_postgres_anomalies())
        self.assertNotIn(b"error_message", run.call_args.kwargs["input"])


if __name__ == "__main__":
    unittest.main()
