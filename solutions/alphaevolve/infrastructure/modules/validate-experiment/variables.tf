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

variable "user_experiment_name" {
  description = "The user-specified experiment name."
  type        = string
  validation {
    condition     = length(var.user_experiment_name) > 0 && length(var.user_experiment_name) <= 25 && !can(regex("_", var.user_experiment_name)) && can(regex("^[a-z0-9][a-z0-9-]*$", var.user_experiment_name))
    error_message = "user_experiment_name must be 1-25 lowercase alphanumeric characters or hyphens, and cannot contain underscores."
  }
}

variable "cloud_build_dir" {
  description = "Local path to the build directory containing the project files."
  type        = string
  default     = ""
}

variable "example_dir" {
  description = "Relative path to the experiment example directory."
  type        = string
  default     = ""
}

variable "repo_url" {
  description = "Optional Git repository URL. If omitted, empty, or 'local', local filesystem paths are validated."
  type        = string
  default     = ""
}

variable "repo_ref" {
  description = "Optional Git ref (branch, tag, or commit SHA) for remote repository validation."
  type        = string
  default     = "main"
}

variable "evaluator_dockerfile_path" {
  description = "Optional direct path to evaluator.Dockerfile. If omitted, constructed from cloud_build_dir/example_dir."
  type        = string
  default     = ""
}

variable "evaluation_mode" {
  description = "Evaluation execution mode (only 'batch' is supported)."
  type        = string
  default     = "batch"
  validation {
    condition     = var.evaluation_mode == "batch"
    error_message = "Invalid evaluation_mode '${var.evaluation_mode}'. Only 'batch' mode is supported."
  }
}

variable "evaluation_provisioning_model" {
  description = "VM provisioning model for Cloud Batch workers ('STANDARD', 'SPOT', or 'FLEX_START')."
  type        = string
  default     = "STANDARD"
  validation {
    condition     = contains(["STANDARD", "SPOT", "FLEX_START"], var.evaluation_provisioning_model)
    error_message = "Invalid evaluation_provisioning_model '${var.evaluation_provisioning_model}'. Valid values are 'STANDARD', 'SPOT', 'FLEX_START'."
  }
}

variable "evaluation_machine_type" {
  description = "Compute machine type for evaluation worker VMs."
  type        = string
  default     = "n2-standard-4"
}

variable "accelerator_count" {
  description = "Number of GPU accelerators to attach to evaluation worker VMs (required for N1 machine types)."
  type        = number
  default     = 0
}

variable "accelerator_type" {
  description = "Accelerator type (e.g., nvidia-tesla-t4, required for N1 machine types)."
  type        = string
  default     = ""
}

variable "max_duration" {
  description = "Absolute maximum wall-clock lifespan of the experiment run in hours (1 to 24)."
  type        = number
  default     = 6
  validation {
    condition     = var.max_duration != null && var.max_duration >= 1 && var.max_duration <= 24 && floor(var.max_duration) == var.max_duration
    error_message = "Invalid max_duration. Must be an integer hour between 1 and 24 inclusive."
  }
}

variable "idle_timeout" {
  description = "Maximum inactivity period allowed in hours."
  type        = number
  default     = 5
  validation {
    condition     = var.idle_timeout == null || (var.idle_timeout >= 1 && floor(var.idle_timeout) == var.idle_timeout)
    error_message = "Invalid idle_timeout. Must be an integer hour greater than or equal to 1."
  }
}

variable "model" {
  description = "AlphaEvolve Gemini model or mixture of models (e.g. 'gemini-3.8-flash' or 'gemini-3.8-flash:0.8,gemini-3.1-pro-preview:0.2')."
  type        = string
  default     = "gemini-3.8-flash"
  validation {
    condition = var.model == "" || (
      length(split(",", replace(replace(replace(var.model, ";", ","), "+", ","), "/", ","))) <= 2 &&
      length(split(",", replace(replace(replace(var.model, ";", ","), "+", ","), "/", ","))) > 0 &&
      alltrue([
        for item in split(",", replace(replace(replace(var.model, ";", ","), "+", ","), "/", ",")) :
        can(regex("^[a-zA-Z0-9_.-]+$", trimspace(split(":", item)[0]))) &&
        length(split(":", item)) <= 2 &&
        (length(split(":", item)) == 1 || can(regex("^(0(\\.[0-9]+)?|1(\\.0+)?|\\.[0-9]+)$", trimspace(split(":", item)[1]))))
      ])
    )
    error_message = "Model parameter is invalid. At most two models can be specified with optional relative weights between 0 and 1 (e.g. 'gemini-3.8-flash' or 'gemini-3.8-flash:0.8,gemini-3.1-pro-preview:0.2')."
  }
}

variable "num_samplers" {
  description = "Number of worker threads polling AlphaEvolve API for candidate programs."
  type        = number
  default     = 4
  validation {
    condition     = var.num_samplers != null && var.num_samplers >= 1 && floor(var.num_samplers) == var.num_samplers
    error_message = "num_samplers must be a positive integer >= 1."
  }
}

variable "concurrency" {
  description = "Number of concurrent candidate programs AlphaEvolve will generate simultaneously."
  type        = number
  default     = 4
  validation {
    condition     = var.concurrency != null && var.concurrency >= 1 && floor(var.concurrency) == var.concurrency
    error_message = "concurrency must be a positive integer >= 1."
  }
}

variable "max_programs_generated" {
  description = "Maximum number of programs AlphaEvolve API will generate in total."
  type        = number
  default     = 100
  validation {
    condition     = var.max_programs_generated != null && var.max_programs_generated >= 1 && floor(var.max_programs_generated) == var.max_programs_generated
    error_message = "max_programs_generated must be a positive integer >= 1."
  }
}

variable "max_programs_evaluated" {
  description = "Maximum number of evaluation jobs that will be run."
  type        = number
  default     = 20
  validation {
    condition     = var.max_programs_evaluated != null && var.max_programs_evaluated >= 1 && floor(var.max_programs_evaluated) == var.max_programs_evaluated
    error_message = "max_programs_evaluated must be a positive integer >= 1."
  }
}

variable "poll_interval" {
  description = "Polling interval in seconds for querying the AlphaEvolve API."
  type        = number
  default     = 4
  validation {
    condition     = var.poll_interval != null && var.poll_interval > 0
    error_message = "poll_interval must be a positive number greater than 0."
  }
}

variable "max_duration_seconds" {
  description = "Maximum timeout in seconds for each evaluation job execution."
  type        = number
  default     = 3600
  validation {
    condition     = var.max_duration_seconds != null && var.max_duration_seconds >= 1 && floor(var.max_duration_seconds) == var.max_duration_seconds
    error_message = "max_duration_seconds must be a positive integer >= 1."
  }
}
