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

"""Unit tests for visualization utilities, including resilient score extraction."""

from alpha_evolve.visualization import get_score


def test_get_score_valid():
    prog = {
        "evaluation": {
            "scores": {
                "scores": [
                    {"metric": "accuracy", "score": 0.95},
                    {"metric": "latency", "score": 12.3},
                ]
            }
        }
    }
    assert get_score(prog, "accuracy") == 0.95
    assert get_score(prog, "latency") == 12.3
    assert get_score(prog, "non_existent") == -float("inf")


def test_get_score_none_score():
    prog = {
        "evaluation": {
            "scores": {
                "scores": [
                    {"metric": "accuracy", "score": None},
                ]
            }
        }
    }
    assert get_score(prog, "accuracy") == -float("inf")


def test_get_score_attribute_error_resilience():
    # scores is a string instead of dict
    prog1 = {"evaluation": {"scores": "invalid_scores_string"}}
    assert get_score(prog1, "accuracy") == -float("inf")

    # element in scores list is a string instead of dict
    prog2 = {
        "evaluation": {
            "scores": {
                "scores": ["not_a_dict_element", None, 42]
            }
        }
    }
    assert get_score(prog2, "accuracy") == -float("inf")

    # evaluation is None
    prog3 = {"evaluation": None}
    assert get_score(prog3, "accuracy") == -float("inf")

    # empty program dict
    assert get_score({}, "accuracy") == -float("inf")


def test_get_score_value_error_resilience():
    prog = {
        "evaluation": {
            "scores": {
                "scores": [
                    {"metric": "accuracy", "score": "not_a_float"},
                ]
            }
        }
    }
    assert get_score(prog, "accuracy") == -float("inf")
