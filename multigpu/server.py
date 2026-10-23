"""Loopback-only dashboard. No external accounts or billed jobs are created on startup."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .service import Service


def make_server(service, port=8765):
    from .environment import EnvironmentSetup
    environment = EnvironmentSetup(service)
    token = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "MultiGPU/0.3"

        def log_message(self, format, *args):
            pass

        def send(self, status, data, content_type="application/json", extra=None):
            body = json.dumps(data, allow_nan=False).encode() if content_type == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def guard(self, mutation=False):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if self.headers.get("Host") not in allowed:
                raise PermissionError("Use the local dashboard address.")
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://" + host for host in allowed}:
                raise PermissionError("Cross-origin requests are not permitted.")
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise PermissionError("Cross-site requests are not permitted.")
            if mutation and not secrets.compare_digest(self.headers.get("X-MultiGPU-Token", ""), token):
                raise PermissionError("Refresh the dashboard to authenticate this local session.")

        def do_GET(self):
            try:
                self.guard()
                url = urlsplit(self.path)
                parts = url.path.strip("/").split("/")
                if url.path == "/api/bootstrap":
                    return self.send(200, service.bootstrap(), extra={"X-MultiGPU-Token": token})
                if url.path == "/api/hardware":
                    return self.send(200, service.hardware)
                if url.path == "/api/draft":
                    return self.send(200, {"draft": service.draft()})
                if len(parts) == 4 and parts[:2] == ["api", "providers"] and parts[3] == "setup":
                    return self.send(200, service.provider_setup(parts[2]))
                if parts[:3] == ["api", "environment", "tasks"] and len(parts) == 4:
                    return self.send(200, environment.get(parts[3]))
                if url.path == "/api/jobs":
                    return self.send(200, service.jobs())
                if len(parts) >= 3 and parts[:2] == ["api", "jobs"]:
                    jid = parts[2]
                    if len(parts) == 3:
                        job = service.job(jid, True)
                        job["logs"] = service.logs(jid)
                        return self.send(200, job)
                    if len(parts) == 4 and parts[3] == "setup":
                        return self.send(200, service.job_setup(jid))
                    if len(parts) == 4 and parts[3] in ("notebook", "notebook-payload"):
                        notebook = parts[3] == "notebook"
                        file = service.notebook(jid) if notebook else service.notebook_payload(jid)
                        filename = "training.ipynb" if notebook else "training-payload.zip"
                        content_type = "application/x-ipynb+json" if notebook else "application/zip"
                        return self.send(200, Path(file).read_bytes(), content_type,
                                         {"Content-Disposition": f'attachment; filename="{filename}"'})
                    if len(parts) == 4 and parts[3] in ("bundle", "artifacts"):
                        file = service.bundle(jid, parse_qs(url.query).get("provider", [None])[0]) if parts[3] == "bundle" else service.artifact_zip(jid)
                        return self.send(200, Path(file).read_bytes(), "application/zip", {"Content-Disposition": f'attachment; filename="multigpu-{parts[3]}-{jid[:8]}.zip"'})
                if url.path.startswith("/api/"):
                    return self.send(404, {"error": "Endpoint not found."})
                relative = url.path.removeprefix("/static/") if url.path.startswith("/static/") else url.path.lstrip("/")
                file = (static / (relative or "index.html")).resolve()
                if not file.is_relative_to(static.resolve()) or not file.is_file():
                    return self.send(404, {"error": "File not found."})
                content_type = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                if file.suffix == ".js":
                    content_type = "text/javascript"
                return self.send(200, file.read_bytes(), content_type)
            except Exception as exc:
                self.error(exc)

        def do_POST(self):
            try:
                self.guard(mutation=True)
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Send JSON data.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("JSON body must be between 1 byte and 1 MB.")
                raw = json.loads(self.rfile.read(length))
                if not isinstance(raw, dict):
                    raise ValueError("Send a JSON object.")
                parts = urlsplit(self.path).path.strip("/").split("/")
                if parts == ["api", "settings"]:
                    return self.send(200, service.save_settings(raw))
                if parts == ["api", "draft"]:
                    return self.send(200, {"draft": service.save_draft(raw)})
                if parts == ["api", "cache", "cleanup"]:
                    return self.send(200, service.clean_cache())
                if parts == ["api", "datasets", "inspect"]:
                    from .datasets import inspect_dataset
                    return self.send(200, inspect_dataset(raw))
                if parts == ["api", "hardware", "recheck"]:
                    from .service import hardware_info
                    service.hardware = hardware_info()
                    return self.send(200, service.hardware)
                if parts == ["api", "environment", "setup"]:
                    return self.send(202, environment.start(raw.get("profile")))
                if parts == ["api", "desktop", "select-path"]:
                    from .desktop import select_path
                    return self.send(200, select_path(raw.get("kind", "file"), raw.get("purpose", "dataset")))
                if parts == ["api", "shutdown"]:
                    if getattr(service, "environment_busy", False):
                        raise ValueError("Environment setup is still running. Wait for it to finish before quitting.")
                    self.send(200, {"stopping": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if parts == ["api", "jobs", "preview"]:
                    return self.send(200, service.plan(raw))
                if parts == ["api", "jobs"]:
                    return self.send(201, service.create(raw, require_preview=True))
                if len(parts) == 4 and parts[:2] == ["api", "providers"] and parts[3] == "probe":
                    return self.send(200, service.probe(parts[2], raw))
                if len(parts) == 4 and parts[:2] == ["api", "providers"] and parts[3] == "setup":
                    return self.send(200, service.provider_setup(parts[2], raw))
                if len(parts) == 4 and parts[:2] == ["api", "jobs"]:
                    if parts[3] == "setup":
                        return self.send(200, service.job_setup(parts[2], raw))
                    if parts[3] == "import":
                        return self.send(200, service.import_output(parts[2], raw))
                    if parts[3] == "resume":
                        return self.send(200, service.resume(parts[2], raw.get("providers")))
                    if parts[3] == "export":
                        return self.send(200, service.export(parts[2], raw.get("output_dir")))
                    if parts[3] == "recover":
                        return self.send(200, service.recover(parts[2], raw))
                    if parts[3] == "archive":
                        return self.send(200, service.archive(parts[2], raw.get("archived", True)))
                    if parts[3] == "predict":
                        return self.send(200, service.predict(parts[2], raw))
                    if parts[3] == "open-export":
                        from .desktop import open_export
                        job = service.job(parts[2])
                        if not job.get("export_path"):
                            raise ValueError("Export the model before opening its folder.")
                        return self.send(200, open_export(job["export_path"]))
                    actions = {"pause": service.pause, "reconcile": service.reconcile, "cancel": service.cancel}
                    if parts[3] in actions:
                        return self.send(200, actions[parts[3]](parts[2]))
                self.send(404, {"error": "Endpoint not found."})
            except Exception as exc:
                self.error(exc)

        def error(self, exc):
            status = 403 if isinstance(exc, PermissionError) else 404 if isinstance(exc, KeyError) else 400 if isinstance(exc, (ValueError, TypeError)) else 500
            self.send(status, {"error": str(exc)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Train with your own compute accounts, from a local dashboard.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("MULTIGPU_HOME", ".multigpu")))
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--connection-file", type=Path, help="Write the selected loopback address for the desktop launcher")
    args = parser.parse_args(argv)
    service = Service(args.data_dir)
    server = make_server(service, args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    if args.connection_file:
        from .artifacts import atomic_json
        atomic_json(args.connection_file.resolve(), {"url": url, "pid": os.getpid(), "workspace_dir":str(service.root)})
    print(f"MultiGPU is running at {url}\nState and training runs: {service.root}", flush=True)
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Saving progress and stopping local workers...", flush=True)
    finally:
        service.shutdown()
        server.server_close()
        service.store.close()
        if args.connection_file and args.connection_file.exists():
            try:
                current = json.loads(args.connection_file.read_text(encoding="utf-8"))
                if current.get("pid") == os.getpid():
                    args.connection_file.unlink()
            except (OSError, ValueError):
                pass
