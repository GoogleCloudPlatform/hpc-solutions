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

import os
import sys

# Ensure google_framework and alpha_evolve are on sys.path for test resolution
tests_dir = os.path.dirname(os.path.abspath(__file__))
alpha_evolve_dir = os.path.abspath(os.path.join(tests_dir, ".."))
google_framework_dir = os.path.abspath(os.path.join(alpha_evolve_dir, ".."))

for path in (google_framework_dir, alpha_evolve_dir):
  if path not in sys.path:
    sys.path.insert(0, path)

from unittest.mock import MagicMock
import google.auth
import google.cloud.batch_v1
import google.cloud.storage
import pytest


@pytest.fixture(autouse=True)
def default_test_env(monkeypatch):
    """Provides a default set of valid environment variables and mock ADC for all unit tests."""
    monkeypatch.setenv("_PROJECT_ID", "test-project-123")
    monkeypatch.setenv("_CLOUD_BUCKET_NAME", "test-bucket-alpha")
    monkeypatch.setenv("_USER_EXPERIMENT_NAME", "test-experiment-name")
    monkeypatch.setenv("_PUBSUB_SUBSCRIPTION", "projects/test-project-123/subscriptions/test-sub")
    monkeypatch.setenv("_MOUNT_PATH", "/mnt/disks/share")
    mock_creds = MagicMock()
    mock_creds.token = "fake-token"
    mock_creds.universe_domain = "googleapis.com"
    monkeypatch.setattr(google.auth, "default", lambda *args, **kwargs: (mock_creds, "test-project-123"))

    mock_storage_client = MagicMock()
    mock_storage_client.return_value.bucket.return_value.blob.return_value.exists.return_value = False
    monkeypatch.setattr(google.cloud.storage, "Client", mock_storage_client)
