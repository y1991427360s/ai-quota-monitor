"""
Codex / OpenAI 额度与异常主动轮询报警守护脚本
"""
import os
import sys
import time
import json
import urllib.request
import subprocess
import html
from pathlib import Path
from datetime import datetime

def load_environment():
    """与后端一样读取项目配置；已设置的服务环境优先，绝不输出配置内容。"""
    for path in (Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"):
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_environment()
CHECK_INTERVAL_SECONDS = max(10, int(os.getenv("CHECK_INTERVAL_SECONDS", "300")))
WX_CORP_ID = os.environ.get("WX_CORP_ID", "YOUR_WECHAT_CORP_ID")
WX_SECRET = os.environ.get("WX_SECRET", "YOUR_WECHAT_SECRET")
WX_AGENT_ID = int(os.getenv("WX_AGENT_ID", "1000002"))

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
        print(f"获取微信 Token 失败: {type(e).__name__}", file=sys.stderr)
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
        print(f"推送异常: {type(e).__name__}", file=sys.stderr)
    return False

def check_postgres_anomalies():
    sql = """
    SELECT json_agg(t) FROM (
      SELECT id, name, status, rate_limited_at
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
        print(f"DB 查询异常: {type(e).__name__}", file=sys.stderr)
    return []


def alert_description(item):
    """冷却标记只证明存在限流记录，不能证明 429、5H 或 Weekly 耗尽。"""
    name = html.escape(str(item.get("name") or "未知"), quote=True)
    return (f"账号 <b>{name}</b> 存在最近的上游限流或冷却记录。"
            "<br>当前记录未提供明确额度窗口，无法确认是否为额度耗尽；"
            "请前往看板查看真实额度与数据状态。")


def check_once(alerted_accounts):
    anomalies = check_postgres_anomalies() or []
    current = {item.get("id") for item in anomalies if isinstance(item, dict)}
    alerted_accounts.intersection_update(current)
    for item in anomalies:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        acc_id = item["id"]
        if acc_id not in alerted_accounts:
            if push_wechat_card("⚠️ Codex / OpenAI 上游限流记录", alert_description(item)):
                alerted_accounts.add(acc_id)

def main():
    print(f"[{datetime.now()}] Codex 额度与限额监控已启动，轮询周期: {CHECK_INTERVAL_SECONDS}s")
    alerted_accounts = set()
    while True:
        try:
            check_once(alerted_accounts)
        except Exception as err:
            print("循环异常:", type(err).__name__, file=sys.stderr)
        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()
