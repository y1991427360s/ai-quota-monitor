import os
import json
import urllib.parse
import urllib.request
import time
import datetime
import subprocess
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

CLIENT_ID = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_ID", "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com")
CLIENT_SECRET = os.environ.get("ANTIGRAVITY_OAUTH_CLIENT_SECRET", "YOUR_CLIENT_SECRET_HERE")
REDIRECT_URI = "http://127.0.0.1:8085/callback"

# 企业微信推送配置
WX_CORP_ID = os.environ.get("WX_CORP_ID", "YOUR_WECHAT_CORP_ID")
WX_SECRET = os.environ.get("WX_SECRET", "YOUR_WECHAT_SECRET")
WX_AGENT_ID = 1000002

class PasteUrlRequest(BaseModel):
    url: str

class SwitchAccountRequest(BaseModel):
    email: str

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
    cached_acc = cache.get(email, {})
    
    best_g_rem = 1.0
    best_g_rst = ""
    best_c_rem = 1.0
    best_c_rst = ""
    
    for i in range(3):
        try:
            g_rem, g_rst, c_rem, c_rst = fetch_single_probe(access_token, project_id)
            if g_rem < best_g_rem or not best_g_rst:
                best_g_rem = g_rem
                best_g_rst = g_rst
            if c_rem < best_c_rem or not best_c_rst:
                best_c_rem = c_rem
                best_c_rst = c_rst
                
            if best_g_rem < 0.99:
                break
        except Exception:
            pass
        time.sleep(0.2)
        
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    
    cached_g = cached_acc.get("gemini", {})
    if cached_g:
        c_pct = cached_g.get("pct", 100.0)
        c_rst_str = cached_g.get("resetTime", "")
        if c_rst_str and c_pct < 90.0:
            try:
                c_rst_dt = datetime.datetime.fromisoformat(c_rst_str.replace("Z", "+00:00"))
                if now_utc < c_rst_dt and best_g_rem >= 0.99:
                    best_g_rem = c_pct / 100.0
                    best_g_rst = c_rst_str
            except Exception:
                pass
                
    g_final_pct = round(best_g_rem * 100, 1)
    c_final_pct = round(best_c_rem * 100, 1)
    
    cached_acc["gemini"] = {"pct": g_final_pct, "resetTime": best_g_rst}
    cached_acc["claude"] = {"pct": c_final_pct, "resetTime": best_c_rst}
    cache[email] = cached_acc
    save_quota_cache(cache)
    
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
    return groups

# ==================== Codex & Sub2API 查询模块 ====================

def query_sub2api_codex_status():
    sql = """
    SELECT json_build_object(
      'key_usage_5h', (
        SELECT json_build_object(
          'total_requests', count(*),
          'total_input', coalesce(sum(input_tokens), 0),
          'total_output', coalesce(sum(output_tokens), 0),
          'total_cache_read', coalesce(sum(cache_read_tokens), 0)
        ) FROM usage_logs WHERE api_key_id = 2 AND created_at > now() - interval '5 hours'
      ),
      'key_usage_24h', (
        SELECT json_build_object(
          'total_requests', count(*),
          'total_input', coalesce(sum(input_tokens), 0),
          'total_output', coalesce(sum(output_tokens), 0),
          'total_cache_read', coalesce(sum(cache_read_tokens), 0)
        ) FROM usage_logs WHERE api_key_id = 2 AND created_at > now() - interval '24 hours'
      ),
      'active_backends', (
        SELECT json_agg(t) FROM (
          SELECT id, name, status, priority, (credentials->>'base_url') as base_url, 
                 rate_multiplier, last_used_at, error_message, rate_limited_at,
                 credentials->>'plan_type' as plan
          FROM accounts
          WHERE platform = 'openai' AND status = 'active'
            AND (
              name ILIKE '%plus%' OR name ILIKE '%codex%' OR type = 'oauth'
              OR (last_used_at > now() - interval '48 hours')
            )
          ORDER BY last_used_at DESC NULLS LAST
          LIMIT 8
        ) t
      ),
      'models_breakdown', (
        SELECT json_agg(t) FROM (
          SELECT model, count(*) as count, sum(input_tokens) as in_tok, sum(output_tokens) as out_tok
          FROM usage_logs
          WHERE api_key_id = 2 AND created_at > now() - interval '24 hours'
          GROUP BY model
          ORDER BY count DESC
          LIMIT 5
        ) t
      )
    );
    """
    try:
        res = subprocess.check_output(
            ["sudo", "docker", "exec", "-i", "sub2api-postgres", "psql", "-U", "sub2api", "-d", "sub2api", "-t", "-A"],
            input=sql.encode("utf-8"),
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        return json.loads(res.decode("utf-8").strip())
    except Exception as e:
        return {"error": str(e)}

# ==================== 接口路由 ====================

@app.get("/api/codex")
def get_codex():
    """获取 Codex 与 OpenAI / ChatGPT Plus 在 Sub2API 的最新配额与活跃状态"""
    return query_sub2api_codex_status()

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
            "title": "⚡ Codex & AI 额度监控测试通知",
            "description": f"<div class=\"gray\">{now_str}</div><div class=\"normal\">来自 sentools.sen666.com/quota/ 的测试推送，通道运转正常！</div>",
            "url": "https://sentools.sen666.com/quota/",
            "btntxt": "打开额度监控"
        }
    }
    req2 = urllib.request.Request(send_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req2, timeout=10) as resp2:
        return json.loads(resp2.read().decode())

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
        is_active = (email == active_email)
        try:
            token = refresh_account_token(acc)
            project_id = acc.get("projectId") or "aicode-consumers"
            groups = fetch_grouped_quota(email, token, project_id, force_refresh=refresh)
            results.append({
                "email": email,
                "isActive": is_active,
                "projectId": project_id,
                "tier": "Google AI Pro",
                "groups": groups,
                "status": "ok"
            })
            changed = True
        except Exception as e:
            results.append({
                "email": email,
                "isActive": is_active,
                "projectId": acc.get("projectId"),
                "status": "error",
                "error": str(e)
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
