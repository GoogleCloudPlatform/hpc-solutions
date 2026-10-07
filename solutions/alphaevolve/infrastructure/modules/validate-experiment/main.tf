/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

locals {
  is_remote_repo = try(coalesce(var.repo_url, "local"), "local") != "local"

  cleaned_example_dir = trim(var.example_dir, "/")
  cleaned_cloud_build = trimsuffix(var.cloud_build_dir, "/")

  # Path to check in remote Git repository
  git_check_path = var.evaluator_dockerfile_path != "" ? var.evaluator_dockerfile_path : (
    local.cleaned_cloud_build != "" && local.cleaned_example_dir != "" ? "${local.cleaned_cloud_build}/${local.cleaned_example_dir}/evaluator.Dockerfile" : (
      local.cleaned_example_dir != "" ? "${local.cleaned_example_dir}/evaluator.Dockerfile" : ""
    )
  )

  # Candidate paths on local disk (supports absolute paths, arbitrary relative paths, and CTK workspace depths)
  local_dockerfile_candidates = compact([
    var.evaluator_dockerfile_path,
    var.example_dir != "" ? "${trimsuffix(var.example_dir, "/")}/evaluator.Dockerfile" : "",
    local.git_check_path,
    var.example_dir != "" ? "${path.root}/${trimsuffix(var.example_dir, "/")}/evaluator.Dockerfile" : "",
    var.example_dir != "" ? "${path.root}/../../${trimsuffix(var.example_dir, "/")}/evaluator.Dockerfile" : "",
    var.example_dir != "" ? "${path.root}/../../../${trimsuffix(var.example_dir, "/")}/evaluator.Dockerfile" : "",
    var.example_dir != "" ? "${path.root}/../../../../${trimsuffix(var.example_dir, "/")}/evaluator.Dockerfile" : "",
    local.git_check_path != "" ? "${path.root}/${local.git_check_path}" : "",
    local.git_check_path != "" ? "${path.root}/../../${local.git_check_path}" : "",
    local.git_check_path != "" ? "${path.root}/../../../${local.git_check_path}" : "",
    local.git_check_path != "" ? "${path.root}/../../../../${local.git_check_path}" : "",
  ])

  local_dockerfile_exists = length([
    for p in local.local_dockerfile_candidates : p if fileexists(p)
  ]) > 0
}

