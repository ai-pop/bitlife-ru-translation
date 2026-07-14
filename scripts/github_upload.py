"""
Заливает файлы в GitHub репозиторий через API.

ВАЖНО: Никогда не коммить токены в репозиторий!
GitHub Secret Scanning автоматически заблокирует push.
Используйте переменные окружения.
"""
import base64
import os
import requests
from pathlib import Path

TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not TOKEN:
    raise SystemExit("Set GITHUB_TOKEN env var")

OWNER = os.environ.get("GITHUB_OWNER", "ai-pop")
REPO = os.environ.get("GITHUB_REPO", "bitlife-ru-translation")
API = "https://api.github.com"

HEADERS = {
    "Authorization": f"token {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28"
}

def api_call(method, url, **kwargs):
    resp = requests.request(method, f"{API}{url}", headers=HEADERS, **kwargs)
    if resp.status_code >= 400:
        print(f"  API error {resp.status_code}: {resp.text[:200]}")
        return None
    if resp.status_code == 204:
        return {}
    return resp.json() if resp.text else {}

def upload_file(path_in_repo, local_path):
    with open(local_path, 'rb') as f:
        content = f.read()
    b64 = base64.b64encode(content).decode('ascii')
    data = api_call("PUT", f"/repos/{OWNER}/{REPO}/contents/{path_in_repo}", json={
        "message": f"Add {path_in_repo}",
        "content": b64,
        "branch": "main"
    })
    return data is not None
