"""2N Color — serveur local Colima. Aucun accès d'écriture aux vidéos."""
from __future__ import annotations

from collections import deque
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import threading
import time

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ROOT = Path(os.environ.get("MEDIA_ROOT", "/library")).resolve()
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
DISPLAY_ROOT = PurePosixPath(os.environ.get("MEDIA_ROOT_DISPLAY", str(ROOT)))
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".avi",
                    ".wmv", ".ts", ".mts", ".m2ts"}
SKIP_DIRS = {"_2n_convertis", "_medias_vu"}
COLORS = {"orange": 1, "rouge": 2, "jaune": 3, "bleu": 4,
          "violet": 5, "vert": 6, "gris": 7, "aucune": 0}
VERSION = "0.1.18+build.0019"

app = FastAPI(title="2N Color", version=VERSION, docs_url=None, redoc_url=None)
lock = threading.RLock()
sessions: dict[str, dict] = {}
folder_scans: dict[str, dict] = {}
jobs: dict[str, dict] = {}
queue: deque[str] = deque()
last_agent_seen = 0.0


class SessionRequest(BaseModel):
    directory: str = ""
    recursive: bool = True


class LabelRequest(BaseModel):
    color: str


class AgentProgress(BaseModel):
    job_id: str
    done: int
    errors: int = 0


class AgentResult(BaseModel):
    job_id: str
    ok: bool = True
    colors: list[int | None] | None = None
    verified: int | None = None
    error: str | None = None


def resolve_path(relative: str, *, directory: bool | None = None) -> Path:
    if not isinstance(relative, str) or "\x00" in relative:
        raise HTTPException(400, "Chemin invalide")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(part in {".", ".."} for part in relative.split("/")):
        raise HTTPException(400, "Chemin relatif exigé")
    target = ROOT.joinpath(*pure.parts)
    try:
        canonical = target.resolve(strict=True)
        if canonical != ROOT and ROOT not in canonical.parents:
            raise HTTPException(403, "Hors bibliothèque")
        # Refuser tout lien dans le chemin, y compris un lien interne.
        walk = ROOT
        for part in pure.parts:
            walk /= part
            if walk.is_symlink():
                raise HTTPException(403, "Liens symboliques interdits")
        if directory is True and not canonical.is_dir():
            raise HTTPException(400, "Pas un dossier")
        if directory is False and not canonical.is_file():
            raise HTTPException(400, "Pas un fichier")
        return canonical
    except FileNotFoundError:
        raise HTTPException(404, "Fichier ou dossier absent")
    except OSError:
        raise HTTPException(400, "Chemin illisible")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()

def select_directory(path_text: str) -> Path:
    """Accepter un sous-chemin ou le chemin absolu macOS configuré.

    Une URL ne peut pas élargir le volume autorisé par Docker Compose :
    seuls les descendants de MEDIA_ROOT sont sélectionnables.
    """
    value = path_text.strip()
    if "\x00" in value:
        raise HTTPException(400, "Chemin invalide")
    if value.startswith("/"):
        requested = PurePosixPath(value)
        if ".." in requested.parts:
            raise HTTPException(400, "Le chemin ne doit pas contenir '..'")
        if requested == DISPLAY_ROOT:
            value = ""
        elif DISPLAY_ROOT in requested.parents:
            value = requested.relative_to(DISPLAY_ROOT).as_posix()
        elif requested == PurePosixPath(str(ROOT)):
            value = ""
        elif PurePosixPath(str(ROOT)) in requested.parents:
            value = requested.relative_to(PurePosixPath(str(ROOT))).as_posix()
        else:
            raise HTTPException(
                403, "Ce dossier est hors du volume partagé avec Colima. "
                     "Sur le Mac, relancer ./deploy.sh '/chemin/du/dossier' "
                     "pour changer la bibliothèque.")
    return resolve_path(value, directory=True)


def display_path(path: Path) -> str:
    relative = rel(path)
    return str(DISPLAY_ROOT / relative) if relative != "." else str(DISPLAY_ROOT)



def enumerate_videos(folder: Path, recursive: bool) -> list[dict]:
    results = []
    def add(path):
        if path.is_symlink() or path.suffix.casefold() not in VIDEO_EXTENSIONS:
            return
        try:
            if path.is_file():
                size = path.stat().st_size
                results.append({"path": rel(path), "name": path.name, "bytes": size})
        except OSError:
            pass

    if recursive:
        for current, subdirs, files in os.walk(folder, followlinks=False):
            base = Path(current)
            subdirs[:] = sorted(d for d in subdirs if d not in SKIP_DIRS
                                and not (base / d).is_symlink())
            for filename in files:
                add(base / filename)
                if len(results) > 25000:
                    raise HTTPException(413, "Plus de 25 000 vidéos : choisir un sous-dossier")
    else:
        for item in folder.iterdir():
            add(item)
    results.sort(key=lambda item: (-item["bytes"], item["path"].casefold()))
    return results


