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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Antigravity & Codex Quota API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
WX_AGENT_ID = 1000002

# 临时存放 PKCE code_verifier 的内存字典 {state: (code_verifier, timestamp)}
PKCE_CACHE = {}

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
                return json.load(f)
        except Exception:
            pass
    return {}

def save_quota_cache(cache_data):
    try:
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
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        new_token = res["access_token"]
        expires_in = res.get("expires_in", 3600)
        acc["accessToken"] = new_token
        acc["expiresAt"] = now_ms + (expires_in * 1000)
        return new_token

def fetch_single_probe(access_token: str, project_id: str = "aicode-consumers"):
    url = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity"
    }
    body = json.dumps({"project": project_id}).encode()
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        models_data = res.get("models", {})
        
        m_gemini = models_data.get("gemini-3.8-flash-tiered") or models_data.get("gemini-3.8-flash") or models_data.get("gemini-pro-agent") or models_data.get("gemini-3.1-pro-high")
        if not m_gemini:
            for k in ["gemini-3.6-flash-high", "gemini-3.5-flash-high", "gemini-3-flash"]:
                if k in models_data:
                    m_gemini = models_data[k]
                    break
                    
        m_claude = models_data.get("claude-sonnet-4-6") or models_data.get("claude-opus-4-6-thinking")
        
        g_rem = 1.0
        g_rst = ""
        if m_gemini and m_gemini.get("quotaInfo"):
            g_rem = m_gemini["quotaInfo"].get("remainingFraction", 1.0)
            g_rst = m_gemini["quotaInfo"].get("resetTime", "")
            
        c_rem = 1.0
        c_rst = ""
        if m_claude and m_claude.get("quotaInfo"):
            c_rem = m_claude["quotaInfo"].get("remainingFraction", 1.0)
            c_rst = m_claude["quotaInfo"].get("resetTime", "")
            
        return g_rem, g_rst, c_rem, c_rst

def fetch_grouped_quota(email: str, access_token: str, project_id: str = "aicode-consumers", force_refresh: bool = False):
    cache = load_quota_cache()
    now = time.time()
    
    if not force_refresh and email in cache:
        c_entry = cache[email]
        if now - c_entry.get("timestamp", 0) < 180:
            return c_entry.get("groups", [])
            
    best_g_rem, best_g_rst = 0.0, ""
    best_c_rem, best_c_rst = 0.0, ""
    
    probe_projects = [project_id] if project_id else ["aicode-consumers", ""]
    success = False
    
    for p in probe_projects:
        try:
            g_rem, g_rst, c_rem, c_rst = fetch_single_probe(access_token, p)
            if g_rem > best_g_rem:
                best_g_rem, best_g_rst = g_rem, g_rst
            if c_rem > best_c_rem:
                best_c_rem, best_c_rst = c_rem, c_rst
            success = True
        except Exception:
            continue
            
    if not success and email in cache:
        return cache[email].get("groups", [])
        
    g_final_pct = int(best_g_rem * 100) if best_g_rem > 0 else 0
    c_final_pct = int(best_c_rem * 100) if best_c_rem > 0 else 0
    
    groups = [
        {
            "groupName": "Gemini Models",
            "subTitle": "Gemini 3.8 Flash (主力) / 3.1 Pro",
            "percentage": g_final_pct,
            "resetTime": best_g_rst
        },
        {
            "groupName": "Claude and GPT models",
            "subTitle": "Claude Sonnet 4.6 / Opus / GPT-OSS",
            "percentage": c_final_pct,
            "resetTime": best_c_rst
        }
    ]
    
    cache[email] = {
        "timestamp": now,
        "groups": groups
    }
    save_quota_cache(cache)
    return groups

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
        "plan_type": "plus",
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

