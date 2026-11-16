#!/usr/bin/env python3
"""Compagnon Finder macOS 2N Color : sortie HTTPS locale, aucune écoute réseau.

Il lit et applique les pastilles Finder pour l'application Docker/Colima,
sans supprimer, renommer ou convertir aucun média. AUCUN retry AppleScript
sur Finder occupé (-15260) : erreur remontée au navigateur.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parent
COLORS = {"orange": 1, "rouge": 2, "jaune": 3, "bleu": 4,
          "violet": 5, "vert": 6, "gris": 7, "aucune": 0}
EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi",
              ".wmv", ".ts", ".mts", ".m2ts"}

WRITE_AND_VERIFY = r'''
on run argv
  set pathText to item 1 of argv
  set expectedIndex to item 2 of argv as integer
  tell application "Finder"
    set theFile to (POSIX file pathText as alias)
    set label index of theFile to expectedIndex
    set finalIndex to label index of theFile
  end tell
  return finalIndex as text
end run
'''


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


# Lecture native reprise de 2n_media_convert V2, avec retour en indices Finder.
def native_finder_reader():
    """Ouvre le lecteur des tags Finder modernes de macOS, sans sous-processus."""
    if sys.platform != "darwin":
        raise RuntimeError("Le lecteur Finder natif nécessite macOS")
    getxattr = ctypes.CDLL(None, use_errno=True).getxattr
    getxattr.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                         ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
    getxattr.restype = ctypes.c_ssize_t
    attr = b"com.apple.metadata:_kMDItemUserTags"

    def read(path):
        filename = os.fsencode(path)
        size = getxattr(filename, attr, None, 0, 0, 0)
        if size < 0:
            error = ctypes.get_errno()
            if error == errno.ENOATTR:
                return 0
            raise OSError(error, os.strerror(error), path)
        if not size:
            raise ValueError("attribut des tags Finder vide")
        buffer = ctypes.create_string_buffer(size)
        size = getxattr(filename, attr, buffer, size, 0, 0)
        if size < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        return label_from_native_tags(buffer.raw[:size])

    return read


def label_from_native_tags(raw):
    try:
        tags = plistlib.loads(raw)
    except (plistlib.InvalidFileException, ValueError, OverflowError, IndexError) as exc:
        raise ValueError("attribut des tags Finder illisible") from exc
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError("format des tags Finder inconnu")
    colors = set()
    for tag in tags:
        _, separator, number = tag.rpartition("\n")
        if separator and number:
            if number in "1234567" and len(number) == 1:
                colors.add(int(number))
            elif number != "0":
                raise ValueError("index de couleur Finder inconnu")
    if len(colors) > 1:
        raise ValueError("plusieurs couleurs dans les tags Finder")
    return next(iter(colors), 0)



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
    result = subprocess.run(
        ["osascript", "-e", WRITE_AND_VERIFY, str(path), str(index)],
        capture_output=True, text=True, check=False, timeout=20)
    if result.returncode:
        raise RuntimeError(
            f"Finder n'a pas accepté la pastille : {result.stderr.strip()[:500]}")
    try:
        applied = int(result.stdout.strip())
    except ValueError as exc:
        raise RuntimeError("Réponse Finder invalide") from exc
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
    print("Lecture Finder native (getxattr) sur macOS.", flush=True)
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
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
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
