import csv
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class DatabaseLogger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    user_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    query TEXT NOT NULL,
                    response TEXT NOT NULL,
                    from_cache INTEGER NOT NULL,
                    response_time_ms INTEGER NOT NULL
                )
                """
            )

    def log_interaction(
        self,
        query: str,
        response: str,
        source: str,
        user_id: str,
        from_cache: bool,
        response_time_ms: int,
    ) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                INSERT INTO interactions (
                    query,
                    response,
                    source,
                    user_id,
                    from_cache,
                    response_time_ms
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    query,
                    response,
                    source,
                    user_id,
                    int(from_cache),
                    response_time_ms,
                ),
            )

    def get_stats(self) -> dict[str, int | float]:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*),
                    COALESCE(SUM(CASE WHEN from_cache = 1 THEN 1 ELSE 0 END), 0),
                    COUNT(DISTINCT user_id),
                    COALESCE(AVG(response_time_ms), 0.0)
                FROM interactions
                """
            ).fetchone()

        if row is None:
            return {
                "total_interactions": 0,
                "cache_hits": 0,
                "unique_users": 0,
                "avg_response_time_ms": 0.0,
            }

        return {
            "total_interactions": int(row[0]),
            "cache_hits": int(row[1]),
            "unique_users": int(row[2]),
            "avg_response_time_ms": float(row[3]),
        }

    def export_to_csv(self) -> Path:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                """
                SELECT
                    id,
                    timestamp,
                    user_id,
                    source,
                    query,
                    response,
                    from_cache,
                    response_time_ms
                FROM interactions
                ORDER BY id
                """
            ).fetchall()

        export_dir = self.path.parent / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        export_path = export_dir / f"interactions_{timestamp}.csv"

        with export_path.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                [
                    "id",
                    "timestamp",
                    "user_id",
                    "source",
                    "query",
                    "response",
                    "from_cache",
                    "response_time_ms",
                ]
            )
            writer.writerows(rows)

        return export_path
