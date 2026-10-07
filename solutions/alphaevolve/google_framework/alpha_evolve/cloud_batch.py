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


"""Creates and submits a Google Cloud Batch job."""
import json
import logging
import os
from typing import Optional
from google.api_core import exceptions as google_exceptions
from google.cloud import batch_v1
import yaml

from .config import ControllerConfig
from .utils import create_full_programs_path

logger = logging.getLogger(__name__)


class BatchClient:
    """Client for interacting with Google Cloud Batch."""

    def __init__(self):
        """Initializes the BatchClient and the BatchServiceClient."""
        self.client = batch_v1.BatchServiceClient()
        self.controller_config = ControllerConfig.from_env()

    def _get_config_path(self) -> str:
        """Finds and returns the path to the eval-batch.yaml config file."""
        # 1. Check if a custom experiment-specific config is present
        config_path = os.path.join(self.controller_config.mount_path, self.controller_config.user_experiment_name, "source", "eval-batch.yaml")
        if os.path.exists(config_path):
            return config_path

        # 2. Check global container root config
        config_path = "/app/eval-batch.yaml"
        if os.path.exists(config_path):
            return config_path
            
        # 3. Try project root (assuming we are in src/alpha-evolve or similar)
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        config_path = os.path.join(project_root, "eval-batch.yaml")
        if os.path.exists(config_path):
            return config_path
            
        # 4. Try same dir
        config_path = os.path.join(current_dir, "eval-batch.yaml")
        if os.path.exists(config_path):
            return config_path
            
        raise FileNotFoundError("eval-batch.yaml not found")

    def _prepare_job_config(
            self,
            job_id: str,
            candidate_program_id: str,
            client_evaluator_script: str,
            client_evaluator_method: str,
            accelerator_count: int = 1,
            accelerator_type: str = "nvidia-tesla-t4") -> str:
        """Loads and prepares the job configuration JSON string from YAML.
        
        Args:
            job_id: A unique ID for this job.
            candidate_program_id: A unique ID for the candidate program.
            client_evaluator_script: The name of the client evaluator script.
            client_evaluator_method: The name of the client evaluator method.
            accelerator_count: Count of accelerators to allocate (n1-* only).
            accelerator_type: Type of accelerator (n1-* only).
        Returns:
            The job configuration JSON string.
        """
        config_path = self._get_config_path()
        
        with open(config_path, "r") as f:
            job_yaml_str = f.read()

        # Replace dynamic parameters FIRST to prevent environment variable collisions
        replacements = {
            "${_JOB_ID}": job_id,
            "${_CANDIDATE_PROGRAM_ID}": candidate_program_id,
            "${_CLIENT_EVALUATOR_SCRIPT}": client_evaluator_script,
            "${_CLIENT_EVALUATOR_METHOD}": client_evaluator_method,
        }
        for placeholder, value in replacements.items():
            job_yaml_str = job_yaml_str.replace(placeholder, str(value))

        job_yaml_str = os.path.expandvars(job_yaml_str)
        
        # Load YAML
        config_dict = yaml.safe_load(job_yaml_str)
        
        # Dynamically inject reservation constraint only if provisioningModel is FLEX_START
        try:
            instances = config_dict.get("allocationPolicy", {}).get("instances", [])
            for instance in instances:
                policy = instance.setdefault("policy", {})
                prov_model = policy.get("provisioningModel")
                machine_type = policy.get("machineType")
                
                if prov_model == "FLEX_START":
                    policy["reservation"] = "NO_RESERVATION"  # Required by GCP Batch for FLEX_START scheduling
                    
                # Inject GPU configuration for n1-* machine types
                if isinstance(machine_type, str) and machine_type.startswith("n1-"):
                    instance["installGpuDrivers"] = True
                    policy["accelerators"] = [
                        {
                            "type": accelerator_type,
                            "count": accelerator_count
                        }
                    ]
        except Exception as e:
            logger.error("Failed to dynamically configure allocation policy: %s", e)
            raise
            
        # Convert to JSON string
        job_json_str = json.dumps(config_dict)
        
        return job_json_str

    def create_batch_job(
      self,
      job_id: str, 
      candidate_program_id: str, 
      client_evaluator_script: str, 
      client_evaluator_method: str, 
      region: str = "us-central1",
      accelerator_count: int = 1,
      accelerator_type: str = "nvidia-tesla-t4") -> Optional[batch_v1.Job]:
      """Creates and submits a Google Cloud Batch job.

      Args:
        job_id: A unique ID for this job.
        candidate_program_id: A unique ID for the candidate program.
        client_evaluator_script: The name of the client evaluator script.
        client_evaluator_method: The name of the client evaluator method.
        region: The Google Cloud region to run the job in (e.g., "us-central1").
        accelerator_count: Count of accelerators to allocate (n1-* only).
        accelerator_type: Type of accelerator (n1-* only).
      Returns:
        The created batch_v1.Job object if successful, None otherwise.
      """
      try:
          job_json_str = self._prepare_job_config(
              job_id, candidate_program_id, client_evaluator_script, client_evaluator_method,
              accelerator_count=accelerator_count, accelerator_type=accelerator_type
          )
      except FileNotFoundError as e:
          logger.error("Config file not found: %s", e)
          raise

      # Project ID is required for the submission API 
      project_id = self.controller_config.project_id

      # Convert back to JSON and create Job object
      job = batch_v1.Job.from_json(job_json_str)

      # Create the request
      create_request = batch_v1.CreateJobRequest(
          parent=f"projects/{project_id}/locations/{region}",
          job=job,
          job_id=job_id
      )
      
      # Submit the job
      try:
        created_job = self.client.create_job(request=create_request)
        logger.info("Job created successfully: %s", created_job.name)
        return created_job
      except google_exceptions.GoogleAPICallError as e:
        logger.error("An API error occurred while creating the job: %s", e)
        raise
      except Exception as e:
        logger.error("An unexpected error occurred while creating the job: %s", e)
        raise


    def delete_batch_job(self, job_name: str) -> None:
      """Deletes a Google Cloud Batch job.

      Args:
        job_name: The full resource name of the job to delete
                  (e.g., "projects/PROJECT_ID/locations/REGION/jobs/JOB_ID").
      """
      delete_request = batch_v1.DeleteJobRequest(name=job_name)
      
      try:
        # This initiates a long-running operation to delete the job
        self.client.delete_job(request=delete_request)
        logger.info("Initiated deletion of job: %s", job_name)
      except google_exceptions.GoogleAPICallError as e:
        logger.error("An API error occurred while deleting the job: %s", e)
      except Exception as e:
        logger.error("An unexpected error occurred while deleting the job: %s", e)

    def get_job_status(self, job_name: str) -> dict:
      """Retrieves execution status events and exit code details for a job.
      
      Args:
        job_name: Full resource name of the job (projects/.../locations/.../jobs/...).
      Returns:
        dict containing is_spot_preemption (bool), exit_code, and description.
      """
      try:
        job = self.client.get_job(name=job_name)
        is_spot_preemption = False
        last_description = ""
        exit_code = None

        if job.status and job.status.status_events:
          for event in job.status.status_events:
            desc = event.description or ""
            last_description = desc
            if any(k in desc for k in ["50001", "50006"]) or any(k in desc.lower() for k in ["spot preemption", "preemption", "recreated"]):
              is_spot_preemption = True
              exit_code = 50001
              break

        return {
            "is_spot_preemption": is_spot_preemption,
            "exit_code": exit_code,
            "description": last_description,
        }
      except Exception as e:
        logger.warning("Could not fetch job status for %s: %s", job_name, e)
        return {"is_spot_preemption": False, "exit_code": None, "description": str(e)}

