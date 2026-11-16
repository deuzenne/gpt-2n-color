"""Tests de l’agent macOS sans dépendance à Finder."""
import errno
import plistlib
from unittest.mock import patch

import pytest

import mac_agent as agent


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
        assert agent.finder_labels(paths) == [0, 4, None]


def test_conflicting_native_colors_are_unreadable():
    with pytest.raises(ValueError, match="plusieurs couleurs"):
        agent.label_from_native_tags(plistlib.dumps(["Bleu\n4", "Vert\n6"]))


def test_native_red_round_trip_preserves_other_tags(tmp_path):
    path = tmp_path / "film.mkv"
    stored = plistlib.dumps(["Projet", "Archive\n0"])

    def read(_path, attribute=agent.TAG_ATTRIBUTE):
        return stored if attribute == agent.TAG_ATTRIBUTE else None

    def write(target, raw):
        nonlocal stored
        assert target == path
        stored = raw

    with patch.object(agent, "read_tag_attribute", side_effect=read), \
         patch.object(agent, "write_tag_attribute", side_effect=write):
        assert agent.apply_color(path, 2) == 2
        assert plistlib.loads(stored) == ["Projet", "Archive\n0", "Rouge\n6"]
        assert agent.label_from_native_tags(stored) == 2
        assert agent.apply_color(path, 0) == 0
        assert plistlib.loads(stored) == ["Projet", "Archive\n0"]


def test_native_write_preserves_custom_colored_tag(tmp_path):
    raw = plistlib.dumps(["Projet\n6"])
    with patch.object(agent, "read_tag_attribute", side_effect=lambda path, attr=agent.TAG_ATTRIBUTE: raw if attr == agent.TAG_ATTRIBUTE else None), \
         patch.object(agent, "write_tag_attribute", create=True) as write:
        with pytest.raises(ValueError, match="personnalisé"):
            agent.apply_color(tmp_path / "film.mkv", 4)
    write.assert_not_called()


def test_native_write_requires_verified_label(tmp_path):
    with patch.object(agent, "read_tag_attribute", return_value=None), \
         patch.object(agent, "write_tag_attribute", create=True):
        with pytest.raises(RuntimeError, match="attendu 2"):
            agent.apply_color(tmp_path / "film.mkv", 2)


@pytest.mark.parametrize("tags", [None, plistlib.dumps(["Projet"])])
def test_scan_recognizes_legacy_finder_color(tmp_path, monkeypatch, tags):
    raw = bytearray(32)
    raw[9] = 0x8D  # Rouge (0x0C), avec des indicateurs Finder indépendants.
    attrs = {b"com.apple.metadata:_kMDItemUserTags": tags,
             b"com.apple.FinderInfo": bytes(raw)}
    monkeypatch.setattr(agent, "read_tag_attribute",
                        lambda path, attr=agent.TAG_ATTRIBUTE: attrs[attr])
    assert agent.finder_labels([tmp_path / "ancien.mkv"]) == [2]


def test_scan_keeps_modern_color_when_present(tmp_path, monkeypatch):
    def read(_path, attr=agent.TAG_ATTRIBUTE):
        assert attr == agent.TAG_ATTRIBUTE
        return plistlib.dumps(["Vert\n2"])

    monkeypatch.setattr(agent, "read_tag_attribute", read)
    assert agent.finder_labels([tmp_path / "moderne.mkv"]) == [6]

@pytest.mark.parametrize("modern", [None, plistlib.dumps(["Rouge\n6", "Projet"])])
def test_clear_and_change_legacy_pastille_preserve_other_flags(tmp_path, monkeypatch, modern):
    info = bytearray(32)
    info[9] = 0x8D
    attrs = {agent.TAG_ATTRIBUTE: modern, agent.FINDER_INFO_ATTRIBUTE: bytes(info)}
    monkeypatch.setattr(agent, "read_tag_attribute",
                        lambda path, attr=agent.TAG_ATTRIBUTE: attrs[attr])
    monkeypatch.setattr(agent, "write_tag_attribute",
                        lambda path, raw, attr=agent.TAG_ATTRIBUTE: attrs.__setitem__(attr, raw))
    path = tmp_path / "ancien.mkv"
    assert agent.apply_color(path, 6) == 6
    assert attrs[agent.FINDER_INFO_ATTRIBUTE][9] == 0x85
    assert agent.apply_color(path, 0) == 0
    assert attrs[agent.FINDER_INFO_ATTRIBUTE][9] == 0x81
    if modern:
        assert plistlib.loads(attrs[agent.TAG_ATTRIBUTE]) == ["Projet"]