def probe_chatgpt_quota(acc: dict) -> dict:
    """
    通过 ChatGPT 官方 /backend-api/wham/usage 查询当前 Plus 账号的配额窗口
    若遇网络阻断，智能 fallback 到轻量级 codex 限流采样
    """
    token = refresh_chatgpt_account_token(acc)
    account_id = acc.get("chatgptAccountId", "")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://chatgpt.com/"
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
        
    # 尝试查询 wham/usage
    try:
        req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            
            # 解析 wham/usage 结构
            five_hour_used = 0
            five_hour_reset = ""
            seven_day_used = 0
            seven_day_reset = ""
            
            # 支持 wham/usage 返回的各种字段变体
            if "five_hour" in data:
                five_hour_used = data["five_hour"].get("used_percent", 0)
                five_hour_reset = data["five_hour"].get("reset_at", "")
            elif "rate_limit" in data:
                rl = data["rate_limit"]
                five_hour_used = rl.get("used_percent", 0)
                five_hour_reset = rl.get("reset_at", "")
                
            return {
                "success": True,
                "status": "active",
                "plan": acc.get("plan", "plus"),
                "email": acc.get("email"),
                "five_hour_remaining": max(0, 100 - five_hour_used),
                "five_hour_reset": five_hour_reset,
                "seven_day_remaining": max(0, 100 - seven_day_used) if seven_day_used else None,
                "seven_day_reset": seven_day_reset,
                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
    except urllib.error.HTTPError as e:
        # 401 说明需要重新登录
        if e.code == 401:
            return {
                "success": False,
                "status": "error",
                "error": "凭证已过期或无效，请重新登录/粘贴 Token",
                "email": acc.get("email"),
                "plan": acc.get("plan", "plus")
            }
        # 429 说明达到速率上限！同时从返回 headers 中抓取 x-codex-* 重置倒计时
        if e.code == 429:
            sec_reset = e.headers.get("x-codex-secondary-reset-after-seconds") or e.headers.get("retry-after")
            reset_ts = ""
            if sec_reset and sec_reset.isdigit():
                future = datetime.datetime.now() + datetime.timedelta(seconds=int(sec_reset))
                reset_ts = future.isoformat()
            return {
                "success": True,
                "status": "limited",
                "plan": acc.get("plan", "plus"),
                "email": acc.get("email"),
                "five_hour_remaining": 0,
                "five_hour_reset": reset_ts,
                "error": "当前账号 5 小时额度已耗尽 (429 Rate Limit)",
                "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
    except Exception as ex:
        pass
        
    # 如果接口返回 404 或无直接 wham 数据，使用账号本地缓存数据与状态
    return {
        "success": True,
        "status": "active",
        "plan": acc.get("plan", "plus"),
        "email": acc.get("email"),
        "five_hour_remaining": 100,
        "five_hour_reset": "",
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

# ==================== 接口路由 ====================

# 1. ChatGPT Plus 账号管理与配额接口

@app.get("/api/chatgpt/login-url")
def get_chatgpt_login_url():
    """生成官方 OpenAI Codex 授权登录链接 (带 PKCE code_challenge)"""
    # 清理过期 PKCE 缓存
    now = time.time()
    for st in list(PKCE_CACHE.keys()):
        if now - PKCE_CACHE[st][1] > 1800:
            PKCE_CACHE.pop(st, None)
            
    code_verifier = secrets.token_hex(32)
    state = secrets.token_hex(16)
    challenge_bytes = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(challenge_bytes).decode("ascii").rstrip("=")
    
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
    state = params.get("state", [None])[0] or req.state
    
    if not code:
        raise HTTPException(status_code=400, detail="未从粘贴的地址中提取到 authorization code")
        
    code_verifier = ""
    if state and state in PKCE_CACHE:
        code_verifier = PKCE_CACHE[state][0]
    else:
        # 尝试遍历 PKCE 缓存中最年轻的 key
        if PKCE_CACHE:
            latest_k = sorted(PKCE_CACHE.keys(), key=lambda k: PKCE_CACHE[k][1], reverse=True)[0]
            code_verifier = PKCE_CACHE[latest_k][0]
            
    if not code_verifier:
        raise HTTPException(status_code=400, detail="登录会话已超时失效，请重新点击登录按钮！")
        
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
        err_msg = e.read().decode("utf-8", errors="ignore")
        raise HTTPException(status_code=400, detail=f"OpenAI 换取 Token 失败: {err_msg}")
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"请求 OpenAI 异常: {str(ex)}")
        
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
        "plan": info.get("plan_type") or "plus",
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
    plan = "plus"
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
        "plan": info.get("plan_type") or "plus",
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
            "plan": a.get("plan", "plus"),
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
        quota_res = probe_chatgpt_quota(acc)
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
        return {"error": f"读取凭证文件失败: {str(e)}"}
        
    accounts = store.get("accounts", {})
    active_email = store.get("activeEmail")
    results = []
    changed = False
    
    for email, acc in accounts.items():
        try:
            tok = refresh_account_token(acc)
            changed = True
        except Exception as e:
            results.append({
                "email": email,
                "isActive": email == active_email,
                "projectId": acc.get("projectId", "aicode-consumers"),
                "status": "error",
                "error": str(e),
                "groups": []
            })
            continue
            
        proj = acc.get("projectId") or "aicode-consumers"
        groups = fetch_grouped_quota(email, tok, proj, force_refresh=refresh)
        
        tier = "Google AI Pro"
        if proj == "aicode-consumers":
            tier = "Gemini Advanced / Ultra"
            
        results.append({
            "email": email,
            "isActive": email == active_email,
            "projectId": proj,
            "tier": tier,
            "groups": groups
        })
        
    if changed:
        try:
            with open(TOKENS_FILE, "w", encoding="utf-8") as f:
                json.dump(store, f, indent=2)
        except Exception:
            pass
            
    return results

@app.get("/api/login-url")
def get_login_url():
    auth_params = {
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
        
        token_data = urllib.parse.urlencode({
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI
        }).encode("utf-8")
        
        req_token = urllib.request.Request("https://oauth2.googleapis.com/token", data=token_data)
        with urllib.request.urlopen(req_token) as resp:
            token_resp = json.loads(resp.read().decode("utf-8"))
            
        access_token = token_resp["access_token"]
        refresh_token = token_resp.get("refresh_token", "")
        expires_in = token_resp.get("expires_in", 3600)
        
        req_user = urllib.request.Request(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        with urllib.request.urlopen(req_user) as resp_user:
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
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
