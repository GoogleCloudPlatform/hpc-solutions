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

"""HTTP endpoint and template regression tests for tools/monitoring/ae_web_monitor.py."""

from http.server import ThreadingHTTPServer
import importlib.util
import json
import os
import pathlib
import threading
from unittest import mock
import urllib.error
import urllib.parse
import urllib.request

import pytest


def _load_web_monitor_module():
    """Loads tools/monitoring/ae_web_monitor.py if present in the checkout."""
    parents = pathlib.Path(__file__).parents
    if len(parents) <= 3:
        if os.environ.get("TEST_SRCDIR"):
            pytest.fail(f"Unexpected test path layout in runfiles: {__file__}")
        pytest.skip("tools/monitoring/ae_web_monitor.py is outside minimal image")
    module_path = parents[3] / "tools" / "monitoring" / "ae_web_monitor.py"
    if not module_path.is_file():
        if os.environ.get("TEST_SRCDIR"):
            pytest.fail(
                f"Expected monitoring entrypoint missing in runfiles: {module_path}"
            )
        pytest.skip("tools/monitoring/ae_web_monitor.py not present")

    spec = importlib.util.spec_from_file_location("ae_web_monitor_test_mod", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeMonitor:
    """In-memory fake AlphaEvolveMonitor for HTTP wiring tests."""

    def __init__(self):
        self.calls = []
        self.raise_on_next = None

    def _record(self, method_name, **kwargs):
        self.calls.append((method_name, kwargs))
        if self.raise_on_next is not None:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc

    def list_all_experiments(self, force_refresh=False):
        self._record("list_all_experiments", force_refresh=force_refresh)
        return [
            {
                "name": "projects/p/locations/global/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1",
                "state": "RUNNING",
                "config": {"title": "Demo Experiment"},
            }
        ]

    def get_experiment_status(
        self, experiment_name, max_programs=None, force_refresh=False
    ):
        self._record(
            "get_experiment_status",
            experiment_name=experiment_name,
            max_programs=max_programs,
            force_refresh=force_refresh,
        )
        return {
            "experiment_name": experiment_name,
            "state": "RUNNING",
            "main_metric": "score",
            "evaluated_count": 2,
        }

    def get_cached_experiment(self, exp_name, force_refresh=False):
        self._record(
            "get_cached_experiment",
            exp_name=exp_name,
            force_refresh=force_refresh,
        )
        return {"name": exp_name, "config": {"title": "Demo Experiment"}}

    def get_initial_program(self, raw_exp, force_refresh=False):
        self._record(
            "get_initial_program",
            raw_exp=raw_exp,
            force_refresh=force_refresh,
        )
        return {"name": "exp1/alphaEvolvePrograms/p0"}

    def list_programs(
        self,
        experiment_name,
        page_size=100,
        max_programs=None,
        force_refresh=False,
        unflatten_files=False,
    ):
        self._record(
            "list_programs",
            experiment_name=experiment_name,
            page_size=page_size,
            max_programs=max_programs,
            force_refresh=force_refresh,
            unflatten_files=unflatten_files,
        )
        return [{"name": "exp1/alphaEvolvePrograms/p0"}]

    def analyze_lineage(
        self, programs, target_metric=None, config=None, initial_prog=None
    ):
        self._record(
            "analyze_lineage",
            programs=programs,
            target_metric=target_metric,
            config=config,
            initial_prog=initial_prog,
        )
        return {
            "total_nodes": len(programs),
            "main_metric": target_metric or "score",
            "seed_programs": ["p0"],
        }

    def get_program_code(self, program_id, experiment_name):
        self._record(
            "get_program_code",
            program_id=program_id,
            experiment_name=experiment_name,
        )
        return {
            "program_id": program_id,
            "files": [{"path": "main.py", "content": "print('ok')"}],
        }

    def get_program_diff(self, program_id, base_program_id, experiment_name):
        self._record(
            "get_program_diff",
            program_id=program_id,
            base_program_id=base_program_id,
            experiment_name=experiment_name,
        )
        return {
            "program_id": program_id,
            "base_program_id": base_program_id,
            "files": [],
        }

    def explain_evolution(
        self, program_id, base_program_id, experiment_name, target_metric=None
    ):
        self._record(
            "explain_evolution",
            program_id=program_id,
            base_program_id=base_program_id,
            experiment_name=experiment_name,
            target_metric=target_metric,
        )
        return {
            "program_id": program_id,
            "base_program_id": base_program_id,
            "primary_metric": target_metric or "score",
            "overall_trend": "dropped",
            "score_deltas": {
                target_metric or "score": {
                    "base": 1.0,
                    "candidate": None,
                    "delta": None,
                    "percent_change": None,
                    "trend": "missing",
                    "higher_is_better": True,
                }
            },
            "explanation": "Evaluation failed before reporting score.",
            "cached": False,
        }


def _http_get(base_url, path):
    """Performs an HTTP GET request and returns (status, content_type, body)."""
    try:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as resp:
            return (
                resp.status,
                resp.headers.get("Content-Type", ""),
                resp.read().decode("utf-8"),
            )
    except urllib.error.HTTPError as err:
        return (
            err.code,
            err.headers.get("Content-Type", ""),
            err.read().decode("utf-8"),
        )


@pytest.fixture
def web_server(monkeypatch):
    """Starts an ephemeral local HTTP server wired to _FakeMonitor."""
    web_mod = _load_web_monitor_module()
    fake_monitor = _FakeMonitor()
    monkeypatch.setattr(web_mod, "_get_monitor", lambda: fake_monitor)

    class _QuietHandler(web_mod.AlphaEvolveHTTPRequestHandler):
        def log_message(self, format, *args):  # pylint: disable=redefined-builtin
            del format, args

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", fake_monitor
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_health_endpoint(web_server):
    base_url, _ = web_server

    status, content_type, body = _http_get(base_url, "/health")

    assert status == 200
    assert content_type == "application/json"
    assert json.loads(body) == {
        "status": "ok",
        "service": "alpha-evolve-monitor",
    }


def test_html_views_serve_expected_templates_and_regressions(web_server):
    base_url, _ = web_server

    idx_status, idx_ct, idx_html = _http_get(base_url, "/")
    assert idx_status == 200
    assert "text/html" in idx_ct
    # Refresh Directory must bypass the 8s TTL cache with ?refresh=1.
    assert 'onclick="loadExperiments(true)"' in idx_html
    assert "/api/experiments?refresh=1" in idx_html
    # State filter and REST API documentation checks.
    assert '<option value="PAUSED">' in idx_html
    assert '<option value="FAILED">' in idx_html
    assert "/api/explain_evolution" in idx_html

    st_status, st_ct, st_html = _http_get(base_url, "/status")
    assert st_status == 200
    assert "text/html" in st_ct
    assert "actualSpanT === 0" in st_html
    assert "refresh=1" in st_html

    lin_status, lin_ct, lin_html = _http_get(base_url, "/lineage")
    assert lin_status == 200
    assert "text/html" in lin_ct
    # vis-network CDN script must be pinned to an explicit version.
    assert "unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js" in lin_html
    assert "unpkg.com/vis-network/standalone/" not in lin_html
    # Failed candidates with missing primary metric must not fall into Score Steady.
    assert "s.trend === 'missing'" in lin_html
    assert "s.trend === 'new'" in lin_html
    # Markdown HTML sanitizer remains present.
    assert "function sanitizeRenderedHtml(" in lin_html


def test_api_experiments_honors_refresh_flag(web_server):
    base_url, fake_monitor = web_server

    status_default, _, body_default = _http_get(base_url, "/api/experiments")
    status_refresh, _, body_refresh = _http_get(
        base_url, "/api/experiments?refresh=1"
    )
    status_refresh_true, _, _ = _http_get(
        base_url, "/api/experiments?refresh=true"
    )

    assert status_default == 200
    assert status_refresh == 200
    assert status_refresh_true == 200
    assert len(json.loads(body_default)["experiments"]) == 1
    assert len(json.loads(body_refresh)["experiments"]) == 1
    assert fake_monitor.calls == [
        ("list_all_experiments", {"force_refresh": False}),
        ("list_all_experiments", {"force_refresh": True}),
        ("list_all_experiments", {"force_refresh": True}),
    ]


def test_api_status_validation_and_forwarding(web_server):
    base_url, fake_monitor = web_server

    bad_status, _, bad_body = _http_get(base_url, "/api/status")
    assert bad_status == 400
    assert "exp" in json.loads(bad_body)["error"]

    ok_status, _, ok_body = _http_get(
        base_url, "/api/status?exp=exp1&limit=25&refresh=1"
    )
    assert ok_status == 200
    assert json.loads(ok_body)["experiment_name"] == "exp1"
    assert fake_monitor.calls == [
        (
            "get_experiment_status",
            {
                "experiment_name": "exp1",
                "max_programs": 25,
                "force_refresh": True,
            },
        )
    ]


def test_api_lineage_validation_and_forwarding(web_server):
    base_url, fake_monitor = web_server

    bad_status, _, _ = _http_get(base_url, "/api/lineage")
    assert bad_status == 400

    ok_status, _, ok_body = _http_get(
        base_url, "/api/lineage?exp=exp1&metric=throughput&refresh=1"
    )
    assert ok_status == 200
    payload = json.loads(ok_body)
    assert payload["total_nodes"] == 1
    assert payload["main_metric"] == "throughput"
    assert [name for name, _ in fake_monitor.calls] == [
        "get_cached_experiment",
        "get_initial_program",
        "list_programs",
        "analyze_lineage",
    ]
    assert fake_monitor.calls[0][1]["force_refresh"] is True
    assert fake_monitor.calls[1][1]["force_refresh"] is True
    assert fake_monitor.calls[2][1]["force_refresh"] is True
    assert fake_monitor.calls[2][1]["unflatten_files"] is False
    assert fake_monitor.calls[3][1]["target_metric"] == "throughput"


def test_api_program_code_diff_and_explain_evolution(web_server):
    base_url, fake_monitor = web_server

    # Missing required params return 400.
    assert _http_get(base_url, "/api/program_code?exp=exp1")[0] == 400
    assert _http_get(base_url, "/api/program_diff?exp=exp1&prog=p1")[0] == 400
    assert _http_get(base_url, "/api/explain_evolution?exp=exp1&prog=p1")[0] == 400

    code_status, _, code_body = _http_get(
        base_url, "/api/program_code?exp=exp1&prog=p1"
    )
    assert code_status == 200
    assert json.loads(code_body)["program_id"] == "p1"

    diff_status, _, diff_body = _http_get(
        base_url, "/api/program_diff?exp=exp1&prog=p1&base=p0"
    )
    assert diff_status == 200
    assert json.loads(diff_body)["base_program_id"] == "p0"

    exp_status, _, exp_body = _http_get(
        base_url, "/api/explain_evolution?exp=exp1&prog=p2&base=p0&metric=score"
    )
    assert exp_status == 200
    exp_json = json.loads(exp_body)
    assert exp_json["overall_trend"] == "dropped"
    assert exp_json["score_deltas"]["score"]["trend"] == "missing"
    assert fake_monitor.calls[-1] == (
        "explain_evolution",
        {
            "program_id": "p2",
            "base_program_id": "p0",
            "experiment_name": "exp1",
            "target_metric": "score",
        },
    )


def test_unknown_path_and_internal_error_handling(web_server):
    base_url, fake_monitor = web_server

    not_found_status, _, not_found_body = _http_get(base_url, "/api/unknown")
    assert not_found_status == 404
    assert "Path not found" in json.loads(not_found_body)["error"]

    fake_monitor.raise_on_next = ValueError("Unknown metric 'bogus'")
    val_status, _, val_body = _http_get(
        base_url, "/api/explain_evolution?exp=exp1&prog=p2&base=p0&metric=bogus"
    )
    assert val_status == 400
    assert "Unknown metric" in json.loads(val_body)["error"]

    fake_monitor.raise_on_next = ValueError("Invalid program_id")
    code_val_status, _, code_val_body = _http_get(
        base_url, "/api/program_code?exp=exp1&prog=../p2"
    )
    assert code_val_status == 400
    assert "Invalid program_id" in json.loads(code_val_body)["error"]

    fake_monitor.raise_on_next = ValueError("Invalid experiment_name")
    lineage_val_status, _, lineage_val_body = _http_get(
        base_url, "/api/lineage?exp=../exp1"
    )
    assert lineage_val_status == 400
    assert "Invalid experiment_name" in json.loads(lineage_val_body)["error"]

    fake_monitor.raise_on_next = json.JSONDecodeError("corrupt json", "", 0)
    decode_status, _, _ = _http_get(base_url, "/api/status?exp=exp1")
    assert decode_status == 500

    fake_monitor.raise_on_next = RuntimeError("discovery backend unavailable")
    err_status, _, err_body = _http_get(base_url, "/api/experiments")
    assert err_status == 500
    assert "discovery backend unavailable" in json.loads(err_body)["error"]


def test_real_monitor_rejects_traversal_and_cross_experiment_with_http_400(monkeypatch):
    """End-to-end HTTP test verifying slash traversal returns 400 without RPCs."""
    from alpha_evolve import monitoring  # pylint: disable=g-import-not-at-top

    monkeypatch.delenv("_CLOUD_BUCKET_NAME", raising=False)
    web_mod = _load_web_monitor_module()
    monitor = monitoring.AlphaEvolveMonitor(project_id="test-project")
    mock_client = mock.MagicMock()
    monitor.client = mock_client
    monkeypatch.setattr(web_mod, "_get_monitor", lambda: monitor)

    class _QuietHandler(web_mod.AlphaEvolveHTTPRequestHandler):
        def log_message(self, format, *args):  # pylint: disable=redefined-builtin
            del format, args

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _QuietHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        exp1 = "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1"
        exp2_p2 = "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp2/alphaEvolvePrograms/p2"

        bad_queries = [
            f"/api/explain_evolution?exp={urllib.parse.quote(exp1, safe='')}&prog={urllib.parse.quote('../p2', safe='')}&base=p1",
            f"/api/explain_evolution?exp={urllib.parse.quote(exp1, safe='')}&prog={urllib.parse.quote(exp2_p2, safe='')}&base=p1",
            f"/api/explain_evolution?exp={urllib.parse.quote(exp1 + '/../../other', safe='')}&prog=p2&base=p1",
            f"/api/program_code?exp={urllib.parse.quote(exp1, safe='')}&prog={urllib.parse.quote(exp2_p2, safe='')}",
            f"/api/program_diff?exp={urllib.parse.quote(exp1, safe='')}&prog=p2&base={urllib.parse.quote('../p1', safe='')}",
            f"/api/status?exp={urllib.parse.quote(exp1 + '/../../other', safe='')}",
        ]
        for query in bad_queries:
            status, _, body = _http_get(base_url, query)
            assert status == 400, f"Expected 400 for {query}, got {status}: {body}"

        mock_client.get_alpha_evolve_program.assert_not_called()
        mock_client.get_alpha_evolve_experiment.assert_not_called()
        mock_client.list_alpha_evolve_programs.assert_not_called()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
