"""Tests macOS agent sans Finder ni AppleScript réel."""
import errno
import plistlib
import subprocess
from unittest.mock import patch

import pytest

import mac_agent as agent


def result(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["osascript"], returncode, stdout, stderr)


def test_mac_agent_confines_paths_and_accepts_ts_mts(tmp_path):
    a = tmp_path / "film.TS"
    b = tmp_path / "camera.mts"
    a.write_bytes(b"ts")
    b.write_bytes(b"mts")
    assert agent.valid_file(tmp_path, "film.TS") == a
    assert agent.valid_file(tmp_path, "camera.mts") == b
    with pytest.raises(ValueError):
        agent.valid_file(tmp_path, "../outside.mkv")
    with pytest.raises(ValueError):
        agent.valid_file(tmp_path, "/tmp/outside.mkv")
    (tmp_path / "wrong.txt").write_bytes(b"x")
    with pytest.raises(ValueError):
        agent.valid_file(tmp_path, "wrong.txt")
    (tmp_path / "symbolic.mts").symlink_to(b)
    with pytest.raises(ValueError):
        agent.valid_file(tmp_path, "symbolic.mts")


def test_finder_reads_native_labels_without_subprocess(tmp_path):
    paths = [tmp_path / name for name in ("a.mp4", "b.mkv", "c.MTS")]

    def read(path):
        if path == paths[2]:
            raise OSError(errno.EACCES, "Permission refusée")
        tags = ["Projet"] if path == paths[0] else ["Bleu\n4"]
        return agent.label_from_native_tags(plistlib.dumps(tags, fmt=plistlib.FMT_BINARY))

    with patch.object(agent, "native_finder_reader", return_value=read):
        with patch.object(agent.subprocess, "run") as run:
            assert agent.finder_labels(paths) == [0, 4, None]
    run.assert_not_called()


def test_conflicting_native_colors_are_unreadable():
    with pytest.raises(ValueError, match="plusieurs couleurs"):
        agent.label_from_native_tags(plistlib.dumps(["Bleu\n4", "Vert\n6"]))


def test_finder_busy_returns_error_not_silent_retry(tmp_path):
    path = tmp_path / "film.mkv"
    busy = result("", "Le Finder est en service (-15260)", 1)
    with patch.object(agent.subprocess, "run", return_value=busy) as run:
        with pytest.raises(RuntimeError, match="15260"):
            agent.apply_color(path, 3)
    run.assert_called_once()


def test_finder_write_confirms_returned_index(tmp_path):
    path = tmp_path / "film.mts"
    with patch.object(agent.subprocess, "run", return_value=result("5\n")) as run:
        assert agent.apply_color(path, 5) == 5
    run.assert_called_once()
    assert run.call_args.args[0][-2:] == [str(path), "5"]


def test_finder_mismatched_color_is_error(tmp_path):
    path = tmp_path / "film.mkv"
    with patch.object(agent.subprocess, "run", return_value=result("4\n")):
        with pytest.raises(RuntimeError, match="attendu 2"):
            agent.apply_color(path, 2)
