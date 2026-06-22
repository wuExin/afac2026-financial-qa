"""Local BM25 debug UI server."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

from src.agent.bm25_debug_service import run_debug_search

ROOT = Path(__file__).resolve().parents[1]
UI_PATH = ROOT / "scripts" / "bm25_debug_ui.html"


def parse_json_body(raw: bytes) -> Dict[str, Any]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 解析失败: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return payload


def make_error(message: str) -> Dict[str, Any]:
    return {"ok": False, "error": message}


class BM25DebugHandler(BaseHTTPRequestHandler):
    server_version = "BM25Debug/1.0"

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_file(UI_PATH, "text/html; charset=utf-8")
            return
        self._send_json(make_error("Not found"), status=404)

    def do_POST(self) -> None:
        if self.path != "/api/search":
            self._send_json(make_error("Not found"), status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = parse_json_body(self.rfile.read(length))
            question = payload.get("question")
            params = payload.get("params", {})
            result = run_debug_search(question, params)
            self._send_json(result)
        except Exception as exc:
            self._send_json(make_error(str(exc)), status=400)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[BM25Debug] {self.address_string()} - {fmt % args}")

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._send_json(make_error(f"静态文件不存在: {path}"), status=404)
            return
        content = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: Dict[str, Any], status: int = 200) -> None:
        content = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    parser = argparse.ArgumentParser(description="BM25 本地调试页面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), BM25DebugHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"BM25 debug UI: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBM25 debug server stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
