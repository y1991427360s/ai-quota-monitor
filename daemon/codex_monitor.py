"""
Codex / OpenAI 额度与异常主动轮询报警守护脚本
"""
import os
import sys
import time
import json
import urllib.request
import subprocess
from datetime import datetime

CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))
WX_CORP_ID = os.environ.get("WX_CORP_ID", "YOUR_WECHAT_CORP_ID")
WX_SECRET = os.environ.get("WX_SECRET", "YOUR_WECHAT_SECRET")
WX_AGENT_ID = 1000002

_token_cache = {"token": "", "expires_at": 0}

def get_access_token():
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={WX_CORP_ID}&corpsecret={WX_SECRET}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if data.get("errcode") == 0:
                _token_cache["token"] = data["access_token"]
                _token_cache["expires_at"] = now + data.get("expires_in", 7200) - 200
                return _token_cache["token"]
    except Exception as e:
        print(f"获取微信 Token 失败: {e}", file=sys.stderr)
    return ""

def push_wechat_card(title: str, text: str):
    token = get_access_token()
    if not token:
        return False
    send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload = {
        "touser": "@all",
        "msgtype": "textcard",
        "agentid": WX_AGENT_ID,
        "textcard": {
            "title": title,
            "description": f"<div class=\"gray\">{now_str}</div><div class=\"normal\">{text}</div>",
            "url": "https://sentools.sen666.com/quota/",
            "btntxt": "查看额度看板"
        }
    }
    try:
        req = urllib.request.Request(send_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode())
            return res.get("errcode") == 0
    except Exception as e:
        print(f"推送异常: {e}", file=sys.stderr)
    return False

def check_postgres_anomalies():
    sql = """
    SELECT json_agg(t) FROM (
      SELECT id, name, status, rate_limited_at, error_message
      FROM accounts
      WHERE platform = 'openai' AND status = 'active'
        AND rate_limited_at > now() - interval '10 minutes'
    ) t;
    """
    try:
        res = subprocess.check_output(
            ["sudo", "docker", "exec", "-i", "sub2api-postgres", "psql", "-U", "sub2api", "-d", "sub2api", "-t", "-A"],
            input=sql.encode("utf-8"),
            stderr=subprocess.DEVNULL,
            timeout=10
        )
        data = res.decode("utf-8").strip()
        if data and data != "":
            return json.loads(data)
    except Exception as e:
        print(f"DB 查询异常: {e}", file=sys.stderr)
    return []

def main():
    print(f"[{datetime.now()}] Codex 额度与限额监控已启动，轮询周期: {CHECK_INTERVAL_SECONDS}s")
    alerted_accounts = set()
    while True:
        try:
            anomalies = check_postgres_anomalies()
            if anomalies:
                for item in anomalies:
                    acc_id = item.get("id")
                    if acc_id not in alerted_accounts:
                        alerted_accounts.add(acc_id)
                        name = item.get("name", "未知")
                        push_wechat_card(
                            "⚠️ Codex / OpenAI 触发 429 限额",
                            f"账号 <b>{name}</b> 触发上游频率或额度限制，系统已自动标记冷却避让。<br>请前往看板查看或等待刷新。"
                        )
        except Exception as err:
            print("循环异常:", err, file=sys.stderr)
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