resource "terraform_data" "validate_experiment_config" {
  input = {
    user_experiment_name          = var.user_experiment_name
    model                         = var.model
    evaluation_mode               = var.evaluation_mode
    evaluation_provisioning_model = var.evaluation_provisioning_model
    evaluation_machine_type       = var.evaluation_machine_type
    accelerator_count             = var.accelerator_count
    accelerator_type              = var.accelerator_type
    max_duration                  = var.max_duration
    idle_timeout                  = var.idle_timeout
    num_samplers                  = var.num_samplers
    concurrency                   = var.concurrency
    max_programs_generated        = var.max_programs_generated
    max_programs_evaluated        = var.max_programs_evaluated
    poll_interval                 = var.poll_interval
    max_duration_seconds          = var.max_duration_seconds
    repo_url                      = var.repo_url
    repo_ref                      = var.repo_ref
  }

  lifecycle {
    precondition {
      condition     = var.idle_timeout == null || var.max_duration == null || var.idle_timeout < var.max_duration
      error_message = "idle_timeout (${var.idle_timeout}) must be strictly less than max_duration (${var.max_duration})."
    }

    precondition {
      condition     = var.evaluation_provisioning_model != "FLEX_START" || can(regex("^(g2|g4|a2|a3|a4|a4x|n1|h4d)-", var.evaluation_machine_type))
      error_message = "DWS FLEX_START is not supported for machine family '${var.evaluation_machine_type}'. FLEX_START on Cloud Batch requires GPU accelerator-enabled VM or H4D instances (supported: G2, G4, A2, A3, A4, A4x, N1, H4D)."
    }

    precondition {
      condition = !can(regex("^n1-", var.evaluation_machine_type)) || (
        var.accelerator_count != null && var.accelerator_count > 0 &&
        var.accelerator_type != null && contains(["nvidia-tesla-t4", "nvidia-tesla-p4", "nvidia-tesla-v100", "nvidia-tesla-p100"], var.accelerator_type)
      )
      error_message = "N1 machine types require accelerator_count to be a positive integer and accelerator_type to be one of: nvidia-tesla-t4, nvidia-tesla-p4, nvidia-tesla-v100, nvidia-tesla-p100."
    }

    # When repo_url is NOT set (or "local"), validate locally
    precondition {
      condition     = local.is_remote_repo || local.local_dockerfile_exists
      error_message = "No evaluator.Dockerfile found locally for example_dir '${var.example_dir}'."
    }
  }

  # When repo_url IS set: check Git first (shallow/sparse). If not found in Git, fall back to checking locally anywhere on disk.
  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -e
      if [ -n "$REPO_URL" ] && [ "$REPO_URL" != "local" ]; then
        if [ -n "$CHECK_PATH" ]; then
          echo "--> [validate-experiment] Checking '$CHECK_PATH' in remote repository: $REPO_URL (ref: $REPO_REF)..."
          TMP_DIR=$(mktemp -d)
          trap 'rm -rf "$TMP_DIR"' EXIT

          FOUND_IN_GIT=0
          git init "$TMP_DIR" >/dev/null 2>&1
          git -C "$TMP_DIR" remote add origin "$REPO_URL" >/dev/null 2>&1
          git -C "$TMP_DIR" sparse-checkout init --cone >/dev/null 2>&1
          git -C "$TMP_DIR" sparse-checkout set --skip-checks "$CHECK_PATH" >/dev/null 2>&1

          # Shallow clone with no unneeded history or extra data
          if git -C "$TMP_DIR" fetch --depth 1 --filter=blob:none origin "$REPO_REF" >/dev/null 2>&1 || git -C "$TMP_DIR" fetch --filter=blob:none origin >/dev/null 2>&1; then
            git -C "$TMP_DIR" checkout "$REPO_REF" >/dev/null 2>&1 || git -C "$TMP_DIR" checkout FETCH_HEAD >/dev/null 2>&1
            if [ -f "$TMP_DIR/$CHECK_PATH" ]; then
              FOUND_IN_GIT=1
              echo "--> [validate-experiment] Verified '$CHECK_PATH' exists in remote Git repository."
            fi
          fi

          if [ "$FOUND_IN_GIT" -eq 0 ]; then
            echo "--> [validate-experiment] '$CHECK_PATH' not found in remote Git repo ($REPO_URL). Checking locally..."
            FOUND_LOCALLY=0
            # Check direct path (absolute/relative) as well as upward relative paths from deployment dir
            for CANDIDATE in "$EXAMPLE_DIR/evaluator.Dockerfile" \
                             "$DOCKERFILE_PATH" \
                             "$CHECK_PATH" \
                             "$PWD/$CHECK_PATH" \
                             "$PWD/../../$CHECK_PATH" \
                             "$PWD/../../../$CHECK_PATH" \
                             "$PWD/../../../../$CHECK_PATH" \
                             "$PWD/$EXAMPLE_DIR/evaluator.Dockerfile" \
                             "$PWD/../../$EXAMPLE_DIR/evaluator.Dockerfile" \
                             "$PWD/../../../$EXAMPLE_DIR/evaluator.Dockerfile" \
                             "$PWD/../../../../$EXAMPLE_DIR/evaluator.Dockerfile"; do
              if [ -n "$CANDIDATE" ] && [ -f "$CANDIDATE" ]; then
                FOUND_LOCALLY=1
                echo "--> [validate-experiment] Verified evaluator.Dockerfile exists locally at '$CANDIDATE'."
                break
              fi
            done

            if [ "$FOUND_LOCALLY" -eq 0 ]; then
              echo "ERROR: [validate-experiment] No evaluator.Dockerfile found in remote Git repository '$REPO_URL' (ref: '$REPO_REF') at '$CHECK_PATH' nor locally at '$EXAMPLE_DIR'." >&2
              exit 1
            fi
          fi
        fi
      fi
    EOT

    environment = {
      REPO_URL        = var.repo_url
      REPO_REF        = var.repo_ref
      CHECK_PATH      = local.git_check_path
      EXAMPLE_DIR     = var.example_dir
      DOCKERFILE_PATH = var.evaluator_dockerfile_path
    }
  }
}