def create_job(kind: str, session_id: str, **payload) -> str:
    jid = secrets.token_urlsafe(12)
    jobs[jid] = {"id": jid, "kind": kind, "session_id": session_id,
                 "status": "queued", "created": time.time(), **payload}
    queue.append(jid)
    return jid


def get_session_or_404(session_id: str) -> dict:
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session expirée : relancer l'inventaire")
    return session


def require_agent(token: str | None) -> None:
    if not AGENT_TOKEN or not token or not hmac.compare_digest(token, AGENT_TOKEN):
        raise HTTPException(401, "Jeton agent incorrect")


@app.get("/api/health")
def health():
    with lock:
        age = time.time() - last_agent_seen
        return {"ok": True, "version": VERSION, "root": os.environ.get("MEDIA_ROOT_DISPLAY", str(ROOT)),
                "agent_online": age < 45, "agent_age": round(age, 1)
                if last_agent_seen else None}


@app.get("/api/tree")
def tree(directory: str = ""):
    folder = select_directory(directory)
    children = []
    videos = []
    total_entries = 0
    try:
        for entry in folder.iterdir():
            total_entries += 1
            if entry.is_symlink():
                continue
            if entry.is_dir():
                children.append({"name": entry.name, "path": rel(entry),
                                 "excluded": entry.name in SKIP_DIRS})
            elif entry.is_file() and entry.suffix.casefold() in VIDEO_EXTENSIONS:
                try:
                    videos.append({"name": entry.name, "bytes": entry.stat().st_size})
                except OSError:
                    pass
    except OSError:
        raise HTTPException(403, "Dossier inaccessible")
    children.sort(key=lambda v: v["name"].casefold())
    videos.sort(key=lambda v: (-v["bytes"], v["name"].casefold()))
    return {"path": rel(folder), "display_path": display_path(folder),
            "root_name": DISPLAY_ROOT.name, "directories": children,
            "video_count": len(videos), "video_samples": videos[:8],
            "total_entries": total_entries}


@app.post("/api/folder-counts")
def start_folder_counts(req: SessionRequest):
    folder = select_directory(req.directory)
    files = [video["path"] for video in enumerate_videos(folder, recursive=True)]
    if len(files) > 25000:
        raise HTTPException(413, "Plus de 25 000 vidéos : choisir un sous-dossier")
    sid = secrets.token_urlsafe(12)
    with lock:
        now = time.time()
        for key, scan in list(folder_scans.items()):
            if now - scan["updated"] > 120:
                folder_scans.pop(key)
                jobs.pop(scan.get("job_id"), None)
        if files and now - last_agent_seen >= 45:
            raise HTTPException(503, "Finder est déconnecté. Réessaie quand l’agent est connecté.")
        if len(folder_scans) >= 12:
            raise HTTPException(429, "Trop de comptages en cours. Réessaie dans un instant.")
        scan = {"status": "scanning" if files else "ready", "counts": {},
                "unreadable": {}, "updated": now, "total": len(files), "done": 0}
        folder_scans[sid] = scan
        if files:
            scan["job_id"] = create_job("scan", sid, files=files, folder_count=True)
    return {"id": sid}


@app.get("/api/folder-counts/{sid}")
def folder_counts(sid: str):
    with lock:
        scan = folder_scans.get(sid)
        if not scan:
            raise HTTPException(404, "Comptage expiré")
        if scan["status"] == "scanning" and time.time() - scan["updated"] >= 120:
            raise HTTPException(503, "Finder ne répond plus. Relance le filtre.")
        return {key: value for key, value in scan.items() if key not in {"updated", "job_id"}}


@app.delete("/api/folder-counts/{sid}")
def release_folder_counts(sid: str):
    with lock:
        scan = folder_scans.pop(sid, None)
        if scan:
            job = jobs.get(scan.get("job_id"))
            if job and job["status"] == "queued":
                jobs.pop(job["id"], None)
                queue.remove(job["id"])
    return {"ok": True}


@app.post("/api/sessions")
def start_session(req: SessionRequest):
    folder = select_directory(req.directory)
    with lock:
        if len(sessions) >= 6:
            # Conserver seulement les plus récentes sessions.
            oldest = min(sessions, key=lambda k: sessions[k]["created"])
            sessions.pop(oldest, None)
    videos = enumerate_videos(folder, req.recursive)
    sid = secrets.token_urlsafe(12)
    with lock:
        session = {"id": sid, "directory": rel(folder),
                   "recursive": req.recursive, "created": time.time(),
                   "total_files": len(videos), "total_bytes": sum(v["bytes"] for v in videos),
                   "candidates": videos, "pending": [], "history": [],
                   "tagged": 0, "skipped": 0, "none": 0,
                   "scan_done": 0, "scan_errors": 0,
                   "error": ("Aucune vidéo prise en charge trouvée dans ce dossier. "
                             "Si le Mac contient des fichiers ici, vérifier "
                             "le montage du volume dans Colima."
                             if not videos else None),
                   "colored_count": 0, "busy": False,
                   "status": "scanning" if videos else "ready"}
        sessions[sid] = session
        if videos:
            create_job("scan", sid, files=[item["path"] for item in videos])
    return {"id": sid}


