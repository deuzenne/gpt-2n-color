"""Tests de non-régression 2N Color : aucun Finder ni FFmpeg réel."""
from pathlib import Path
import os
import subprocess
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import main


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "ROOT", tmp_path)
    monkeypatch.setattr(main, "AGENT_TOKEN", "test-secret" * 5)
    with main.lock:
        main.sessions.clear()
        main.jobs.clear()
        main.queue.clear()
        main.last_agent_seen = 0
    return tmp_path, TestClient(main.app), {"X-Agent-Token": "test-secret" * 5}


def create_session(client, headers, name=""):
    response = client.post("/api/sessions", json={"directory": name, "recursive": True})
    assert response.status_code == 200, response.text
    sid = response.json()["id"]
    job = client.get("/api/agent/next", headers=headers).json()
    assert job["kind"] == "scan"
    return sid, job


def scan_result(client, headers, job, indexes):
    response = client.post("/api/agent/result", headers=headers, json={
        "job_id": job["id"], "ok": True, "colors": indexes})
    assert response.status_code == 200, response.text


def test_tree_navigation_and_path_safety(env):
    root, client, headers = env
    (root / "vacances").mkdir()
    (root / "_2n_convertis").mkdir()
    (root / "vacances" / "sous").mkdir()
    resp = client.get("/api/tree").json()
    names = {x["name"] for x in resp["directories"]}
    assert names == {"vacances", "_2n_convertis"}
    assert any(x["excluded"] for x in resp["directories"])
    assert client.get("/api/tree", params={"directory": "../"}).status_code == 400
    assert client.get("/api/tree", params={"directory": "/etc"}).status_code == 400
    assert client.get("/api/tree", params={"directory": "vacances"}).status_code == 200
    (root / "escape").symlink_to(root.parent)
    assert client.get("/api/tree", params={"directory": "escape"}).status_code == 403


def test_inventory_filters_archives_and_orders_by_size(env):
    root, client, headers = env
    (root / "petit.mp4").write_bytes(b"a" * 3)
    (root / "gros.MTS").write_bytes(b"b" * 33)
    (root / "medium.ts").write_bytes(b"c" * 12)
    (root / "texte.txt").write_bytes(b"x" * 500)
    (root / "_2n_convertis").mkdir()
    (root / "_2n_convertis" / "archive.mkv").write_bytes(b"x" * 900)
    (root / "_medias_vu").mkdir()
    (root / "_medias_vu" / "ignore.mp4").write_bytes(b"y" * 900)
    sid, job = create_session(client, headers)
    assert job["files"] == ["gros.MTS", "medium.ts", "petit.mp4"]
    scan_result(client, headers, job, [0, 4, 0])
    state = client.get("/api/sessions/" + sid).json()
    assert state["total_files"] == 3
    assert state["queue_size"] == 2
    assert state["current"]["name"] == "gros.MTS"
    assert state["remaining_bytes"] == 36


def test_color_waits_for_native_finder_confirmation_and_moves_next(env):
    root, client, headers = env
    big = root / "grand.mts"
    small = root / "petit.mp4"
    big.write_bytes(b"big" * 30)
    small.write_bytes(b"small")
    sid, job = create_session(client, headers)
    scan_result(client, headers, job, [0, 0])
    response = client.post("/api/sessions/" + sid + "/color", json={"color": "rouge"})
    assert response.status_code == 200
    assert response.json()["busy"] is True
    assert response.json()["current"]["name"] == big.name
    task = client.get("/api/agent/next", headers=headers).json()
    assert task["kind"] == "set"
    assert task["label"] == 2
    assert task["path"] == big.name
    client.post("/api/agent/result", headers=headers,
                json={"job_id": task["id"], "ok": True, "verified": 2})
    after = client.get("/api/sessions/" + sid).json()
    assert after["busy"] is False
    assert after["classified"] == 1
    assert after["current"]["name"] == small.name
    assert big.read_bytes() == b"big" * 30


