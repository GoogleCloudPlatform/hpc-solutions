#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Lightweight HTTP server entrypoint for AlphaEvolve Cloud Run Web Service.

Exposes REST endpoints and interactive HTML dashboards to query experiment
status, unflattened lineage graphs, and inspect candidate program source code.
Designed for serverless deployment on Google Cloud Run.
"""

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

project_root = Path(__file__).resolve().parents[2]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

gf_path = project_root / "google_framework"
if str(gf_path) not in sys.path:
    sys.path.insert(0, str(gf_path))

from alpha_evolve.monitoring import AlphaEvolveMonitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("AlphaEvolveCloudRun")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_MONITOR_LOCK = threading.Lock()
_CACHED_MONITOR: Optional[AlphaEvolveMonitor] = None


def _load_template(filename: str) -> str:
    """Loads HTML template file from the templates directory."""
    filepath = os.path.join(TEMPLATES_DIR, filename)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        logger.error("Failed to load template %s: %s", filepath, e)
        return f"<h1>500 Internal Error</h1><p>Could not load template {filename}: {e}</p>"


def _get_monitor() -> AlphaEvolveMonitor:
    """Retrieves cached AlphaEvolveMonitor instance (reusing GCS and client credentials)."""
    global _CACHED_MONITOR
    if _CACHED_MONITOR is None:
        with _MONITOR_LOCK:
            if _CACHED_MONITOR is None:
                project_id = os.environ.get("_PROJECT_ID")
                bucket_name = os.environ.get("_CLOUD_BUCKET_NAME")
                _CACHED_MONITOR = AlphaEvolveMonitor(
                    project_id=project_id,
                    bucket_name=bucket_name,
                )
    return _CACHED_MONITOR


class AlphaEvolveHTTPRequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler for Cloud Run AlphaEvolve web service."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # HTML Views
        if path == "/":
            self.send_html_response(200, _load_template("index.html"))
            return

        if path == "/lineage":
            self.send_html_response(200, _load_template("lineage.html"))
            return

        if path == "/status":
            self.send_html_response(200, _load_template("status.html"))
            return

        # Health Check
        if path == "/health":
            self.send_json_response(200, {"status": "ok", "service": "alpha-evolve-monitor"})
            return

        # REST APIs
        try:
            monitor = _get_monitor()

            if path == "/api/experiments":
                exps = monitor.list_all_experiments()
                self.send_json_response(200, {"experiments": exps})
                return

            if path == "/api/status":
                exp_name = params.get("exp", [None])[0]
                if not exp_name:
                    self.send_json_response(400, {"error": "Missing query parameter 'exp'"})
                    return
                limit_str = params.get("limit", [None])[0]
                max_progs = int(limit_str) if limit_str and limit_str.isdigit() else 100
                status = monitor.get_experiment_status(experiment_name=exp_name, max_programs=max_progs)
                self.send_json_response(200, status)
                return

            if path == "/api/lineage":
                exp_name = params.get("exp", [None])[0]
                if not exp_name:
                    self.send_json_response(400, {"error": "Missing query parameter 'exp'"})
                    return
                programs = monitor.list_programs(experiment_name=exp_name, page_size=100)
                lineage = monitor.analyze_lineage(programs)
                self.send_json_response(200, lineage)
                return

            if path == "/api/program_code":
                exp_name = params.get("exp", [None])[0]
                prog_id = params.get("prog", [None])[0]
                if not exp_name or not prog_id:
                    self.send_json_response(400, {"error": "Missing query parameter 'exp' or 'prog'"})
                    return
                code_info = monitor.get_program_code(program_id=prog_id, experiment_name=exp_name)
                self.send_json_response(200, code_info)
                return

            if path == "/api/program_diff":
                exp_name = params.get("exp", [None])[0]
                prog_id = params.get("prog", [None])[0]
                base_id = params.get("base", [None])[0]
                if not exp_name or not prog_id or not base_id:
                    self.send_json_response(400, {"error": "Missing query parameter 'exp', 'prog', or 'base'"})
                    return
                diff_info = monitor.get_program_diff(
                    program_id=prog_id,
                    base_program_id=base_id,
                    experiment_name=exp_name,
                )
                self.send_json_response(200, diff_info)
                return

            if path == "/api/explain_evolution":
                exp_name = params.get("exp", [None])[0]
                prog_id = params.get("prog", [None])[0]
                base_id = params.get("base", [None])[0]
                if not exp_name or not prog_id or not base_id:
                    self.send_json_response(400, {"error": "Missing query parameter 'exp', 'prog', or 'base'"})
                    return
                explanation_info = monitor.explain_evolution(
                    program_id=prog_id,
                    base_program_id=base_id,
                    experiment_name=exp_name,
                )
                self.send_json_response(200, explanation_info)
                return

            self.send_json_response(404, {"error": f"Path not found: {path}"})

        except Exception as e:
            logger.error("Error handling GET request: %s", e, exc_info=True)
            self.send_json_response(500, {"error": str(e)})

    def send_json_response(self, status_code: int, data: dict):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html_response(self, status_code: int, html: str):
        body = html.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get("PORT", 8080))
    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, AlphaEvolveHTTPRequestHandler)
    logger.info("AlphaEvolve Cloud Run Web Monitor listening on port %d...", port)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