def view(session: dict) -> dict:
    pending = session["pending"]
    reviewed = len(session["history"])
    return {"id": session["id"], "directory": session["directory"],
            "recursive": session["recursive"],
            "status": session["status"], "total_files": session["total_files"],
            "total_bytes": session["total_bytes"], "scan_done": session["scan_done"],
            "scan_errors": session["scan_errors"],
            "colored_count": session.get("colored_count", 0),
            "queue_size": len(pending),
            "remaining_bytes": sum(x["bytes"] for x in pending),
            "classified": session["tagged"], "skipped": session["skipped"],
            "none": session["none"], "reviewed": reviewed,
            "current": pending[0] if pending else None, "busy": session["busy"],
            "can_undo": bool(session["history"]) and not session["busy"],
            "error": session["error"]}


@app.get("/api/sessions/{sid}")
def session_state(sid: str):
    with lock:
        return view(get_session_or_404(sid))


@app.post("/api/sessions/{sid}/color")
def color_video(sid: str, req: LabelRequest):
    color = req.color.casefold().strip()
    if color not in COLORS:
        raise HTTPException(400, "Couleur inconnue")
    with lock:
        s = get_session_or_404(sid)
        if s["status"] != "ready" or s["busy"] or not s["pending"]:
            raise HTTPException(409, "Aucune vidéo prête à classer")
        s["error"] = None
        item = s["pending"][0]
        if color == "aucune":
            s["pending"].pop(0)
            s["none"] += 1
            s["history"].append({"kind": "none", "item": item})
        else:
            s["busy"] = True
            create_job("set", sid, path=item["path"], label=COLORS[color],
                       action="color", color=color)
        return view(s)


@app.post("/api/sessions/{sid}/skip")
def skip_video(sid: str):
    with lock:
        s = get_session_or_404(sid)
        if s["status"] != "ready" or s["busy"] or not s["pending"]:
            raise HTTPException(409, "Aucune vidéo prête")
        s["error"] = None
        item = s["pending"].pop(0)
        s["skipped"] += 1
        s["history"].append({"kind": "skip", "item": item})
        return view(s)


@app.post("/api/sessions/{sid}/undo")
def undo_video(sid: str):
    with lock:
        s = get_session_or_404(sid)
        if s["status"] != "ready" or s["busy"] or not s["history"]:
            raise HTTPException(409, "Rien à annuler")
        s["error"] = None
        prev = s["history"][-1]
        if prev["kind"] in {"skip", "none"}:
            s["history"].pop()
            s["pending"].insert(0, prev["item"])
            s["skipped" if prev["kind"] == "skip" else "none"] -= 1
        else:
            s["busy"] = True
            create_job("set", sid, path=prev["item"]["path"], label=0,
                       action="undo", color=prev["color"])
        return view(s)


@app.get("/api/agent/next")
def agent_next(x_agent_token: str | None = Header(default=None)):
    global last_agent_seen
    require_agent(x_agent_token)
    with lock:
        last_agent_seen = time.time()
        while queue:
            jid = queue.popleft()
            job = jobs.get(jid)
            if not job or job["status"] != "queued":
                continue
            owners = folder_scans if job.get("folder_count") else sessions
            if job["session_id"] not in owners:
                jobs.pop(jid, None)
                continue
            job["status"] = "running"
            return {key: value for key, value in job.items()
                    if key not in {"created", "status"}}
    return {"job": None}


@app.post("/api/agent/progress")
def agent_progress(data: AgentProgress, x_agent_token: str | None = Header(default=None)):
    global last_agent_seen
    require_agent(x_agent_token)
    with lock:
        last_agent_seen = time.time()
        job = jobs.get(data.job_id)
        if not job or job["status"] != "running" or job["kind"] != "scan":
            raise HTTPException(404, "Scan inconnu")
        if job.get("folder_count"):
            scan = folder_scans.get(job["session_id"])
            if scan:
                scan["done"] = min(len(job["files"]), max(0, data.done))
                scan["updated"] = time.time()
            return {"ok": True}
        s = sessions.get(job["session_id"])
        if s:
            s["scan_done"] = min(len(job["files"]), max(0, data.done))
            s["scan_errors"] = max(0, data.errors)
    return {"ok": True}