def test_finder_error_does_not_advance_or_retry_implicitly(env):
    root, client, headers = env
    (root / "film.mkv").write_bytes(b"video")
    sid, job = create_session(client, headers)
    scan_result(client, headers, job, [0])
    client.post("/api/sessions/" + sid + "/color", json={"color": "bleu"})
    task = client.get("/api/agent/next", headers=headers).json()
    client.post("/api/agent/result", headers=headers, json={
        "job_id": task["id"], "ok": False, "error": "Finder is busy (-15260)"})
    s = client.get("/api/sessions/" + sid).json()
    assert s["current"]["path"] == "film.mkv"
    assert s["classified"] == 0
    assert s["queue_size"] == 1
    assert "15260" in s["error"]
    assert client.get("/api/agent/next", headers=headers).json()["job"] is None


def test_skip_undo_and_none_are_not_finder_writes(env):
    root, client, headers = env
    (root / "a.mp4").write_bytes(b"aa")
    (root / "b.mp4").write_bytes(b"b")
    sid, job = create_session(client, headers)
    scan_result(client, headers, job, [0, 0])
    skipped = client.post("/api/sessions/" + sid + "/skip").json()
    assert skipped["current"]["path"] == "b.mp4"
    assert skipped["skipped"] == 1
    restored = client.post("/api/sessions/" + sid + "/undo").json()
    assert restored["current"]["path"] == "a.mp4"
    assert restored["skipped"] == 0
    none = client.post("/api/sessions/" + sid + "/color",
                       json={"color": "aucune"}).json()
    assert none["current"]["path"] == "b.mp4"
    assert none["none"] == 1
    assert client.get("/api/agent/next", headers=headers).json()["job"] is None
    back = client.post("/api/sessions/" + sid + "/undo").json()
    assert back["current"]["path"] == "a.mp4"
    assert back["none"] == 0


def test_color_undo_asks_finder_to_reset_to_zero(env):
    root, client, headers = env
    (root / "a.mp4").write_bytes(b"source")
    sid, scan = create_session(client, headers)
    scan_result(client, headers, scan, [0])
    client.post("/api/sessions/" + sid + "/color", json={"color": "vert"})
    job = client.get("/api/agent/next", headers=headers).json()
    assert job["label"] == 6
    client.post("/api/agent/result", headers=headers,
                json={"job_id": job["id"], "verified": 6})
    assert client.get("/api/sessions/" + sid).json()["current"] is None
    undo = client.post("/api/sessions/" + sid + "/undo").json()
    assert undo["busy"] is True
    task = client.get("/api/agent/next", headers=headers).json()
    assert task["label"] == 0
    assert task["action"] == "undo"
    client.post("/api/agent/result", headers=headers,
                json={"job_id": task["id"], "verified": 0})
    state = client.get("/api/sessions/" + sid).json()
    assert state["classified"] == 0
    assert state["current"]["name"] == "a.mp4"


def test_non_readable_label_never_classified_as_uncolored(env):
    root, client, headers = env
    (root / "a.mts").write_bytes(b"data")
    sid, job = create_session(client, headers)
    scan_result(client, headers, job, [None])
    state = client.get("/api/sessions/" + sid).json()
    assert state["queue_size"] == 0
    assert state["scan_errors"] == 1
    assert "illisible" in state["error"]


def test_agent_cannot_run_without_token(env):
    root, client, headers = env
    assert client.get("/api/agent/next").status_code == 401
    assert client.get("/api/agent/next", headers={"X-Agent-Token": "wrong"}).status_code == 401


def test_video_range_and_path_escape(env):
    root, client, headers = env
    (root / "test.mp4").write_bytes(b"0123456789")
    response = client.get("/api/video", params={"path": "test.mp4"},
                          headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert client.get("/api/video", params={"path": "../etc/passwd"}).status_code == 400
    assert client.get("/api/video", params={"path": "no.txt"}).status_code == 404


def test_failed_scan_can_be_reported(env):
    root, client, headers = env
    (root / "bad.mkv").write_bytes(b"broken")
    sid, job = create_session(client, headers)
    result = client.post("/api/agent/result", headers=headers,
                         json={"job_id": job["id"], "ok": False,
                               "error": "Permission Finder absente"})
    assert result.status_code == 200
    state = client.get("/api/sessions/" + sid).json()
    assert state["status"] == "error"
    assert "Finder" in state["error"]
