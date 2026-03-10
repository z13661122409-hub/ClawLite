import os, re, json, hashlib
import requests
from urllib.parse import urlparse
from typing import Dict, Any

def _is_private_ip(hostname: str) -> bool:
    # 极简：阻止 localhost / 内网；生产你应更严格（解析 DNS、CIDR 判断等）
    return hostname in {"localhost", "127.0.0.1"} or hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172.")

def web_fetch(url: str, allowlist_csv: str) -> Dict[str, Any]:
    p = urlparse(url)
    if p.scheme not in {"https"}:
        raise ValueError("Only https is allowed")

    host = p.hostname or ""
    if _is_private_ip(host):
        raise ValueError("Private/internal host blocked")

    allowlist = [x.strip().lower() for x in (allowlist_csv or "").split(",") if x.strip()]
    if allowlist and host.lower() not in allowlist:
        raise ValueError(f"Host not in allowlist: {host}")

    r = requests.get(url, timeout=20, headers={"User-Agent": "clawlite/0.1"})
    return {"status_code": r.status_code, "url": url, "text": r.text[:20000]}

def _safe_path(sandbox_dir: str, rel_path: str) -> str:
    sandbox_dir = os.path.abspath(sandbox_dir)
    target = os.path.abspath(os.path.join(sandbox_dir, rel_path))
    if not target.startswith(sandbox_dir):
        raise ValueError("Path escapes sandbox")
    return target

def read_file(sandbox_dir: str, path: str, max_bytes: int = 20000) -> Dict[str, Any]:
    fp = _safe_path(sandbox_dir, path)
    with open(fp, "rb") as f:
        data = f.read(max_bytes)
    return {"path": path, "bytes": len(data), "content": data.decode("utf-8", errors="replace")}

def write_file(sandbox_dir: str, path: str, content: str) -> Dict[str, Any]:
    fp = _safe_path(sandbox_dir, path)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "bytes": len(content.encode("utf-8"))}

def append_file(sandbox_dir: str, path: str, content: str) -> Dict[str, Any]:
    fp = _safe_path(sandbox_dir, path)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "a", encoding="utf-8") as f:
        f.write(content)
    return {"path": path, "bytes_appended": len(content.encode("utf-8"))}

def sha256_file(sandbox_dir: str, path: str) -> Dict[str, Any]:
    fp = _safe_path(sandbox_dir, path)
    h = hashlib.sha256()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return {"path": path, "sha256": h.hexdigest()}

TOOLS = {
    "web_fetch": web_fetch,
    "read_file": read_file,
    "write_file": write_file,
    "append_file": append_file,
    "sha256_file": sha256_file,
}