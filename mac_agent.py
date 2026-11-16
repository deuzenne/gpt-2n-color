#!/usr/bin/env python3
"""Compagnon Finder macOS 2N Color : sortie HTTP locale, aucune écoute réseau.

Il lit et applique les pastilles Finder pour l'application Docker/Colima,
sans supprimer, renommer ou convertir aucun média.
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import errno
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import sys
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parent
COLORS = {"orange": 1, "rouge": 2, "jaune": 3, "bleu": 4,
          "violet": 5, "vert": 6, "gris": 7, "aucune": 0}
EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi",
              ".wmv", ".ts", ".mts", ".m2ts"}

TAG_ATTRIBUTE = b"com.apple.metadata:_kMDItemUserTags"
FINDER_INFO_ATTRIBUTE = b"com.apple.FinderInfo"
# Indices de l’interface historiques, numéros des tags macOS et noms usuels.
TAG_COLORS = {
    1: (7, "Orange", "Orange"),
    2: (6, "Rouge", "Red"),
    3: (5, "Jaune", "Yellow"),
    4: (4, "Bleu", "Blue"),
    5: (3, "Violet", "Purple"),
    6: (2, "Vert", "Green"),
    7: (1, "Gris", "Gray"),
}
LABEL_BY_TAG_NUMBER = {number: label for label, (number, _, _) in TAG_COLORS.items()}
XATTR_NOFOLLOW = 0x0001


def load_env():
    values = {}
    for line in (PROJECT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("'\"")
    root = Path(values["MEDIA_ROOT"]).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"MEDIA_ROOT inaccessible sur le Mac : {root}")
    token = values["AGENT_TOKEN"]
    if len(token) < 32 or token == "GENERATED_ON_DEPLOY":
        raise RuntimeError("AGENT_TOKEN invalide")
    port = int(values.get("PORT", "8765"))
    if not 1 <= port <= 65535:
        raise RuntimeError("Port incorrect")
    return root, token, f"http://127.0.0.1:{port}"


def valid_file(root: Path, relative: str):
    if not isinstance(relative, str) or "\x00" in relative:
        raise ValueError("Chemin mal formé")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(x in {".", ".."} for x in relative.split("/")):
        raise ValueError("Chemin sortant de la bibliothèque")
    path = root.joinpath(*pure.parts)
    resolved = path.resolve(strict=True)
    if root not in resolved.parents or resolved.suffix.casefold() not in EXTENSIONS:
        raise ValueError("Fichier hors bibliothèque ou format non pris en charge")
    walk = root
    for part in pure.parts:
        walk /= part
        if walk.is_symlink():
            raise ValueError("Chemin symbolique refusé")
    if not resolved.is_file():
        raise ValueError("Fichier absent")
    return resolved


@lru_cache(maxsize=2)
def tag_xattr_function(name):
    if sys.platform != "darwin":
        raise RuntimeError("Les tags Finder natifs nécessitent macOS")
    function = getattr(ctypes.CDLL(None, use_errno=True), name)
    function.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
    function.restype = ctypes.c_ssize_t if name == "getxattr" else ctypes.c_int
    return function


def read_tag_attribute(path, attribute=TAG_ATTRIBUTE):
    getxattr = tag_xattr_function("getxattr")
    filename = os.fsencode(path)
    size = getxattr(filename, attribute, None, 0, 0, XATTR_NOFOLLOW)
    if size < 0:
        error = ctypes.get_errno()
        if error == errno.ENOATTR:
            return None
        raise OSError(error, os.strerror(error), path)
    buffer = ctypes.create_string_buffer(size)
    size = getxattr(filename, attribute, buffer, size, 0, XATTR_NOFOLLOW)
    if size < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)
    return buffer.raw[:size]


def write_tag_attribute(path, raw):
    setxattr = tag_xattr_function("setxattr")
    data = ctypes.create_string_buffer(raw)
    if setxattr(os.fsencode(path), TAG_ATTRIBUTE, data, len(raw), 0, XATTR_NOFOLLOW) < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), path)


def decode_tags(raw):
    if raw is None:
        return []
    try:
        tags = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, OverflowError, IndexError) as exc:
        raise ValueError("attribut des tags Finder illisible") from exc
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("format des tags Finder inconnu")
    return tags


def tag_label(tag):
    _, separator, number = tag.rpartition("\n")
    if not separator or number in {"", "0"}:
        return 0
    if len(number) == 1 and number in "1234567":
        return LABEL_BY_TAG_NUMBER[int(number)]
    raise ValueError("index de couleur Finder inconnu")


def label_from_native_tags(raw):
    colors = {tag_label(tag) for tag in decode_tags(raw)} - {0}
    if len(colors) > 1:
        raise ValueError("plusieurs couleurs dans les tags Finder")
    return next(iter(colors), 0)


def native_finder_reader():
    def read(path):
        label = label_from_native_tags(read_tag_attribute(path))
        if label:
            return label
        info = read_tag_attribute(path, FINDER_INFO_ATTRIBUTE)
        if info is None:
            return 0
        if len(info) != 32:
            raise ValueError("attribut FinderInfo de taille inattendue")
        number = (info[9] & 0x0E) >> 1  # Bits 1 à 3 des indicateurs Finder.
        return LABEL_BY_TAG_NUMBER.get(number, 0)

    return read


def finder_labels(paths: list[Path]) -> list[int | None]:
    if not paths:
        return []
    read = native_finder_reader()
    values = []
    for path in paths:
        try:
            values.append(read(path))
        except (OSError, ValueError) as exc:
            print(f"Pastille Finder illisible : {path} : {exc}", flush=True)
            values.append(None)
    return values


def apply_color(path: Path, index: int) -> int:
    if index not in range(8):
        raise ValueError("Indice couleur invalide")
    raw = read_tag_attribute(path)
    if label_from_native_tags(raw) == index:
        return index
    retained = []
    for tag in decode_tags(raw):
        label = tag_label(tag)
        if not label:
            retained.append(tag)
            continue
        _, french, english = TAG_COLORS[label]
        if tag.rpartition("\n")[0].casefold() not in {french.casefold(), english.casefold()}:
            raise ValueError("Un tag Finder coloré personnalisé est déjà présent")
    if index:
        number, name, _ = TAG_COLORS[index]
        retained.append(f"{name}\n{number}")
    write_tag_attribute(path, plistlib.dumps(retained, fmt=plistlib.FMT_BINARY))
    applied = label_from_native_tags(read_tag_attribute(path))
    if applied != index:
        raise RuntimeError(f"Finder a retourné {applied}, attendu {index}")
    return applied


def http_json(base, token, route, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + route, data=body, method="GET" if body is None else "POST",
        headers={"X-Agent-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.load(response)


def run_agent(root: Path, token: str, base: str):
    print(f"2N Color Agent macOS — {root} — {base}", flush=True)
    print("Lecture et écriture natives des tags Finder sur macOS.", flush=True)
    while True:
        try:
            job = http_json(base, token, "/api/agent/next")
        except (OSError, urllib.error.URLError, ValueError) as exc:
            print(f"API Colima indisponible : {exc}", flush=True)
            time.sleep(3)
            continue
        if not job.get("id"):
            time.sleep(0.45)
            continue
        jid = job["id"]
        print(f"Job {jid} : {job.get('kind')}", flush=True)
        try:
            if job["kind"] == "scan":
                names = job["files"]
                values: list[int | None] = []
                errors = 0
                for n in range(0, len(names), 24):
                    batch = names[n:n + 24]
                    local: list[Path | None] = []
                    for relative in batch:
                        try:
                            local.append(valid_file(root, relative))
                        except (OSError, ValueError) as exc:
                            print(f"Scan exclu : {relative} : {exc}", flush=True)
                            local.append(None)
                    positions = [i for i, p in enumerate(local) if p is not None]
                    labeled = finder_labels([local[i] for i in positions])
                    segment: list[int | None] = [None] * len(local)
                    for i, label in zip(positions, labeled):
                        segment[i] = label
                    values.extend(segment)
                    errors += sum(v is None for v in segment)
                    http_json(base, token, "/api/agent/progress",
                              {"job_id": jid, "done": len(values), "errors": errors})
                http_json(base, token, "/api/agent/result",
                          {"job_id": jid, "ok": True, "colors": values})
                print(f"Inventaire Finder : {len(values)} contrôlés, {errors} illisibles",
                      flush=True)
            elif job["kind"] == "set":
                path = valid_file(root, job["path"])
                verified = apply_color(path, int(job["label"]))
                http_json(base, token, "/api/agent/result",
                          {"job_id": jid, "ok": True, "verified": verified})
                print(f"Pastille Finder confirmée : {path.name} → {verified}", flush=True)
            else:
                raise RuntimeError("Type de job inconnu")
        except (OSError, ValueError, RuntimeError,
                urllib.error.URLError) as exc:
            print(f"Job {jid} EN ERREUR : {exc}", flush=True)
            try:
                http_json(base, token, "/api/agent/result",
                          {"job_id": jid, "ok": False, "error": str(exc)[:600]})
            except (OSError, urllib.error.URLError) as report_exc:
                print(f"Signalement de l'erreur impossible : {report_exc}", flush=True)


def main():
    if sys.platform != "darwin":
        raise SystemExit("Le compagnon Finder doit s'exécuter sur macOS, pas dans Colima.")
    root, token, base = load_env()
    try:
        run_agent(root, token, base)
    except KeyboardInterrupt:
        print("Agent arrêté.")


if __name__ == "__main__":
    main()
