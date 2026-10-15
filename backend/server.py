import os
import json
import urllib.parse
import urllib.request
import time
import datetime
import subprocess
import base64
import secrets
import hashlib
import copy
import threading
try:
    from . import quota_parsing as parsing
except ImportError:
    import quota_parsing as parsing

CODEX_SOURCE = parsing.CODEX_SOURCE
GOOGLE_SUMMARY_SOURCE = parsing.GOOGLE_SUMMARY_SOURCE
GOOGLE_MODELS_SOURCE = parsing.GOOGLE_MODELS_SOURCE
utc_now = parsing.utc_now
aggregate_status = parsing.aggregate_status
data_status = parsing.data_status
mark_stale = parsing.mark_stale
model_metadata = parsing.model_metadata
# Public parser aliases preserve imports used by existing tools and integrations.
normalize_reset_time = parsing.normalize_reset_time
parse_single_codex_window = parsing.parse_single_codex_window
parse_codex_rate_limits = parsing.parse_codex_rate_limits
format_model_entry = parsing.format_model_entry
extract_primary_models = parsing.extract_primary_models
parse_quota_summary = parsing.parse_quota_summary
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Antigravity & Codex Quota API")


@app.get("/api/health")
def health():
    return {"status": "ok", "schema_version": 2}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 自动加载当前目录及上级目录的 .env 文件
for env_path in [".env", "../.env", os.path.join(os.path.dirname(__file__), ".env"), os.path.join(os.path.dirname(__file__), "..", ".env")]:
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

CONFIG_DIR = os.path.expanduser("~/.config/antigravity-usage")
TOKENS_FILE = os.path.join(CONFIG_DIR, "tokens.json")
CACHE_FILE = os.path.join(CONFIG_DIR, "quota_cache.json")

CHATGPT_CONFIG_DIR = os.path.expanduser("~/.config/chatgpt-usage")
CHATGPT_TOKENS_FILE = os.path.join(CHATGPT_CONFIG_DIR, "tokens.json")
CHATGPT_CACHE_FILE = os.path.join(CHATGPT_CONFIG_DIR, "quota_cache.json")

CLIENT_ID = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_ID", "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_SECRET", "YOUR_CLIENT_SECRET_HERE")
REDIRECT_URI = "http://127.0.0.1:8085/callback"

# OpenAI Codex 客户端 ID 与配置
OPENAI_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"

# 企业微信推送配置
WX_CORP_ID = os.environ.get("WX_CORP_ID", "YOUR_WECHAT_CORP_ID")
WX_SECRET = os.environ.get("WX_SECRET", "YOUR_WECHAT_SECRET")
WX_AGENT_ID = int(os.environ.get("WX_AGENT_ID", "1000002"))

ANTIGRAVITY_DEBUG = os.environ.get("ANTIGRAVITY_DEBUG", "false").lower() in ("true", "1", "yes")
CODEX_QUOTA_DEBUG = os.environ.get("CODEX_QUOTA_DEBUG", "false").lower() in ("true", "1", "yes")

# 临时存放 PKCE code_verifier 的内存字典 {state: (code_verifier, timestamp)}
PKCE_CACHE = {}
GOOGLE_PKCE_CACHE = {}
OAUTH_LOCK = threading.Lock()
CACHE_SCHEMA_VERSION = 2

class PasteUrlRequest(BaseModel):
    url: str

class SwitchAccountRequest(BaseModel):
    email: str

class SaveChatGPTSessionRequest(BaseModel):
    raw_data: str

class SaveChatGPTAuthUrlRequest(BaseModel):
    url: str
    state: str = ""

# ==================== Google 模块 ====================

def load_quota_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}

def save_quota_cache(cache_data):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, indent=2)
    except Exception:
        pass

def refresh_account_token(acc: dict) -> str:
    expires_at = acc.get("expiresAt", 0)
    now_ms = int(time.time() * 1000)
    if expires_at - now_ms > 300 * 1000 and acc.get("accessToken"):
        return acc["accessToken"]
    
    refresh_token = acc.get("refreshToken")
    if not refresh_token:
        return acc.get("accessToken", "")
        
    data = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }).encode("utf-8")
    
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        new_token = res["access_token"]
        expires_in = res.get("expires_in", 3600)
        acc["accessToken"] = new_token
        acc["expiresAt"] = now_ms + (expires_in * 1000)
        return new_token

