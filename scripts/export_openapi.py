"""导出 OpenAPI schema（T-608 产出，供 T-700 契约 v1.0 定稿核对）。

不启动服务，直接由 FastAPI app 对象生成；写入 docs/tech/api/openapi.json。
运行：./.venvs/sr-app/Scripts/python.exe scripts/export_openapi.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

from app.main import app  # noqa: E402

OUT = ROOT / "docs" / "tech" / "api" / "openapi.json"
OUT.parent.mkdir(parents=True, exist_ok=True)
schema = app.openapi()
OUT.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已导出 {OUT}（{len(schema.get('paths', {}))} 条路径）")