@app.post("/api/agent/result")
def agent_result(data: AgentResult, x_agent_token: str | None = Header(default=None)):
    global last_agent_seen
    require_agent(x_agent_token)
    with lock:
        last_agent_seen = time.time()
        job = jobs.get(data.job_id)
        if not job or job["status"] != "running":
            raise HTTPException(404, "Opération inconnue")
        job["status"] = "completed"
        if job.get("folder_count"):
            scan = folder_scans.get(job["session_id"])
            if scan:
                scan["updated"] = time.time()
                if data.ok and data.colors is not None and len(data.colors) == len(job["files"]):
                    for path, color in zip(job["files"], data.colors):
                        child = str(PurePosixPath(path).parent)
                        if color == 0:
                            scan["counts"][child] = scan["counts"].get(child, 0) + 1
                        elif color is None:
                            scan["unreadable"][child] = scan["unreadable"].get(child, 0) + 1
                    scan["done"] = len(job["files"])
                    scan["status"] = "ready"
                else:
                    scan["status"] = "error"
                    scan["error"] = data.error or "Lecture Finder incomplète"
            jobs.pop(data.job_id, None)
            return {"ok": True}
        s = sessions.get(job["session_id"])
        if not s:
            jobs.pop(data.job_id, None)
            return {"ok": True}
        if job["kind"] == "scan":
            if data.ok and data.colors is not None and len(data.colors) == len(job["files"]):
                uncolored = {p for p, label in zip(job["files"], data.colors) if label == 0}
                s["pending"] = [item for item in s["candidates"]
                                if item["path"] in uncolored]
                s["colored_count"] = sum(v in range(1, 8) for v in data.colors
                                         if v is not None)
                s["scan_done"] = len(job["files"])
                s["scan_errors"] = sum(1 for v in data.colors if v is None)
                s["status"] = "ready"
                s["error"] = (f"{s['scan_errors']} pastille(s) Finder illisible(s) : "
                              "fichiers exclus par sécurité"
                              if s["scan_errors"] else
                              (f"{s['colored_count']} vidéos détectées, mais déjà toutes "
                               "colorées dans Finder." if not s["pending"] else None))
            else:
                s["status"] = "error"
                s["error"] = data.error or "Lecture Finder incomplète"
        elif job["kind"] == "set":
            s["busy"] = False
            if data.ok and data.verified == job["label"]:
                if job["action"] == "color":
                    if s["pending"] and s["pending"][0]["path"] == job["path"]:
                        item = s["pending"].pop(0)
                        s["history"].append({"kind": "color", "item": item,
                                             "color": job["color"]})
                        s["tagged"] += 1
                else:
                    if s["history"] and s["history"][-1]["item"]["path"] == job["path"]:
                        prev = s["history"].pop()
                        s["pending"].insert(0, prev["item"])
                        s["tagged"] -= 1
            else:
                s["error"] = data.error or "Finder n'a pas confirmé la pastille"
        jobs.pop(data.job_id, None)
    return {"ok": True}


@app.get("/api/probe")
def probe_video(path: str = Query(...)):
    target = resolve_path(path, directory=False)
    if target.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise HTTPException(415, "Pas un format vidéo")
    try:
        output = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height",
             "-of", "json", str(target)],
            capture_output=True, text=True, timeout=15, check=True)
        data = json.loads(output.stdout)
        video = next((v for v in data.get("streams", [])
                      if v.get("codec_type") == "video"), {})
        return {"duration": round(float(data.get("format", {}).get("duration", 0)), 1),
                "codec": video.get("codec_name", "?"),
                "width": video.get("width"), "height": video.get("height")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {"duration": 0, "codec": "?", "width": None, "height": None}


@app.get("/api/video")
def original_video(path: str = Query(...)):
    target = resolve_path(path, directory=False)
    if target.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise HTTPException(415, "Extension non vidéo")
    return FileResponse(target, media_type="video/mp4" if target.suffix.casefold()
                        in {".mp4", ".m4v", ".mov"} else "application/octet-stream",
                        headers={"Cache-Control": "no-store"})


@app.get("/api/preview")
def preview_video(path: str = Query(...), start: float = Query(default=0, ge=0, le=864000)):
    target = resolve_path(path, directory=False)
    if target.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise HTTPException(415, "Extension non vidéo")
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
               "-ss", str(start), "-i", str(target),
               "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
               "-movflags", "frag_keyframe+empty_moov+default_base_moof",
               "-f", "mp4", "pipe:1"]

    def chunks():
        proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, bufsize=0)
        try:
            while True:
                block = proc.stdout.read(128 * 1024)
                if not block:
                    break
                yield block
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            proc.stdout.close()

    return StreamingResponse(chunks(), media_type="video/mp4",
                             headers={"Cache-Control": "no-store",
                                      "X-Content-Type-Options": "nosniff"})


app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"),
                           html=True), name="web")