def fetch_primary_models_probe(access_token: str, project_id: str = "aicode-consumers", fallback_groups: list = None):
    req = urllib.request.Request(GOOGLE_MODELS_SOURCE,
        data=json.dumps({"project": project_id} if project_id else {}).encode(),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "User-Agent": "antigravity"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("models"), dict):
            return None
        return extract_primary_models(data["models"])
    except Exception:
        return None


def fetch_single_probe(access_token: str, project_id: str = "aicode-consumers"):
    # Compatibility wrapper: no representative model may stand in for a group.
    models = fetch_primary_models_probe(access_token, project_id)
    if models is None:
        raise RuntimeError("fetchAvailableModels unavailable")
    return None, "", None, "", models


def fetch_quota_summary(access_token: str, project_id: str = "aicode-consumers"):
    for payload in ([{"project": project_id}, {}] if project_id else [{}]):
        req = urllib.request.Request(GOOGLE_SUMMARY_SOURCE, data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json", "User-Agent": "antigravity"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                parsed = parse_quota_summary(json.loads(resp.read().decode("utf-8")))
            if parsed:
                return parsed
        except Exception:
            continue
    return None


def google_quota_snapshot(email, access_token, project_id="aicode-consumers", force_refresh=False):
    cache = load_quota_cache()
    cached = cache.get(email, {})
    if cached.get("schema_version") != CACHE_SCHEMA_VERSION:
        cached = {}  # Legacy snapshots may contain invented models and percentages.
    now = time.time()
    if not force_refresh and now - cached.get("timestamp", 0) < 180:
        return copy.deepcopy(cached["data"])
    groups = fetch_quota_summary(access_token, project_id)
    models = fetch_primary_models_probe(access_token, project_id)
    fetched = utc_now()
    if groups is None and models is None:
        if cached.get("data"):
            result = mark_stale(cached["data"])
            result["error"] = "实时接口不可用，正在展示历史快照"
            result["last_attempt_at"] = fetched
            return result
        return {"groups": [], "models": [], "status": "unavailable", "stale": False,
                "source": [GOOGLE_SUMMARY_SOURCE, GOOGLE_MODELS_SOURCE], "fetched_at": fetched,
                "error": "无法获取真实额度或模型列表", "degradation": {"status": "unknown", "reason": "缺少可靠路由证据"}}
    # Summary owns group quotas, model endpoint owns model quotas. Never copy one into the other.
    result = {"groups": groups or [], "models": models or [],
              "source": [GOOGLE_SUMMARY_SOURCE] if groups else [], "fetched_at": fetched, "stale": False,
              "status": aggregate_status((groups or []) + (models or [])) if groups or models else "unavailable",
              "degradation": {"status": "unknown", "reason": "缺少可靠的路由降级证据"},
              "sources": {"summary": "available" if groups is not None else "unavailable",
                          "models": "available" if models is not None else "unavailable"}}
    if models is not None:
        result["source"].append(GOOGLE_MODELS_SOURCE)
    # Partial failures retain only the failed source's verified history, explicitly stale.
    for key, fresh in (("groups", groups), ("models", models)):
        if fresh is None and cached.get("data", {}).get(key):
            result[key] = mark_stale(cached["data"][key])
            result["stale"] = True
            result["status"] = "stale"
    if not result["stale"]:
        cache[email] = {"schema_version": CACHE_SCHEMA_VERSION, "timestamp": now, "data": result}
        save_quota_cache(cache)
    return result


def fetch_grouped_quota(email: str, access_token: str, project_id: str = "aicode-consumers", force_refresh: bool = False):
    result = google_quota_snapshot(email, access_token, project_id, force_refresh)
    return result["groups"], result["models"]

# ==================== ChatGPT Plus / Codex 个人账号模块 ====================

def load_chatgpt_store():
    if os.path.exists(CHATGPT_TOKENS_FILE):
        try:
            with open(CHATGPT_TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"activeEmail": None, "accounts": {}}

def save_chatgpt_store(data):
    os.makedirs(CHATGPT_CONFIG_DIR, exist_ok=True)
    with open(CHATGPT_TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def parse_jwt_payload(token_str: str) -> dict:
    try:
        parts = token_str.strip().split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            payload_json = base64.urlsafe_b64decode(payload_b64.encode("ascii")).decode("utf-8")
            return json.loads(payload_json)
    except Exception:
        pass
    return {}

def extract_chatgpt_account_info(access_token: str, id_token: str = "") -> dict:
    info = {
        "email": "",
        "name": "",
        "plan_type": "unknown",
        "chatgpt_account_id": "",
        "chatgpt_user_id": "",
        "expires_at": 0
    }
    
    # 解析 id_token 或 access_token 中的 Claims
    for tok in [id_token, access_token]:
        if not tok:
            continue
        payload = parse_jwt_payload(tok)
        if not payload:
            continue
            
        if payload.get("email"):
            info["email"] = payload["email"]
        if payload.get("name"):
            info["name"] = payload["name"]
        if payload.get("exp"):
            info["expires_at"] = payload["exp"]
            
        auth_claim = payload.get("https://api.openai.com/auth", {})
        if auth_claim:
            if auth_claim.get("chatgpt_account_id"):
                info["chatgpt_account_id"] = auth_claim["chatgpt_account_id"]
            if auth_claim.get("chatgpt_user_id"):
                info["chatgpt_user_id"] = auth_claim["chatgpt_user_id"]
            if auth_claim.get("chatgpt_plan_type"):
                info["plan_type"] = auth_claim["chatgpt_plan_type"]
                
        profile_claim = payload.get("https://api.openai.com/profile", {})
        if profile_claim:
            if profile_claim.get("email") and not info["email"]:
                info["email"] = profile_claim["email"]
            if profile_claim.get("name") and not info["name"]:
                info["name"] = profile_claim["name"]
                
    return info

def refresh_chatgpt_account_token(acc: dict) -> str:
    expires_at = acc.get("expiresAt", 0)
    now_sec = int(time.time())
    if expires_at - now_sec > 300 and acc.get("accessToken"):
        return acc["accessToken"]
        
    refresh_token = acc.get("refreshToken")
    if not refresh_token:
        return acc.get("accessToken", "")
        
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "refresh_token": refresh_token
    }).encode("utf-8")
    
    req = urllib.request.Request(OPENAI_TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            new_token = res.get("access_token")
            expires_in = res.get("expires_in", 864000)
            if new_token:
                acc["accessToken"] = new_token
                acc["expiresAt"] = now_sec + expires_in
                if res.get("refresh_token"):
                    acc["refreshToken"] = res["refresh_token"]
                return new_token
    except Exception as e:
        pass
    return acc.get("accessToken", "")

def load_chatgpt_cache():
    if os.path.exists(CHATGPT_CACHE_FILE):
        try:
            with open(CHATGPT_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_chatgpt_cache(data):
    try:
        os.makedirs(CHATGPT_CONFIG_DIR, exist_ok=True)
        with open(CHATGPT_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def codex_result(data, acc):
    rate_limits, additional, credits = parse_codex_rate_limits(data)
    five, week = rate_limits["five_hour"], rate_limits["weekly"]
    models = []
    for pool in additional:
        # Feature pools have no implied model; only explicit model IDs create models.
        if not pool.get("model_id"):
            continue
        model_id = pool["model_id"]
        target = pool["five_hour"] or pool["weekly"] or next(iter(pool["windows"]), {})
        remaining = target.get("remaining_percent")
        models.append(dict(model_metadata(model_id, {}), id=model_id, name=model_id, rawModelId=model_id,
            displayName=pool["name"], remainingPercent=remaining, resetTime=target.get("reset_at", ""),
            used=None if remaining is None else remaining < 100, isPrimary=True, tagLabel="专属模型额度",
            status=data_status(remaining), source=CODEX_SOURCE, fetched_at=pool["fetched_at"], stale=False,
            windows=pool["windows"]))
    fetched = utc_now()
    return {"success": True, "status": aggregate_status(rate_limits["windows"] + additional),
            "plan": data.get("plan_type") or acc.get("plan", "unknown"), "email": acc.get("email", "unknown"),
            "rate_limits": rate_limits, "additional_rate_limits": additional, "credits": credits,
            "five_hour_remaining": five["remaining_percent"] if five else None,
            "five_hour_reset": five["reset_at"] if five else "",
            "seven_day_remaining": week["remaining_percent"] if week else None,
            "seven_day_reset": week["reset_at"] if week else "", "models": models,
            "fetched_at": fetched, "updated_at": fetched, "source": CODEX_SOURCE, "stale": False,
            "rate_limit_reached_type": data.get("rate_limit_reached_type"),
            "chatgpt_web_quota": {"status": "unavailable", "reason": "当前授权来源未提供可可靠读取的网页端独立额度"}}


def probe_chatgpt_quota(acc: dict, force_refresh: bool = False) -> dict:
    email = acc.get("email") or "unknown"
    cache = load_chatgpt_cache()
    entry = cache.get(email, {})
    if entry.get("schema_version") != CACHE_SCHEMA_VERSION:
        entry = {}
    now = time.time()
    if not force_refresh and now - entry.get("timestamp", 0) < 75:
        return copy.deepcopy(entry["data"])
    error, http_status = "网络或响应异常", None
    try:
        token = refresh_chatgpt_account_token(acc)
        headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli", "Accept": "application/json"}
        if acc.get("chatgptAccountId"):
            headers["ChatGPT-Account-Id"] = acc["chatgptAccountId"]
        req = urllib.request.Request(CODEX_SOURCE, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict) or not any(k in data for k in ("rate_limit", "rate_limits", "additional_rate_limits", "credits", "five_hour", "weekly")):
            raise ValueError("Missing quota structure")
        result = codex_result(data, acc)
        cache[email] = {"schema_version": CACHE_SCHEMA_VERSION, "timestamp": now, "data": result}
        save_chatgpt_cache(cache)
        return result
    except urllib.error.HTTPError as exc:
        http_status = exc.code
        error = {401: "凭证无效或已过期，请重新授权", 403: "当前授权无权读取额度", 429: "请求受到速率限制，无法确认限制窗口"}.get(exc.code, "上游服务异常")
    except Exception:
        # Never log response bodies, credential-bearing URLs or token exceptions.
        pass
    fetched = utc_now()
    if entry.get("data"):
        result = mark_stale(entry["data"])
        result.update(error=error + "，展示历史快照", last_attempt_at=fetched, http_status=http_status)
        return result
    result = codex_result({}, acc)
    result.update(success=False, status="unavailable", error=error, http_status=http_status)
    return result

# ==================== 接口路由 ====================

# 1. ChatGPT Plus 账号管理与配额接口

@app.get("/api/chatgpt/login-url")
def get_chatgpt_login_url():
    """生成官方 OpenAI Codex 授权登录链接 (带 PKCE code_challenge)"""
    # 清理过期 PKCE 缓存
    now = time.time()
    with OAUTH_LOCK:
        for st in list(PKCE_CACHE.keys()):
            if now - PKCE_CACHE[st][1] > 1800:
                PKCE_CACHE.pop(st, None)
            
    code_verifier = secrets.token_hex(32)
    state = secrets.token_hex(16)
    challenge_bytes = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")
    
    with OAUTH_LOCK:
        PKCE_CACHE[state] = (code_verifier, now)
    
    params = {
        "response_type": "code",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true"
    }
    auth_url = f"{OPENAI_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
    return {
        "auth_url": auth_url,
        "state": state
    }

@app.post("/api/chatgpt/complete-oauth")
def complete_chatgpt_oauth(req: SaveChatGPTAuthUrlRequest):
    """通过用户粘贴的 localhost:1455/auth/callback?code=... 完成 OAuth 换票"""
    parsed = urllib.parse.urlparse(req.url)
    params = urllib.parse.parse_qs(parsed.query)
    code = params.get("code", [None])[0]
    state = params.get("state", [None])[0]
    
    if not code:
        raise HTTPException(status_code=400, detail="未从粘贴的地址中提取到 authorization code")
        
    if not state or (req.state and req.state != state):
        raise HTTPException(status_code=400, detail="OAuth state 不匹配，请重新登录")
    with OAUTH_LOCK:
        session = PKCE_CACHE.pop(state, None)
    if not session or time.time() - session[1] > 1800:
        raise HTTPException(status_code=400, detail="OAuth state 无效或登录会话已过期")
    code_verifier = session[0]

    post_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "client_id": OPENAI_OAUTH_CLIENT_ID,
        "code": code,
        "redirect_uri": OPENAI_OAUTH_REDIRECT_URI,
        "code_verifier": code_verifier
    }).encode("utf-8")
    
    req_token = urllib.request.Request(
        OPENAI_TOKEN_URL,
        data=post_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req_token, timeout=15) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"OpenAI 换取 Token 失败 (HTTP {e.code})")
    except Exception as ex:
        raise HTTPException(status_code=500, detail="请求 OpenAI 异常，请稍后重试")
        
    access_token = token_resp.get("access_token")
    id_token = token_resp.get("id_token", "")
    refresh_token = token_resp.get("refresh_token", "")
    expires_in = token_resp.get("expires_in", 864000)
    
    if not access_token:
        raise HTTPException(status_code=400, detail="OpenAI 未返回 access_token")
        
    info = extract_chatgpt_account_info(access_token, id_token)
    email = info.get("email") or f"chatgpt-plus-{int(time.time())}@openai.user"
    
    store = load_chatgpt_store()
    store["accounts"][email] = {
        "email": email,
        "name": info.get("name") or "ChatGPT Plus 会员",
        "plan": info.get("plan_type") or "unknown",
        "chatgptAccountId": info.get("chatgpt_account_id"),
        "chatgptUserId": info.get("chatgpt_user_id"),
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "idToken": id_token,
        "expiresAt": int(time.time()) + expires_in,
        "addedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if not store.get("activeEmail"):
        store["activeEmail"] = email
    save_chatgpt_store(store)
    
    return {
        "success": True,
        "email": email,
        "plan": store["accounts"][email]["plan"],
        "message": f"ChatGPT Plus 账号 {email} 授权成功并已绑定！"
    }

@app.post("/api/chatgpt/save-session")
def save_chatgpt_session(req: SaveChatGPTSessionRequest):
    """支持用户直接粘贴 access_token 或 /api/auth/session 的 JSON 数据"""
    text = req.raw_data.strip()
    access_token = ""
    refresh_token = ""
    email = ""
    plan = "unknown"
    chatgpt_account_id = ""
    
    # 尝试作为 JSON 解析（用户直接粘贴 chatgpt.com/api/auth/session 全文）
    if text.startswith("{") and text.endswith("}"):
        try:
            doc = json.loads(text)
            access_token = doc.get("accessToken") or doc.get("access_token") or ""
            user = doc.get("user", {})
            email = user.get("email", "")
            if not email and "email" in doc:
                email = doc["email"]
            if doc.get("refreshToken"):
                refresh_token = doc["refreshToken"]
        except Exception:
            pass
            
    # 如果不是 JSON，则作为 raw Bearer access_token
    if not access_token:
        if text.startswith("Bearer "):
            access_token = text[7:].strip()
        else:
            access_token = text
            
    info = extract_chatgpt_account_info(access_token)
    if not email:
        email = info.get("email")
    if not email:
        email = f"chatgpt-user-{secrets.token_hex(3)}@openai.user"
        
    store = load_chatgpt_store()
    store["accounts"][email] = {
        "email": email,
        "name": info.get("name") or "ChatGPT Plus 会员",
        "plan": info.get("plan_type") or "unknown",
        "chatgptAccountId": info.get("chatgpt_account_id") or chatgpt_account_id,
        "chatgptUserId": info.get("chatgpt_user_id"),
        "accessToken": access_token,
        "refreshToken": refresh_token,
        "expiresAt": info.get("expires_at") or (int(time.time()) + 864000),
        "addedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    if not store.get("activeEmail"):
        store["activeEmail"] = email
    save_chatgpt_store(store)
    
    return {
        "success": True,
        "email": email,
        "plan": store["accounts"][email]["plan"],
        "message": f"ChatGPT Plus 账号 {email} 已成功添加！"
    }

@app.get("/api/chatgpt/accounts")
def get_chatgpt_accounts():
    """获取所有已绑定的个人 ChatGPT Plus 账号"""
    store = load_chatgpt_store()
    accs = []
    active_email = store.get("activeEmail")
    for em, a in store.get("accounts", {}).items():
        accs.append({
            "email": em,
            "name": a.get("name", ""),
            "plan": a.get("plan", "unknown"),
            "isActive": em == active_email,
            "hasRefreshToken": bool(a.get("refreshToken")),
            "addedAt": a.get("addedAt")
        })
    return {
        "has_accounts": len(accs) > 0,
        "active_email": active_email,
        "accounts": accs
    }

@app.get("/api/chatgpt/quota")
def get_chatgpt_quota(refresh: bool = False):
    """查询个人 ChatGPT Plus 会员账号配额状态与 5 小时重置倒计时"""
    store = load_chatgpt_store()
    accounts = store.get("accounts", {})
    if not accounts:
        return []
        
    active_email = store.get("activeEmail")
    results = []
    
    for email, acc in accounts.items():
        quota_res = probe_chatgpt_quota(acc, force_refresh=refresh)
        quota_res["isActive"] = (email == active_email)
        results.append(quota_res)
        
    save_chatgpt_store(store)
    return results

@app.post("/api/chatgpt/accounts/switch")
def switch_chatgpt_account(req: SwitchAccountRequest):
    store = load_chatgpt_store()
    if req.email not in store.get("accounts", {}):
        raise HTTPException(status_code=404, detail="账号不存在")
    store["activeEmail"] = req.email
    save_chatgpt_store(store)
    return {"success": True, "active": req.email}

@app.delete("/api/chatgpt/accounts/{email}")
def delete_chatgpt_account(email: str):
    store = load_chatgpt_store()
    if email in store.get("accounts", {}):
        del store["accounts"][email]
        if store.get("activeEmail") == email:
            store["activeEmail"] = next(iter(store["accounts"]), None)
        save_chatgpt_store(store)
    return {"success": True, "message": f"账号 {email} 已移除"}

# 2. 微信推送测试

@app.post("/api/codex/test-push")
def test_codex_push():
    """触发测试企业微信推送"""
    token_url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_SECRET}"
    req = urllib.request.Request(token_url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode())
        token = res.get("access_token")
    if not token:
        raise HTTPException(status_code=500, detail="获取微信 Token 失败")
        
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    payload = {
        "touser": "@all",
        "msgtype": "textcard",
        "agentid": WX_AGENT_ID,
        "textcard": {
            "title": "⚡ ChatGPT Plus & Codex 额度监控通知",
            "description": f"<div class=\"gray\">{now_str}</div><div class=\"normal\">来自 sentools.sen666.com/quota/ 的测试推送，通道运转正常！</div>",
            "url": "https://sentools.sen666.com/quota/",
            "btntxt": "打开额度监控"
        }
    }
    req2 = urllib.request.Request(send_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=10) as resp2:
        return json.loads(resp2.read().decode())

# 3. Google Antigravity 接口

@app.get("/api/status")
def get_status():
    has_tokens = os.path.exists(TOKENS_FILE)
    accounts = []
    active_email = None
    if has_tokens:
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                active_email = data.get("activeEmail")
                accs = data.get("accounts", {})
                for em, acc in accs.items():
                    accounts.append({
                        "email": em,
                        "isActive": em == active_email,
                        "expiresAt": acc.get("expiresAt"),
                        "projectId": acc.get("projectId")
                    })
        except Exception:
            pass
    return {
        "has_tokens": has_tokens,
        "accounts": accounts,
        "active_email": active_email
    }

@app.get("/api/quota")
def get_quota(refresh: bool = False):
    if not os.path.exists(TOKENS_FILE):
        return []
        
    try:
        with open(TOKENS_FILE, "r", encoding="utf-8") as f:
            store = json.load(f)
    except Exception as e:
        return {"error": "读取凭证文件失败"}
        
    accounts = store.get("accounts", {})
    active_email = store.get("activeEmail")
    results = []
    changed = False
    
    for email, acc in accounts.items():
        try:
            tok = refresh_account_token(acc)
            changed = True
        except Exception as e:
            entry = load_quota_cache().get(email, {})
            snapshot = mark_stale(entry["data"]) if entry.get("schema_version") == CACHE_SCHEMA_VERSION and entry.get("data") else {
                "groups": [], "models": [], "status": "unavailable", "stale": False,
                "source": [GOOGLE_SUMMARY_SOURCE, GOOGLE_MODELS_SOURCE], "fetched_at": None}
            results.append(dict(snapshot, **{
                "email": email,
                "isActive": email == active_email,
                "projectId": acc.get("projectId", "aicode-consumers"),
                "error": "账号凭证刷新失败",
                "last_attempt_at": utc_now()
            }))
            continue
            
        proj = acc.get("projectId") or "aicode-consumers"
        snapshot = google_quota_snapshot(email, tok, proj, force_refresh=refresh)
        results.append(dict(snapshot, email=email, isActive=email == active_email,
                            projectId=proj, tier=acc.get("tier") or acc.get("plan") or "套餐未知"))

    if changed:
        try:
            with open(TOKENS_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, indent=2)
        except Exception:
            pass
            
    return results

@app.get("/api/login-url")
def get_login_url():
    now = time.time()
    with OAUTH_LOCK:
        for key in list(GOOGLE_PKCE_CACHE):
            if now - GOOGLE_PKCE_CACHE[key][1] > 1800:
                GOOGLE_PKCE_CACHE.pop(key, None)
        state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        GOOGLE_PKCE_CACHE[state] = (verifier, now)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    auth_params = {"state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/cloud-platform",
        "access_type": "offline",
        "prompt": "consent"
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(auth_params)
    return {"login_url": url}

@app.post("/api/complete-login")
def complete_login(req: PasteUrlRequest):
    try:
        parsed = urllib.parse.urlparse(req.url)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if not code:
            fragment_params = urllib.parse.parse_qs(parsed.fragment)
            code = fragment_params.get("code", [None])[0]
            
        if not code:
            raise HTTPException(status_code=400, detail="未从 URL 中提取到授权 code 参数")
        
        state = params.get("state", [None])[0]
        with OAUTH_LOCK:
            session = GOOGLE_PKCE_CACHE.pop(state, None) if state else None
        if not session or time.time() - session[1] > 1800:
            raise HTTPException(status_code=400, detail="OAuth state 无效或登录会话已过期")

        token_data = urllib.parse.urlencode({
            "code_verifier": session[0],
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI
        }).encode("utf-8")
        
        req_token = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data)
        with urllib.request.urlopen(req_token, timeout=15) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
            
        access_token = token_resp["access_token"]
        refresh_token = token_resp.get("refresh_token", "")
        expires_in = token_resp.get("expires_in", 3600)
        
        req_user = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(req_user, timeout=15) as resp_user:
            user_info = json.loads(resp_user.read().decode("utf-8"))
            email = user_info.get("email")
            
        if not email:
            raise HTTPException(status_code=400, detail="获取用户邮箱失败")
            
        os.makedirs(CONFIG_DIR, exist_ok=True)
        store = {"activeEmail": email, "accounts": {}}
        if os.path.exists(TOKENS_FILE):
            try:
                with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                    store = json.load(f)
            except Exception:
                pass
                
        store["accounts"][email] = {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": int(time.time() * 1000) + (expires_in * 1000),
            "email": email,
            "projectId": "aicode-consumers"
        }
        if not store.get("activeEmail"):
            store["activeEmail"] = email
            
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
            
        return {"success": True, "email": email, "message": f"账号 {email} 授权成功并已保存！"}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Google 授权请求失败，请重新登录或稍后重试")

@app.post("/api/accounts/switch")
def switch_account(req: SwitchAccountRequest):
    if not os.path.exists(TOKENS_FILE):
        raise HTTPException(status_code=400, detail="无账号文件")
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        store = json.load(f)
    if req.email not in store.get("accounts", {}):
        raise HTTPException(status_code=404, detail="账号不存在")
    store["activeEmail"] = req.email
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)
    return {"success": True, "active": req.email}

@app.delete("/api/accounts/{email}")
def delete_account(email: str):
    if not os.path.exists(TOKENS_FILE):
        return {"success": True}
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        store = json.load(f)
    if email in store.get("accounts", {}):
        del store["accounts"][email]
        if store.get("activeEmail") == email:
            store["activeEmail"] = next(iter(store["accounts"]), None)
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
    return {"success": True, "message": f"账号 {email} 已移除"}
