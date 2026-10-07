# AlphaEvolve Experiment Configuration Validator

This module validates AlphaEvolve experiment configurations fail-fast as the first deployment group before any cloud infrastructure, repositories, service accounts, or container builds are executed.

## Validations Performed

1. **`user_experiment_name`**: Max 25 characters, lowercase alphanumeric and hyphens, no underscores.
2. **`evaluation_mode`**: Must be `"batch"`.
3. **`evaluator.Dockerfile`**: Must exist in the specified `example_dir` under `cloud_build_dir`.
4. **`evaluation_provisioning_model`**: Must be `"STANDARD"`, `"SPOT"`, or `"FLEX_START"`.
5. **DWS `FLEX_START` constraints**: Requires GPU accelerator or H4D machine family.
6. **N1 accelerator rules**: Requires `accelerator_count > 0` and valid `accelerator_type` (`nvidia-tesla-t4`, `nvidia-tesla-p4`, `nvidia-tesla-v100`, `nvidia-tesla-p100`).
7. **`max_duration`**: Integer hour between 1 and 24 inclusive.
8. **`idle_timeout`**: Integer hour >= 1 and strictly less than `max_duration`.
9. **`model`**: At most two models in mixture with optional weights between 0 and 1.
10. **`num_samplers`**: Positive integer >= 1.
11. **`concurrency`**: Positive integer >= 1.
12. **`max_programs_generated`**: Positive integer >= 1.
13. **`max_programs_evaluated`**: Positive integer >= 1.
14. **`poll_interval`**: Positive number > 0.
15. **`max_duration_seconds`**: Positive integer >= 1.

## Usage

```yaml
- group: validation
  modules:
  - id: validate-experiment
    source: ./infrastructure/modules/validate-experiment
    settings:
      user_experiment_name: $(vars.user_experiment_name)
      cloud_build_dir: $(vars.cloud_build_dir)
      example_dir: $(vars.example_dir)
      evaluation_mode: $(vars.evaluation_mode)
      evaluation_provisioning_model: $(vars.evaluation_provisioning_model)
      evaluation_machine_type: $(vars.evaluation_machine_type)
      accelerator_count: $(vars.accelerator_count)
      accelerator_type: $(vars.accelerator_type)
      max_duration: $(vars.max_duration)
      idle_timeout: $(vars.idle_timeout)
      model: $(vars.model)
      num_samplers: $(vars.num_samplers)
      concurrency: $(vars.concurrency)
      max_programs_generated: $(vars.max_programs_generated)
      max_programs_evaluated: $(vars.max_programs_evaluated)
      poll_interval: $(vars.poll_interval)
      max_duration_seconds: $(vars.max_duration_seconds)
```

## License

<!-- BEGINNING OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
Copyright 2026 Google LLC

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

## Requirements

| Name | Version |
| ---- | ------- |
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.4 |

## Providers

| Name | Version |
| ---- | ------- |
| <a name="provider_terraform"></a> [terraform](#provider\_terraform) | n/a |

## Modules

No modules.

## Resources

| Name | Type |
| ---- | ---- |
| [terraform_data.validate_experiment_config](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_accelerator_count"></a> [accelerator\_count](#input\_accelerator\_count) | Number of GPU accelerators to attach to evaluation worker VMs (required for N1 machine types). | `number` | `0` | no |
| <a name="input_accelerator_type"></a> [accelerator\_type](#input\_accelerator\_type) | Accelerator type (e.g., nvidia-tesla-t4, required for N1 machine types). | `string` | `""` | no |
| <a name="input_cloud_build_dir"></a> [cloud\_build\_dir](#input\_cloud\_build\_dir) | Local path to the build directory containing the project files. | `string` | `""` | no |
| <a name="input_concurrency"></a> [concurrency](#input\_concurrency) | Number of concurrent candidate programs AlphaEvolve will generate simultaneously. | `number` | `4` | no |
| <a name="input_evaluation_machine_type"></a> [evaluation\_machine\_type](#input\_evaluation\_machine\_type) | Compute machine type for evaluation worker VMs. | `string` | `"n2-standard-4"` | no |
| <a name="input_evaluation_mode"></a> [evaluation\_mode](#input\_evaluation\_mode) | Evaluation execution mode (only 'batch' is supported). | `string` | `"batch"` | no |
| <a name="input_evaluation_provisioning_model"></a> [evaluation\_provisioning\_model](#input\_evaluation\_provisioning\_model) | VM provisioning model for Cloud Batch workers ('STANDARD', 'SPOT', or 'FLEX\_START'). | `string` | `"STANDARD"` | no |
| <a name="input_evaluator_dockerfile_path"></a> [evaluator\_dockerfile\_path](#input\_evaluator\_dockerfile\_path) | Optional direct path to evaluator.Dockerfile. If omitted, constructed from cloud\_build\_dir/example\_dir. | `string` | `""` | no |
| <a name="input_example_dir"></a> [example\_dir](#input\_example\_dir) | Relative path to the experiment example directory. | `string` | `""` | no |
| <a name="input_idle_timeout"></a> [idle\_timeout](#input\_idle\_timeout) | Maximum inactivity period allowed in hours. | `number` | `5` | no |
| <a name="input_max_duration"></a> [max\_duration](#input\_max\_duration) | Absolute maximum wall-clock lifespan of the experiment run in hours (1 to 24). | `number` | `6` | no |
| <a name="input_max_duration_seconds"></a> [max\_duration\_seconds](#input\_max\_duration\_seconds) | Maximum timeout in seconds for each evaluation job execution. | `number` | `3600` | no |
| <a name="input_max_programs_evaluated"></a> [max\_programs\_evaluated](#input\_max\_programs\_evaluated) | Maximum number of evaluation jobs that will be run. | `number` | `20` | no |
| <a name="input_max_programs_generated"></a> [max\_programs\_generated](#input\_max\_programs\_generated) | Maximum number of programs AlphaEvolve API will generate in total. | `number` | `100` | no |
| <a name="input_model"></a> [model](#input\_model) | AlphaEvolve Gemini model or mixture of models (e.g. 'gemini-3.8-flash' or 'gemini-3.8-flash:0.8,gemini-3.1-pro-preview:0.2'). | `string` | `"gemini-3.8-flash"` | no |
| <a name="input_num_samplers"></a> [num\_samplers](#input\_num\_samplers) | Number of worker threads polling AlphaEvolve API for candidate programs. | `number` | `4` | no |
| <a name="input_poll_interval"></a> [poll\_interval](#input\_poll\_interval) | Polling interval in seconds for querying the AlphaEvolve API. | `number` | `4` | no |
| <a name="input_user_experiment_name"></a> [user\_experiment\_name](#input\_user\_experiment\_name) | The user-specified experiment name. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_user_experiment_name"></a> [user\_experiment\_name](#output\_user\_experiment\_name) | The validated user experiment name. |
| <a name="output_validation_status"></a> [validation\_status](#output\_validation\_status) | Status string confirming all experiment configurations passed pre-flight validation. |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
