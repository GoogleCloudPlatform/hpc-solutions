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

"""Unit tests for AlphaEvolveMonitor lineage and program code inspection."""

import unittest
from unittest.mock import MagicMock, patch
from alpha_evolve.monitoring import AlphaEvolveMonitor


@patch("alpha_evolve.monitoring.load_config_from_bucket", return_value={})
class TestAlphaEvolveMonitor(unittest.TestCase):

    def test_analyze_lineage_with_node_details(self, mock_load_config):
        monitor = AlphaEvolveMonitor(project_id="test-project")

        sample_programs = [
            {
                "name": "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p0",
                "state": "COMPLETED",
                "createTime": "2026-07-10T10:00:00Z",
                "parentPrograms": [],
                "content": {"files": [{"path": "main.py", "content": "print('seed')"}]},
                "evaluation": {
                    "scores": {
                        "scores": [{"metric": "score", "score": 0.5}]
                    }
                },
            },
            {
                "name": "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p1",
                "state": "COMPLETED",
                "createTime": "2026-07-10T10:05:00Z",
                "parentPrograms": [
                    "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p0"
                ],
                "content": {"files": [{"path": "main.py", "content": "print('child')"}]},
                "evaluation": {
                    "scores": {
                        "scores": [{"metric": "score", "score": 0.8}]
                    }
                },
            },
            {
                "name": "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p2",
                "state": "EVALUATION_FAILED",
                "createTime": "2026-07-10T10:10:00Z",
                "parentPrograms": [
                    "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p0"
                ],
                "content": {"files": [{"path": "main.py", "content": "invalid code"}]},
                "evaluation": {
                    "insights": {
                        "insights": [{"label": "ERROR", "text": "SyntaxError: invalid syntax"}]
                    }
                },
            },
        ]

        lineage = monitor.analyze_lineage(sample_programs)

        self.assertEqual(lineage["total_nodes"], 3)
        self.assertEqual(lineage["seed_programs"], ["p0"])
        self.assertEqual(lineage["max_evolution_depth"], 1)
        self.assertEqual(lineage["parent_to_children"]["p0"], ["p1", "p2"])
        self.assertEqual(lineage["child_to_parents"]["p1"], ["p0"])

        # Verify node_details enrichment
        node_details = lineage["node_details"]
        self.assertIn("p0", node_details)
        self.assertEqual(node_details["p0"]["target_score"], 0.5)
        self.assertEqual(node_details["p0"]["children_ids"], ["p1", "p2"])

        self.assertIn("p1", node_details)
        self.assertEqual(node_details["p1"]["target_score"], 0.8)
        self.assertEqual(node_details["p1"]["depth"], 1)

        self.assertIn("p2", node_details)
        self.assertEqual(node_details["p2"]["state"], "EVALUATION_FAILED")

    def test_get_program_code(self, mock_load_config):
        monitor = AlphaEvolveMonitor(project_id="test-project")
        mock_client = MagicMock()
        mock_client.get_alpha_evolve_program.return_value = {
            "name": "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p1",
            "state": "COMPLETED",
            "createTime": "2026-07-10T10:05:00Z",
            "parentPrograms": [
                "projects/p/locations/l/collections/c/engines/e/sessions/s/alphaEvolveExperiments/exp1/alphaEvolvePrograms/p0"
            ],
            "content": {
                "files": [
                    {"path": "evaluator.py", "content": "def evaluate(): return {'score': 0.95}"}
                ]
            },
            "evaluation": {
                "scores": {
                    "scores": [{"metric": "score", "score": 0.95}]
                }
            },
        }
        monitor.client = mock_client
        res = monitor.get_program_code(program_id="p1", experiment_name="exp1")

        self.assertEqual(res["program_id"], "p1")
        self.assertEqual(res["state"], "COMPLETED")
        self.assertEqual(len(res["files"]), 1)
        self.assertEqual(res["files"][0]["path"], "evaluator.py")
        self.assertEqual(res["scores"]["score"], 0.95)


if __name__ == "__main__":
    unittest.main()

