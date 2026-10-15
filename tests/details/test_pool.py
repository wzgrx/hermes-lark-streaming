from __future__ import annotations

import json
import time
from pathlib import Path

from hermes_lark_streaming.details.pool import pool_route

ROWS = [{"id": "go-a22", "label": "Go a22", "provider": "opencode-go", "key_env": "OPENCODE_GO_API_KEY"}]


def write(home: Path, entries: list[dict]) -> None:
    (home / "auth.json").write_text(json.dumps({"credential_pool": {"opencode-go": entries}}))


def entry(label: str, priority: int, *, source: str = "manual", **state) -> dict:
    return {"label": label, "priority": priority, "source": source, "access_token": "secret", **state}


def test_the_first_key_not_cooling_down_serves(tmp_path: Path) -> None:
    write(tmp_path, [entry("OPENCODE_GO_API_KEY", 1, source="env:OPENCODE_GO_API_KEY"), entry("Go 221", 0)])
    assert pool_route(tmp_path, "opencode-go", time.time(), ROWS) == ["Go 221"]


def test_a_key_benched_during_the_turn_comes_first(tmp_path: Path) -> None:
    now = time.time()
    write(tmp_path, [
        entry("Go 221", 0, last_status="exhausted", last_status_at=now - 5, last_error_reset_at=now + 3600),
        entry("OPENCODE_GO_API_KEY", 1, source="env:OPENCODE_GO_API_KEY"),
    ])
    assert pool_route(tmp_path, "opencode-go", now - 60, ROWS) == ["Go 221", "Go a22"]
    # benched before the turn started: just skipped
    assert pool_route(tmp_path, "opencode-go", now, ROWS) == ["Go a22"]


def test_an_elapsed_bench_serves_again(tmp_path: Path) -> None:
    now = time.time()
    write(tmp_path, [
        entry("Go 221", 0, last_status="exhausted", last_status_at=now - 7200, last_error_reset_at=now - 10),
        entry("OPENCODE_GO_API_KEY", 1, source="env:OPENCODE_GO_API_KEY"),
    ])
    assert pool_route(tmp_path, "opencode-go", now, ROWS) == ["Go 221"]


def test_unknown_labels_single_keys_and_missing_files_say_nothing(tmp_path: Path) -> None:
    assert pool_route(tmp_path, "opencode-go", time.time(), ROWS) == []
    write(tmp_path, [entry("Go 221", 0)])
    assert pool_route(tmp_path, "opencode-go", time.time(), ROWS) == []
    write(tmp_path, [entry("Go 221", 1), entry("OTHER_KEY", 0, source="env:OTHER_KEY")])
    assert pool_route(tmp_path, "opencode-go", time.time(), ROWS) == []
