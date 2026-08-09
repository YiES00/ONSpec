"""DB 연결·초기화. 데모=SQLite, 운영=PostgreSQL(db/schema.postgres.sql).

DATABASE_URL 환경변수가 postgresql:// 이면 psycopg를 사용하도록 확장하는 것이
운영 전환 지점이다(현재 데모는 SQLite 경로만 구현).
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "db" / "drone_cots.sqlite3"
SCHEMA_PATH = ROOT / "db" / "schema.sqlite.sql"
CATEGORIES_DIR = ROOT / "categories"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(fresh: bool = True) -> sqlite3.Connection:
    """스키마 적용 후 카테고리·사양 정의를 시드한다."""
    if fresh and DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    seed_categories(conn)
    conn.commit()
    return conn


def seed_categories(conn: sqlite3.Connection) -> None:
    for f in sorted(CATEGORIES_DIR.glob("*.json")):
        cat = json.loads(f.read_text(encoding="utf-8"))
        conn.execute(
            "INSERT OR IGNORE INTO categories (id, name_ko, name_en, phase) VALUES (?,?,?,?)",
            (cat["id"], cat["name_ko"], cat["name_en"], cat.get("phase", 1)),
        )
        for s in cat["specs"]:
            conn.execute(
                """INSERT OR IGNORE INTO spec_definitions
                   (category_id, spec_key, name_ko, unit, value_type,
                    requires_conditions, compare_default, sort_order)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    cat["id"], s["key"], s["name_ko"], s.get("unit"), s["type"],
                    1 if s.get("requires_conditions") else 0,
                    1 if s.get("compare_default") else 0,
                    s.get("sort", 100),
                ),
            )


def spec_def_map(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    rows = conn.execute("SELECT * FROM spec_definitions").fetchall()
    return {(r["category_id"], r["spec_key"]): r for r in rows}
