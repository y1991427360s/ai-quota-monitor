"""Pure parsers. Only upstream values may create quota or model records."""
import datetime
import json
import math
import re
from pathlib import Path

CODEX_SOURCE = "https://chatgpt.com/backend-api/wham/usage"
GOOGLE_SUMMARY_SOURCE = "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
GOOGLE_MODELS_SOURCE = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def percentage(value, multiplier=1):
    result = number(value)
    result = result * multiplier if result is not None else None
    return result if result is not None and 0 <= result <= 100 else None


def data_status(remaining):
    return "unknown" if remaining is None else "exhausted" if remaining == 0 else "available"


def normalize_reset_time(raw_val, window_seconds=0):
    """No inferred reset: naive ISO timestamps are explicitly treated as UTC."""
    if raw_val is None or raw_val == "" or isinstance(raw_val, bool):
        return ""
    try:
        numeric = number(raw_val)
        if numeric is not None:
            if abs(numeric) > 1e11:
                numeric /= 1000
            dt = datetime.datetime.fromtimestamp(numeric, datetime.timezone.utc)
        else:
            dt = datetime.datetime.fromisoformat(str(raw_val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def window_kind(data, identifier=""):
    duration = number(data.get("limit_window_seconds", data.get("window_seconds", data.get("windowSeconds", data.get("durationSeconds")))))
    if duration == 18000:
        return "five_hour", duration
    if duration == 604800:
        return "weekly", duration
    # A different explicit duration is authoritative; never relabel a new period.
    if duration is not None:
        return "unknown", duration
    labels = " ".join(str(data.get(k, "")) for k in ("window", "windowType", "period", "bucketId", "displayName")) + " " + identifier
    labels = labels.lower().replace("_", "-")
    if re.search(r"(?:^|[^a-z0-9])(?:5h|5-hour|5-hours|five-hour|five hour)(?:$|[^a-z0-9])", labels):
        return "five_hour", None
    if re.search(r"(?:^|[^a-z0-9])(?:7d|7-day|weekly|week|seven-day)(?:$|[^a-z0-9])", labels):
        return "weekly", None
    return "unknown", None


def parse_single_codex_window(win_data, identifier="", fetched_at=None):
    if not isinstance(win_data, dict):
        return None
    used = percentage(win_data.get("used_percent"))
    remaining = None if used is None else 100 - used
    kind, seconds = window_kind(win_data, identifier)
    reset = normalize_reset_time(win_data.get("reset_at"))
    # Explicit upstream countdown is allowed, unlike guessing from window duration.
    after = number(win_data.get("reset_after_seconds"))
    fetched_at = fetched_at or utc_now()
    if not reset and after is not None and after >= 0:
        try:
            baseline = datetime.datetime.fromisoformat(fetched_at)
            reset = (baseline + datetime.timedelta(seconds=after)).isoformat()
        except (TypeError, ValueError, OverflowError):
            reset = ""
    return {"id": identifier, "kind": kind, "used_percent": used,
            "remaining_percent": remaining, "reset_at": reset,
            "window_seconds": seconds, "reset_after_seconds": after,
            "status": data_status(remaining), "source": CODEX_SOURCE,
            "fetched_at": fetched_at, "stale": False}


def codex_windows(container, fetched_at):
    result = {"five_hour": None, "weekly": None, "windows": [], "status": "unknown",
              "source": CODEX_SOURCE, "fetched_at": fetched_at, "stale": False}
    if not isinstance(container, dict):
        return result
    candidates = []
    for key, val in container.items():
        if isinstance(val, dict) and (key.endswith("window") or key in ("primary", "secondary", "five_hour", "weekly") or any(k in val for k in ("used_percent", "limit_window_seconds"))):
            candidates.append((key, val))
        elif key == "windows":
            if isinstance(val, list):
                candidates.extend((str(i), w) for i, w in enumerate(val) if isinstance(w, dict))
            elif isinstance(val, dict):
                candidates.extend((str(i), w) for i, w in val.items() if isinstance(w, dict))
    if not candidates and any(k in container for k in ("used_percent", "limit_window_seconds", "reset_at")):
        candidates = [("window", container)]
    for key, raw in candidates:
        win = parse_single_codex_window(raw, key, fetched_at)
        result["windows"].append(win)
        if win["kind"] != "unknown" and result[win["kind"]] is None:
            result[win["kind"]] = win
    result["status"] = aggregate_status(result["windows"])
    return result


def parse_codex_rate_limits(data):
    data = data if isinstance(data, dict) else {}
    fetched = utc_now()
    container = data.get("rate_limit") or data.get("rate_limits") or {}
    container = dict(container) if isinstance(container, dict) else {}
    for key in ("five_hour", "weekly"):
        if isinstance(data.get(key), dict):
            container[key] = data[key]
    rate_limits = codex_windows(container, fetched)
    additional = []
    raw_additional = data.get("additional_rate_limits") or []
    if isinstance(raw_additional, dict):
        raw_additional = [dict(v, id=k) for k, v in raw_additional.items() if isinstance(v, dict)]
    for index, item in enumerate(raw_additional):
        if not isinstance(item, dict):
            continue
        limits = codex_windows(item.get("rate_limit") or item.get("rate_limits") or item, fetched)
        model_id = item.get("model_id") or item.get("model")
        if not isinstance(model_id, str) or not model_id.strip():
            model_id = None
        additional.append(dict(limits, id=item.get("id") or item.get("limit_name") or model_id or f"pool-{index}",
                               name=item.get("name") or item.get("limit_name") or model_id or item.get("id") or "专属额度池",
                               model_id=model_id, limit_name=item.get("limit_name"), metered_feature=item.get("metered_feature"), source=CODEX_SOURCE, fetched_at=fetched, stale=False,
                               status=aggregate_status(limits["windows"])))
    credits = None
    if isinstance(data.get("credits"), dict):
        raw = data["credits"]
        balance = number(raw.get("balance"))
        unlimited = raw.get("unlimited") if isinstance(raw.get("unlimited"), bool) else None
        credits = {"has_credits": raw.get("has_credits") if isinstance(raw.get("has_credits"), bool) else None,
                   "unlimited": unlimited, "balance": balance,
                   "status": "available" if unlimited else "unavailable" if balance is None else data_status(balance),
                   "source": CODEX_SOURCE, "fetched_at": fetched, "stale": False}
    return rate_limits, additional, credits


def aggregate_status(records):
    statuses = [r.get("status") for r in records if isinstance(r, dict)]
    if "exhausted" in statuses:
        return "exhausted"
    return "available" if "available" in statuses else "unknown"


def model_metadata(key, val):
    lower = key.lower()
    provider = val.get("provider") or val.get("publisher")
    if not isinstance(provider, str):
        provider = "Anthropic" if lower.startswith("claude-") else "OpenAI" if lower.startswith(("gpt-", "o1-", "o3-", "o4-", "openai-", "codex-")) else "Google" if lower.startswith("gemini-") else "Unknown"
    tier_match = re.search(r"-(extra-low|high|medium|low|tiered)(?:-|$)", lower)
    thinking_match = re.search(r"-(thinking|agent)(?:-|$)", lower)
    version_match = re.search(r"(?:^|-)(\d+(?:\.\d+)*)(?:-|$)", lower)
    claude_version = re.search(r"^claude-(?:sonnet|opus|haiku)-(\d+)-(\d+)(?:-|$)", lower)
    family = val.get("family") or val.get("modelFamily")
    if not family:
        family = lower.split("-")[0]
        for candidate in ("flash-lite", "flash", "pro", "argon", "sonnet", "opus", "haiku", "oss"):
            if re.search(r"(?:^|-)" + candidate + r"(?:-|$)", lower):
                family += " " + candidate
                break
    return {"provider": provider, "family": family,
            "version": val.get("version") or (".".join(claude_version.groups()) if claude_version else version_match.group(1) if version_match else ""),
            "tier": val.get("tier") or (tier_match.group(1) if tier_match else ""),
            "thinkingLevel": val.get("thinkingLevel") or (thinking_match.group(1) if thinking_match else "")}


def google_window(raw, identifier="", source=GOOGLE_SUMMARY_SOURCE, fetched_at=None):
    kind, seconds = window_kind(raw, identifier)
    pct = percentage(raw.get("remainingFraction"), 100)
    return {"id": raw.get("bucketId") or identifier, "name": raw.get("displayName") or raw.get("bucketId") or identifier,
            "kind": kind, "window_seconds": seconds, "percentage": pct,
            "resetTime": normalize_reset_time(raw.get("resetTime")), "status": data_status(pct),
            "source": source, "fetched_at": fetched_at or utc_now(), "stale": False}


def format_model_entry(key, val, spec_tier="", spec_tag=""):
    val = val if isinstance(val, dict) else {}
    quota = val.get("quotaInfo") if isinstance(val.get("quotaInfo"), dict) else {}
    fetched = utc_now()
    pct = percentage(quota.get("remainingFraction"), 100)
    metadata = model_metadata(key, val)
    return dict(metadata, id=key, rawModelId=key, name=key,
                displayName=val.get("displayName") or key, tagLabel=metadata["tier"] or metadata["thinkingLevel"],
                remainingPercent=pct, resetTime=normalize_reset_time(quota.get("resetTime")),
                used=None if pct is None else pct < 100, isPrimary=True, status=data_status(pct),
                source=GOOGLE_MODELS_SOURCE, fetched_at=fetched, stale=False,
                windows=[google_window(quota, source=GOOGLE_MODELS_SOURCE, fetched_at=fetched)] if quota else [],
                quotaScope="model", quotaGroup=quota.get("quotaGroup") or val.get("quotaGroup"))


def extract_primary_models(models_data, fallback_groups=None):
    # Preferences affect ordering only; never create a model or discard unknown IDs.
    preferences = {}
    try:
        module_dir = Path(__file__).resolve().parent
        local_config = module_dir / "config" / "model_preferences.json"
        config_path = local_config if local_config.is_file() else module_dir.parent / "config" / "model_preferences.json"
        preferences = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    priority = preferences.get("recommended_model_ids", [])
    providers = preferences.get("provider_order", ["Google", "Anthropic", "OpenAI", "Unknown"])
    entries = [format_model_entry(k, v) for k, v in models_data.items()
               if isinstance(k, str) and k.strip() and isinstance(v, dict)] if isinstance(models_data, dict) else []
    aliases = preferences.get("display_names", {})
    if isinstance(aliases, dict):
        for entry in entries:
            alias = aliases.get(entry["id"])
            # An alias never creates a record or overrides an upstream display name.
            if entry["displayName"] == entry["id"] and isinstance(alias, str) and alias.strip():
                entry["displayName"] = alias
    entries.sort(key=lambda e: (priority.index(e["id"]) if e["id"] in priority else len(priority),
                                providers.index(e["provider"]) if e["provider"] in providers else len(providers), e["id"]))
    return entries


def parse_quota_summary(summary_data):
    if not isinstance(summary_data, dict):
        return None
    parsed = []
    fetched = utc_now()
    for index, group in enumerate(summary_data.get("groups") or []):
        if not isinstance(group, dict):
            continue
        windows = [google_window(b, str(i), fetched_at=fetched) for i, b in enumerate(group.get("buckets") or []) if isinstance(b, dict)]
        five = next((w for w in windows if w["kind"] == "five_hour"), None)
        week = next((w for w in windows if w["kind"] == "weekly"), None)
        legacy = five or week or (windows[0] if windows else {})
        parsed.append({"id": group.get("groupId") or str(index),
                       "groupName": group.get("displayName") or group.get("groupName") or group.get("groupId") or "未知额度组",
                       "subTitle": group.get("description") or "组额度，非单模型专属额度",
                       "percentage": legacy.get("percentage"), "resetTime": legacy.get("resetTime", ""),
                       "fiveHour": five, "weekly": week, "windows": windows,
                       "quotaScope": "group", "status": aggregate_status(windows),
                       "source": GOOGLE_SUMMARY_SOURCE, "fetched_at": fetched, "stale": False})
    # Some upstream revisions return ungrouped buckets. Keep them without guessed families.
    if isinstance(summary_data.get("buckets"), list) and summary_data["buckets"]:
        parsed.extend(parse_quota_summary({"groups": [{"groupName": "未分组额度", "buckets": summary_data["buckets"]}]}) or [])
    return parsed or None


def mark_stale(value):
    if isinstance(value, dict):
        result = {k: mark_stale(v) for k, v in value.items()}
        if any(k in value for k in ("status", "remainingPercent", "percentage", "remaining_percent")):
            result.update(stale=True, status="stale")
        return result
    if isinstance(value, list):
        return [mark_stale(v) for v in value]
    return value
