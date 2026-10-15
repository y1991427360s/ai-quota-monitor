"""Offline, synthetic fixtures only. No account credentials or production calls."""
import io
import json
import time
import urllib.error
import urllib.parse
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from backend import server
from backend import quota_parsing as qp


@pytest.fixture(autouse=True)
def isolated_files(tmp_path, monkeypatch):
    for key in ("TOKENS_FILE", "CACHE_FILE", "CHATGPT_TOKENS_FILE", "CHATGPT_CACHE_FILE"):
        monkeypatch.setattr(server, key, str(tmp_path / key))
    monkeypatch.setattr(server, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(server, "CHATGPT_CONFIG_DIR", str(tmp_path))
    server.PKCE_CACHE.clear()
    server.GOOGLE_PKCE_CACHE.clear()


def test_codex_two_windows_and_float_values():
    limits, extra, credits = qp.parse_codex_rate_limits({"rate_limit": {
        "primary_window": {"used_percent": 12.25, "limit_window_seconds": 18000, "reset_at": 1790000000},
        "secondary_window": {"used_percent": 43.5, "limit_window_seconds": 604800}}})
    assert limits["five_hour"]["remaining_percent"] == 87.75
    assert limits["weekly"]["remaining_percent"] == 56.5
    assert limits["weekly"]["reset_at"] == ""
    assert extra == [] and credits is None


@pytest.mark.parametrize("value,remaining,status", [(0,100,"available"),(100,0,"exhausted"),(None,None,"unknown"),("bad",None,"unknown"),(float("nan"),None,"unknown"),(True,None,"unknown"),(-1,None,"unknown"),(101,None,"unknown")])
def test_codex_missing_invalid_zero_full(value, remaining, status):
    result = qp.parse_single_codex_window({"used_percent": value})
    assert result["remaining_percent"] == remaining
    assert result["status"] == status


def test_unknown_windows_preserved_and_no_duration_guess():
    limits, _, _ = qp.parse_codex_rate_limits({"rate_limit": {
        "primary_window": {"used_percent": 1},
        "new_window": {"used_percent": 2, "limit_window_seconds": 3600}}})
    assert limits["five_hour"] is None and limits["weekly"] is None
    assert [w["window_seconds"] for w in limits["windows"]] == [None,3600]
    assert all(w["kind"] == "unknown" for w in limits["windows"])


def test_additional_new_model_and_feature_pools():
    data = {"additional_rate_limits": [
        {"model_id": "gpt-99-new", "limit_name": "Novel model", "rate_limit": {"primary_window": {"used_percent": 10, "limit_window_seconds": 18000}}},
        {"limit_name": "research", "metered_feature": "research", "rate_limit": {"new_window": {"used_percent": 20,"limit_window_seconds": 1234}}}]}
    _, extra, _ = qp.parse_codex_rate_limits(data)
    assert extra[0]["five_hour"]["remaining_percent"] == 90
    assert extra[1]["metered_feature"] == "research"
    assert extra[1]["windows"][0]["kind"] == "unknown"
    result = server.codex_result(data, {"email": "synthetic@example.invalid"})
    assert [m["id"] for m in result["models"]] == ["gpt-99-new"]
    assert server.codex_result({}, {})["models"] == []


@pytest.mark.parametrize("raw,status,balance", [({"balance":"12.75"},"available",12.75),({"balance":0},"exhausted",0),({"unlimited":True},"available",None),({},"unavailable",None),({"balance":"invalid"},"unavailable",None)])
def test_credits_states(raw,status,balance):
    credits = qp.parse_codex_rate_limits({"credits":raw})[2]
    assert credits["status"] == status and credits["balance"] == balance


@pytest.mark.parametrize("raw,expected", [(1790000000,"2026-09-21T14:13:20+00:00"),(1790000000000,"2026-09-21T14:13:20+00:00"),("2026-10-15T20:00:00+08:00","2026-10-15T12:00:00+00:00"),("2026-10-15T12:00:00Z","2026-10-15T12:00:00+00:00"),("2026-10-15T12:00:00","2026-10-15T12:00:00+00:00"),(None,""),("invalid","")])
def test_time_normalization(raw, expected):
    assert qp.normalize_reset_time(raw, 18000) == expected


def test_explicit_countdown_only():
    window = qp.parse_single_codex_window({"reset_after_seconds":0,"limit_window_seconds":18000}, fetched_at="2026-10-15T12:00:00+00:00")
    assert window["reset_at"] == "2026-10-15T12:00:00+00:00"
    assert qp.parse_single_codex_window({"limit_window_seconds":18000})["reset_at"] == ""


def test_google_all_real_models_and_metadata():
    ids = ["gemini-4-argon-new-tier", "gemini-3.8-flash-tiered", "gemini-3.6-flash-low", "gemini-3.5-flash-lite", "claude-opus-5-6-thinking", "gpt-6.1-sol", "new-provider-v9"]
    entries = qp.extract_primary_models({key:{"displayName":key} for key in ids})
    assert {e["id"] for e in entries} == set(ids)
    assert all(e["remainingPercent"] is None for e in entries)
    assert next(e for e in entries if e["id"] == "claude-opus-5-6-thinking")["version"] == "5.6"
    assert next(e for e in entries if e["id"] == "gemini-3.5-flash-lite")["family"] == "gemini flash-lite"
    assert qp.extract_primary_models({}, [{"groupName":"Gemini", "percentage":70}]) == []


def test_google_no_prefix_model_substitution():
    result = qp.extract_primary_models({"gemini-3.6-flash-extra-low":{},"gemini-3.6-flash-unknown":{}})
    assert len(result) == 2
    assert {x["tier"] for x in result} == {"extra-low",""}
    assert qp.format_model_entry("gemini-invalid", {"quotaInfo":{"remainingFraction":4}})["remainingPercent"] is None


def test_google_buckets_order_duplicates_unknown_and_null():
    groups = qp.parse_quota_summary({"groups":[{"displayName":"Actual group","buckets":[
        {"bucketId":"novel", "remainingFraction":0.2,"windowSeconds":1234},
        {"bucketId":"weekly-a","remainingFraction":0},
        {"bucketId":"weekly-b","remainingFraction":0.5},
        {"bucketId":"5h","remainingFraction":None}]}]})
    group = groups[0]
    assert len(group["windows"]) == 4
    assert group["windows"][0]["kind"] == "unknown"
    assert group["fiveHour"]["percentage"] is None
    assert group["weekly"]["percentage"] == 0
    assert len([w for w in group["windows"] if w["kind"] == "weekly"]) == 2
    assert group["quotaScope"] == "group"


def test_google_models_only_does_not_fabricate_weekly_or_groups(monkeypatch):
    monkeypatch.setattr(server,"fetch_quota_summary",lambda *a:None)
    models = qp.extract_primary_models({"gemini-4-actual":{"quotaInfo":{"remainingFraction":0.7}}})
    monkeypatch.setattr(server,"fetch_primary_models_probe",lambda *a:models)
    result = server.google_quota_snapshot("sample", "synthetic")
    assert result["groups"] == []
    assert result["models"][0]["remainingPercent"] == 70
    assert result["models"][0]["windows"][0]["kind"] == "unknown"
    assert result["degradation"]["status"] == "unknown"


def test_google_groups_only_does_not_fabricate_models(monkeypatch):
    groups = qp.parse_quota_summary({"groups":[{"groupName":"Gemini","buckets":[{"window":"5h","remainingFraction":0.4}]}]})
    monkeypatch.setattr(server,"fetch_quota_summary",lambda *a:groups)
    monkeypatch.setattr(server,"fetch_primary_models_probe",lambda *a:None)
    result = server.google_quota_snapshot("sample", "synthetic")
    assert result["models"] == [] and result["groups"][0]["percentage"] == 40


def test_google_source_priority_separates_model_and_group(monkeypatch):
    groups = qp.parse_quota_summary({"groups":[{"groupName":"Gemini","buckets":[{"window":"5h","remainingFraction":0.4}]}]})
    models = qp.extract_primary_models({"gemini-real":{"quotaInfo":{"remainingFraction":0.7}}})
    monkeypatch.setattr(server,"fetch_quota_summary",lambda *a:groups)
    monkeypatch.setattr(server,"fetch_primary_models_probe",lambda *a:models)
    result = server.google_quota_snapshot("sample", "synthetic")
    assert result["groups"][0]["percentage"] == 40
    assert result["models"][0]["remainingPercent"] == 70
    assert result["groups"][0]["source"] != result["models"][0]["source"]


def test_google_failure_stale_and_legacy_rejection(monkeypatch):
    monkeypatch.setattr(server,"fetch_quota_summary",lambda *a:None)
    monkeypatch.setattr(server,"fetch_primary_models_probe",lambda *a:None)
    assert server.google_quota_snapshot("sample","synthetic")["status"] == "unavailable"
    server.save_quota_cache({"sample":{"schema_version":2,"timestamp":time.time()-1000,"data":{"status":"available","groups":[{"status":"available","percentage":50}],"models":[],"fetched_at":"2026-10-01T00:00:00+00:00"}}})
    result = server.google_quota_snapshot("sample","synthetic")
    assert result["status"] == result["groups"][0]["status"] == "stale"
    assert result["fetched_at"] == "2026-10-01T00:00:00+00:00"
    server.save_quota_cache({"sample":{"timestamp":time.time(),"models":[{"id":"invented"}]}})
    assert server.google_quota_snapshot("sample","synthetic")["models"] == []


@pytest.mark.parametrize("code",[401,403,429,500])
def test_codex_http_errors_do_not_imply_exhaustion(code,monkeypatch):
    def fail(*args,**kwargs):
        raise urllib.error.HTTPError(qp.CODEX_SOURCE,code,"synthetic",{},io.BytesIO(b"credential-shaped-response-must-not-leak"))
    monkeypatch.setattr(server.urllib.request,"urlopen",fail)
    result = server.probe_chatgpt_quota({"email":"sample","accessToken":"synthetic","expiresAt":time.time()+9999})
    assert result["status"] == "unavailable"
    assert result["five_hour_remaining"] is None
    assert result["rate_limits"]["windows"] == []
    assert "credential-shaped" not in json.dumps(result)


def test_codex_expired_cache_stale_and_timeout(monkeypatch):
    data = server.codex_result({"rate_limit":{"primary_window":{"used_percent":25,"limit_window_seconds":18000}}},{"email":"sample"})
    server.save_chatgpt_cache({"sample":{"schema_version":2,"timestamp":time.time()-1000,"data":data}})
    def fail(*args,**kwargs):
        raise TimeoutError("synthetic-secret")
    monkeypatch.setattr(server.urllib.request,"urlopen",fail)
    result = server.probe_chatgpt_quota({"email":"sample","accessToken":"synthetic","expiresAt":time.time()+9999})
    assert result["stale"] and result["status"] == "stale"
    assert result["rate_limits"]["five_hour"]["stale"]
    assert result["five_hour_remaining"] == 75
    assert result["fetched_at"] == data["fetched_at"]
    assert "synthetic-secret" not in json.dumps(result)


def test_oauth_state_wrong_missing_expired_never_uses_other_verifier(monkeypatch):
    client = TestClient(server.app)
    state = client.get("/api/chatgpt/login-url").json()["state"]
    with patch.object(server.urllib.request,"urlopen") as request:
        for callback in ("http://localhost:1455/auth/callback?code=synthetic", "http://localhost:1455/auth/callback?code=synthetic&state=wrong"):
            assert client.post("/api/chatgpt/complete-oauth",json={"url":callback,"state":state}).status_code == 400
        assert state in server.PKCE_CACHE
        server.PKCE_CACHE[state] = ("synthetic",time.time()-1900)
        assert client.post("/api/chatgpt/complete-oauth",json={"url":f"http://localhost:1455/auth/callback?code=synthetic&state={state}"}).status_code == 400
        request.assert_not_called()


def test_oauth_google_namespace_isolation():
    client = TestClient(server.app)
    openai_state = client.get("/api/chatgpt/login-url").json()["state"]
    google_url = client.get("/api/login-url").json()["login_url"]
    google_state = urllib.parse.parse_qs(urllib.parse.urlparse(google_url).query)["state"][0]
    assert openai_state != google_state
    with patch.object(server.urllib.request,"urlopen") as request:
        response = client.post("/api/complete-login",json={"url":f"http://127.0.0.1:8085/callback?code=synthetic&state={openai_state}"})
        assert response.status_code == 400
        request.assert_not_called()
    assert openai_state in server.PKCE_CACHE and google_state in server.GOOGLE_PKCE_CACHE


def test_account_management_compatibility_and_health():
    client = TestClient(server.app)
    server.save_chatgpt_store({"activeEmail":"a@example.invalid","accounts":{"a@example.invalid":{"email":"a@example.invalid"},"b@example.invalid":{"email":"b@example.invalid"}}})
    assert client.get("/api/chatgpt/accounts").json()["has_accounts"]
    assert client.post("/api/chatgpt/accounts/switch",json={"email":"b@example.invalid"}).json()["success"]
    assert client.post("/api/chatgpt/accounts/switch",json={"email":"absent"}).status_code == 404
    assert client.delete("/api/chatgpt/accounts/b@example.invalid").json()["success"]
    assert client.get("/api/chatgpt/accounts").json()["active_email"] == "a@example.invalid"
    assert client.get("/api/health").json() == {"status":"ok","schema_version":2}


def test_google_refresh_failure_preserves_verified_stale_cache(monkeypatch):
    server.save_quota_cache({"sample":{"schema_version":2,"timestamp":time.time()-1000,"data":{
        "status":"available","fetched_at":"2026-10-01T00:00:00+00:00","groups":[],
        "models":qp.extract_primary_models({"gemini-real":{"quotaInfo":{"remainingFraction":0.8}}})}}})
    with open(server.TOKENS_FILE,"w",encoding="utf-8") as f:
        json.dump({"activeEmail":"sample","accounts":{"sample":{"email":"sample"}}},f)
    def fail(*args):
        raise RuntimeError("do-not-expose-token")
    monkeypatch.setattr(server,"refresh_account_token",fail)
    result = server.get_quota(refresh=True)[0]
    assert result["status"] == "stale" and result["models"][0]["stale"]
    assert result["models"][0]["remainingPercent"] == 80
    assert result["fetched_at"] == "2026-10-01T00:00:00+00:00"
    assert "do-not-expose-token" not in json.dumps(result)


def test_token_refresh_retains_account_identity_and_rotation(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps({"access_token":"synthetic-new","refresh_token":"synthetic-rotated","expires_in":3600}).encode()
    monkeypatch.setattr(server.urllib.request,"urlopen",lambda *args,**kwargs:Response())
    account = {"email":"sample","refreshToken":"synthetic-old","expiresAt":0}
    assert server.refresh_chatgpt_account_token(account) == "synthetic-new"
    assert account["refreshToken"] == "synthetic-rotated"
    assert account["email"] == "sample" and account["expiresAt"] > time.time()


def test_token_refresh_without_rotation_keeps_existing_refresh_token(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def read(self): return json.dumps({"access_token":"synthetic-new","expires_in":3600}).encode()
    monkeypatch.setattr(server.urllib.request,"urlopen",lambda *args,**kwargs:Response())
    account = {"email":"sample","refreshToken":"synthetic-old","expiresAt":0}
    assert server.refresh_chatgpt_account_token(account) == "synthetic-new"
    assert account["refreshToken"] == "synthetic-old"


def test_recommendations_order_only_actual_records():
    result = qp.extract_primary_models({"unknown-route-99":{},"gemini-3.1-pro-high":{},"gemini-3.8-flash-tiered":{}})
    assert [m["id"] for m in result] == ["gemini-3.8-flash-tiered","gemini-3.1-pro-high","unknown-route-99"]
    assert len(result) == 3


def test_absurd_reset_countdown_preserves_real_percentage():
    result = qp.parse_single_codex_window({"reset_after_seconds": 1e300,
        "used_percent": 10, "limit_window_seconds": 18000})
    assert result["remaining_percent"] == 90
    assert result["reset_at"] == ""


def test_name_aliases_do_not_create_models_or_override_upstream():
    result = qp.extract_primary_models({"gpt-6.1-sol": {},
        "gpt-6-astra": {"displayName": "Actual upstream name"}, "gpt-future-99": {}})
    names = {entry["id"]: entry["displayName"] for entry in result}
    assert names == {"gpt-6.1-sol": "GPT-6.1 Sol", "gpt-6-astra": "Actual upstream name",
                     "gpt-future-99": "gpt-future-99"}
