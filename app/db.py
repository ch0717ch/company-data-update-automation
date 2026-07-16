from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")

TRACKED_FIELDS = (
    "name",
    "industry",
    "item",
    "representative",
    "founded_year",
    "employee_count",
    "phone",
    "address",
    "email",
    "homepage",
    "business_area",
    "summary",
    "description",
    "product_service",
    "interest_category",
)

ALL_DATA_FIELDS = TRACKED_FIELDS + ("logo_url", "source_url")

FIELD_LABELS = {
    "name": "기업명",
    "industry": "업종",
    "item": "세부품목",
    "representative": "대표자",
    "founded_year": "설립연도",
    "employee_count": "직원 수",
    "phone": "전화번호",
    "address": "주소",
    "email": "이메일",
    "homepage": "홈페이지",
    "business_area": "사업영역",
    "summary": "기업 소개",
    "description": "상세 설명",
    "product_service": "제품·서비스",
    "interest_category": "관심분야",
}


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _compare_value(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


class CompanyDatabase:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS search_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    filters_json TEXT NOT NULL DEFAULT '{}',
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    source_result_count INTEGER NOT NULL DEFAULT 0,
                    synchronized_count INTEGER NOT NULL DEFAULT 0,
                    detail_failed_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    changed_count INTEGER NOT NULL DEFAULT 0,
                    unchanged_count INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                );

                CREATE TABLE IF NOT EXISTS companies (
                    source_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    industry TEXT,
                    item TEXT,
                    representative TEXT,
                    founded_year TEXT,
                    employee_count INTEGER,
                    phone TEXT,
                    address TEXT,
                    email TEXT,
                    homepage TEXT,
                    business_area TEXT,
                    summary TEXT,
                    description TEXT,
                    product_service TEXT,
                    interest_category TEXT,
                    logo_url TEXT,
                    source_url TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    last_changed_at TEXT,
                    last_sync_status TEXT NOT NULL DEFAULT 'new',
                    last_change_run_id INTEGER,
                    raw_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(last_change_run_id) REFERENCES search_runs(id)
                );

                CREATE TABLE IF NOT EXISTS company_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    run_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type IN ('created', 'updated')),
                    field_name TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    changed_at TEXT NOT NULL,
                    FOREIGN KEY(source_id) REFERENCES companies(source_id) ON DELETE CASCADE,
                    FOREIGN KEY(run_id) REFERENCES search_runs(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
                CREATE INDEX IF NOT EXISTS idx_companies_industry ON companies(industry);
                CREATE INDEX IF NOT EXISTS idx_companies_last_seen ON companies(last_seen_at DESC);
                CREATE INDEX IF NOT EXISTS idx_changes_source ON company_changes(source_id, id DESC);
                CREATE INDEX IF NOT EXISTS idx_changes_run ON company_changes(run_id);
                CREATE INDEX IF NOT EXISTS idx_runs_started ON search_runs(started_at DESC);
                """
            )

    def start_run(self, query: str, filters: dict[str, Any]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT INTO search_runs(query, filters_json, started_at) VALUES (?, ?, ?)",
                (query, _json(filters), now_kst()),
            )
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        source_result_count: int = 0,
        synchronized_count: int = 0,
        detail_failed_count: int = 0,
        new_count: int = 0,
        changed_count: int = 0,
        unchanged_count: int = 0,
        error_message: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE search_runs
                SET completed_at = ?, status = ?, source_result_count = ?, synchronized_count = ?,
                    detail_failed_count = ?, new_count = ?, changed_count = ?, unchanged_count = ?,
                    error_message = ?
                WHERE id = ?
                """,
                (
                    now_kst(),
                    status,
                    source_result_count,
                    synchronized_count,
                    detail_failed_count,
                    new_count,
                    changed_count,
                    unchanged_count,
                    error_message,
                    run_id,
                ),
            )

    def sync_companies(self, run_id: int, companies: list[dict[str, Any]]) -> dict[str, Any]:
        timestamp = now_kst()
        counts = {"new": 0, "changed": 0, "unchanged": 0}
        results: list[dict[str, Any]] = []

        with self.connect() as connection:
            for company in companies:
                source_id = str(company["source_id"])
                existing_row = connection.execute(
                    "SELECT * FROM companies WHERE source_id = ?", (source_id,)
                ).fetchone()
                available = set(company.get("available_fields") or ALL_DATA_FIELDS)
                available.update({"source_url"})
                raw_json = _json(company.get("raw") or {})

                if existing_row is None:
                    values = {field: company.get(field) if field in available else None for field in ALL_DATA_FIELDS}
                    values["name"] = values.get("name") or f"이름 없음 ({source_id})"
                    columns = ["source_id", *ALL_DATA_FIELDS, "first_seen_at", "last_seen_at", "last_sync_status", "raw_json"]
                    placeholders = ", ".join("?" for _ in columns)
                    connection.execute(
                        f"INSERT INTO companies ({', '.join(columns)}) VALUES ({placeholders})",
                        [
                            source_id,
                            *(values.get(field) for field in ALL_DATA_FIELDS),
                            timestamp,
                            timestamp,
                            "new",
                            raw_json,
                        ],
                    )
                    connection.execute(
                        """
                        INSERT INTO company_changes(source_id, run_id, event_type, changed_at)
                        VALUES (?, ?, 'created', ?)
                        """,
                        (source_id, run_id, timestamp),
                    )
                    counts["new"] += 1
                    results.append({"source_id": source_id, "status": "new", "changes": {}})
                    continue

                existing = dict(existing_row)
                changes: dict[str, dict[str, Any]] = {}
                updates: dict[str, Any] = {}
                for field in ALL_DATA_FIELDS:
                    if field not in available:
                        continue
                    new_value = company.get(field)
                    old_value = existing.get(field)
                    updates[field] = new_value
                    if field in TRACKED_FIELDS and _compare_value(old_value) != _compare_value(new_value):
                        changes[field] = {"old": old_value, "new": new_value}

                updates["last_seen_at"] = timestamp
                updates["raw_json"] = raw_json
                if changes:
                    updates["last_sync_status"] = "changed"
                    updates["last_changed_at"] = timestamp
                    updates["last_change_run_id"] = run_id
                    for field, change in changes.items():
                        connection.execute(
                            """
                            INSERT INTO company_changes(
                                source_id, run_id, event_type, field_name, old_value, new_value, changed_at
                            ) VALUES (?, ?, 'updated', ?, ?, ?, ?)
                            """,
                            (
                                source_id,
                                run_id,
                                field,
                                None if change["old"] is None else str(change["old"]),
                                None if change["new"] is None else str(change["new"]),
                                timestamp,
                            ),
                        )
                    counts["changed"] += 1
                    status = "changed"
                else:
                    updates["last_sync_status"] = "unchanged"
                    counts["unchanged"] += 1
                    status = "unchanged"

                assignments = ", ".join(f"{column} = ?" for column in updates)
                connection.execute(
                    f"UPDATE companies SET {assignments} WHERE source_id = ?",
                    [*updates.values(), source_id],
                )
                results.append({"source_id": source_id, "status": status, "changes": changes})

        return {**counts, "results": results}

    @staticmethod
    def _filters(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []

        q = str(filters.get("q") or "").strip()
        if q:
            like = f"%{q}%"
            clauses.append(
                "(name LIKE ? OR representative LIKE ? OR phone LIKE ? OR email LIKE ? OR address LIKE ? OR business_area LIKE ?)"
            )
            params.extend([like] * 6)

        for key, column in (("industry", "industry"),):
            value = str(filters.get(key) or "").strip()
            if value:
                clauses.append(f"{column} = ?")
                params.append(value)

        for key, column in (("representative", "representative"), ("address", "address")):
            value = str(filters.get(key) or "").strip()
            if value:
                clauses.append(f"{column} LIKE ?")
                params.append(f"%{value}%")

        numeric_filters = (
            ("founded_from", "founded_year", ">="),
            ("founded_to", "founded_year", "<="),
            ("employees_min", "employee_count", ">="),
            ("employees_max", "employee_count", "<="),
        )
        for key, column, operator in numeric_filters:
            value = filters.get(key)
            if value not in (None, ""):
                clauses.append(f"CAST(COALESCE({column}, 0) AS INTEGER) {operator} ?")
                params.append(int(value))

        status = str(filters.get("status") or "").strip()
        if status in {"new", "changed", "unchanged"}:
            clauses.append("last_sync_status = ?")
            params.append(status)

        if filters.get("changed_only"):
            clauses.append(
                "EXISTS (SELECT 1 FROM company_changes cc WHERE cc.source_id = companies.source_id AND cc.event_type = 'updated')"
            )

        return " AND ".join(clauses), params

    def list_companies(
        self,
        filters: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        offset: int = 0,
        include_last_changes: bool = True,
    ) -> dict[str, Any]:
        filters = filters or {}
        where, params = self._filters(filters)
        sort_value = str(filters.get("sort") or "name")
        descending = sort_value.startswith("-")
        sort_key = sort_value.lstrip("-")
        sort_columns = {
            "name": "name COLLATE NOCASE",
            "industry": "industry COLLATE NOCASE",
            "founded_year": "CAST(COALESCE(founded_year, 0) AS INTEGER)",
            "employee_count": "COALESCE(employee_count, 0)",
            "last_seen_at": "last_seen_at",
            "last_changed_at": "COALESCE(last_changed_at, '')",
        }
        order_by = sort_columns.get(sort_key, sort_columns["name"])
        direction = "DESC" if descending else "ASC"
        limit = max(1, min(int(limit), 100_000))
        offset = max(0, int(offset))

        with self.connect() as connection:
            total = int(
                connection.execute(f"SELECT COUNT(*) FROM companies WHERE {where}", params).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM companies WHERE {where} ORDER BY {order_by} {direction}, name ASC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            items = [dict(row) for row in rows]

            if include_last_changes and items:
                ids = [item["source_id"] for item in items if item.get("last_change_run_id")]
                run_ids = list({item["last_change_run_id"] for item in items if item.get("last_change_run_id")})
                if ids and run_ids:
                    id_marks = ",".join("?" for _ in ids)
                    run_marks = ",".join("?" for _ in run_ids)
                    change_rows = connection.execute(
                        f"""
                        SELECT * FROM company_changes
                        WHERE event_type = 'updated' AND source_id IN ({id_marks}) AND run_id IN ({run_marks})
                        ORDER BY id ASC
                        """,
                        [*ids, *run_ids],
                    ).fetchall()
                    by_id = {item["source_id"]: item for item in items}
                    for item in items:
                        item["last_changes"] = {}
                    for row in change_rows:
                        item = by_id.get(row["source_id"])
                        if item and item.get("last_change_run_id") == row["run_id"]:
                            item["last_changes"][row["field_name"]] = {
                                "old": row["old_value"],
                                "new": row["new_value"],
                                "changed_at": row["changed_at"],
                            }
            for item in items:
                item.pop("raw_json", None)
                item.setdefault("last_changes", {})

        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def get_company(self, source_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM companies WHERE source_id = ?", (source_id,)
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            item.pop("raw_json", None)
            return item

    def get_history(self, source_id: str, limit: int = 500) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT cc.*, sr.query
                FROM company_changes cc
                JOIN search_runs sr ON sr.id = cc.run_id
                WHERE cc.source_id = ?
                ORDER BY cc.id DESC
                LIMIT ?
                """,
                (source_id, max(1, min(limit, 5000))),
            ).fetchall()
            return [dict(row) for row in rows]

    def changes_for_companies(self, source_ids: list[str]) -> list[dict[str, Any]]:
        if not source_ids:
            return []
        with self.connect() as connection:
            marks = ",".join("?" for _ in source_ids)
            rows = connection.execute(
                f"""
                SELECT cc.*, c.name, sr.query
                FROM company_changes cc
                JOIN companies c ON c.source_id = cc.source_id
                JOIN search_runs sr ON sr.id = cc.run_id
                WHERE cc.source_id IN ({marks})
                ORDER BY cc.changed_at DESC, cc.id DESC
                """,
                source_ids,
            ).fetchall()
            return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connect() as connection:
            counts = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN last_sync_status = 'new' THEN 1 ELSE 0 END) AS current_new,
                    SUM(CASE WHEN last_sync_status = 'changed' THEN 1 ELSE 0 END) AS current_changed,
                    SUM(CASE WHEN EXISTS (
                        SELECT 1 FROM company_changes cc
                        WHERE cc.source_id = companies.source_id AND cc.event_type = 'updated'
                    ) THEN 1 ELSE 0 END) AS with_change_history
                FROM companies
                """
            ).fetchone()
            last_run = connection.execute(
                "SELECT * FROM search_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
            industries = connection.execute(
                """
                SELECT industry, COUNT(*) AS count
                FROM companies
                WHERE industry IS NOT NULL AND TRIM(industry) <> ''
                GROUP BY industry
                ORDER BY industry COLLATE NOCASE
                """
            ).fetchall()
            return {
                "total": counts["total"] or 0,
                "current_new": counts["current_new"] or 0,
                "current_changed": counts["current_changed"] or 0,
                "with_change_history": counts["with_change_history"] or 0,
                "last_run": dict(last_run) if last_run else None,
                "industries": [dict(row) for row in industries],
            }

    def checkpoint(self) -> dict[str, Any]:
        """Flush the WAL so a copied SQLite file contains the latest committed data."""
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(FULL)").fetchall()
        return {
            "path": str(self.path),
            "size": self.path.stat().st_size if self.path.exists() else 0,
        }
