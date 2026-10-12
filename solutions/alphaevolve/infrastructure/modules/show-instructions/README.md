# AlphaEvolve Experiment Instructions Module

This module displays clear, formatted instructions to the user upon successful experiment deployment, showing how to select and run the experiment in the Jupyter Notebook.

## Usage

```yaml
- group: instructions
  modules:
  - id: show-instructions
    source: ./infrastructure/modules/show-instructions
    settings:
      bucket_name: $(vars.existing_bucket_name)
      user_experiment_name: $(vars.user_experiment_name)
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
| [terraform_data.display_instructions](https://registry.terraform.io/providers/hashicorp/terraform/latest/docs/resources/data) | resource |

## Inputs

| Name | Description | Type | Default | Required |
| ---- | ----------- | ---- | ------- | :------: |
| <a name="input_bucket_name"></a> [bucket\_name](#input\_bucket\_name) | Target GCS bucket name where experiment configurations are stored. | `string` | n/a | yes |
| <a name="input_user_experiment_name"></a> [user\_experiment\_name](#input\_user\_experiment\_name) | The user experiment name. | `string` | n/a | yes |

## Outputs

| Name | Description |
| ---- | ----------- |
| <a name="output_instructions"></a> [instructions](#output\_instructions) | Formatted user instructions for running the experiment in the Jupyter Notebook. |
<!-- END OF PRE-COMMIT-TERRAFORM DOCS HOOK -->
