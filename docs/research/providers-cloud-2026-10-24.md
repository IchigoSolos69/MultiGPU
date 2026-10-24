# Official-source audit: cloud and development resources

Checked 24 October 2026. Scope: Databricks Free Edition, Colab Enterprise, GitHub Codespaces and JupyterHub. This is a published-offer audit, not evidence that a particular account or the MultiGPU connector has qualified for a resource. Only provider/project documentation, pricing and official signup pages were used. No third-party guides or community answers establish the conclusions below.

## Recommended directory classification

| Resource | Free CPU | Free GPU | Paid compute | Recommended product grouping |
| --- | --- | --- | --- | --- |
| Databricks Free Edition | Yes, limited serverless compute | Conditional: limited AI Runtime after eligible LinkedIn verification | Full platform is a separate account/trial, not a Free Edition upgrade | Free CPU; conditional free GPU; personal/non-commercial only |
| Colab Enterprise | No established recurring Enterprise CPU entitlement found | No established recurring Enterprise GPU entitlement found | Metered Google Cloud CPU/GPU, disks and applicable ancillary services | Paid/cloud-credit eligible; keep separate from consumer Colab |
| GitHub Codespaces | Personal GitHub Free includes 120 core-hours and 15 GB-month per month | Not generally available | CPU overage after included allowance; Pro includes a larger allowance | Free CPU development helper; exclude general training-worker pool |
| JupyterHub | Depends on the Hub operator | Depends on the Hub operator | Depends on infrastructure/institution/operator | Bring your own or institutional hardware; not a free-compute vendor |

The rows are conclusions from the cited evidence below. A free account, a free software license and a conditional credit grant must not be presented as equivalent to free GPU hardware.

## Databricks Free Edition

Databricks describes Free Edition as perpetually no-cost for personal learning and experimentation. Its official signup selector says no credit card is required and distinguishes the personal Free Edition from a **14-day, $400-credit work trial**. The marketing page expressly excludes commercial and production use. [Free Edition FAQ](https://www.databricks.com/learn/free-edition), [official signup](https://login.databricks.com/signup?dbx_source=docs).

The current limitations page (updated 18 October 2026) restricts Free Edition to limited serverless resources, trusted outbound destinations and non-commercial use. Fair-use exhaustion can suspend compute. Eligible accounts offered LinkedIn verification can unlock limited AI Runtime; eligibility is account/region-specific and no fixed GPU-hour allocation is published. Account-console/account-level APIs are unavailable, which does not imply every workspace API is disabled. [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations).

AI Runtime itself is documented as GPU compute for training and fine-tuning, with CLI, notebooks and Jobs entry points. This establishes a training capability rather than an entitlement to every advertised GPU. [AI Runtime](https://docs.databricks.com/aws/en/machine-learning/ai-runtime/).

GPU availability varies by workspace and geography. The general AI Runtime overview lists A10, H100 and B300 configurations and capacity constraints; these must not be advertised as all included in Free Edition. [AI Runtime overview](https://docs.databricks.com/aws/en/machine-learning/ai-runtime/overview).

**App implication (inference):** expose conditional GPU eligibility, verify access before launch, and respect network/use restrictions. Do not silently activate a work trial. Full-platform access needs a separate account rather than upgrading Free Edition. [Subscription management](https://docs.databricks.com/aws/en/admin/account-settings/account).

## Colab Enterprise

The Enterprise pricing page charges for runtime CPU, memory, disks and accelerators, with management fees where applicable. Accelerator cost is separate for machines with attached accelerators. No recurring free Colab Enterprise GPU or CPU allocation was found in its runtime pricing. A generic Google Cloud “Start free” link is not an Enterprise GPU entitlement. [Colab Enterprise pricing](https://cloud.google.com/colab/pricing).

Creating a runtime requires a Google Cloud project with billing enabled, required APIs and IAM permissions; runtime creation is documented through Cloud console, `gcloud` and Terraform. [Create a runtime](https://docs.cloud.google.com/colab/docs/create-runtime).

Notebook execution can be scheduled immediately or recurrently, using a runtime template and user or service-account permissions; outputs go to Cloud Storage. This is a documented automation route, with separately relevant storage permissions and costs. [Schedule a notebook run](https://docs.cloud.google.com/colab/docs/schedule-notebook-run).

The Google Cloud welcome offer is **$300 for 90 days** for eligible new customers and requires a valid payment method. A nonbillable Free Trial account **cannot add GPUs to VM instances or request quota increases**. Activating a paid billing account unlocks restricted products and retains unexpired welcome credit, but uncovered/excess usage can then be charged. Therefore “$300 free trial” does not mean “no-billing GPU access.” [Google Cloud Free Program](https://docs.cloud.google.com/free/docs/free-cloud-features).

Enterprise runtimes consume Compute Engine quotas; GPUs require model/region quotas and the global GPU quota. Credit availability does not establish GPU quota or physical capacity. [Colab Enterprise quotas](https://docs.cloud.google.com/colab/docs/quotas).

Google now documents **spend cap budgets** for selected API services; ordinary alerts-only budgets do not stop spending. [Budgets and alerts](https://docs.cloud.google.com/billing/docs/how-to/budgets).

The spend-cap page, updated 22 October 2026, does not list Compute Engine among eligible services. It says persistent compute/storage costs continue, in-flight work finishes, enforcement is not instant, and overages remain billable. A selected Agent Platform API cap consequently must not be assumed to cover every Colab Enterprise runtime cost. [Spend cap budgets](https://docs.cloud.google.com/billing/docs/how-to/budgets-spend-caps).

**App implication (inference):** classify as paid compute, optionally offset by verified, unexpired, SKU-eligible cloud credits on a billing-enabled account. Keep consumer Colab's free runtime in a different entry. Do not promise a zero bill merely from a local timer, a Cloud Billing alert or a generic credit balance. Entitlements, quota and cost-control coverage need account-level evidence.

## GitHub Codespaces

Personal GitHub Free accounts include **120 core-hours and 15 GB-month per month**; Pro includes **180 core-hours and 20 GB-month**. These are core-hours: a two-core machine consumes two included hours for one wall-clock hour. No free quota is included with organization/enterprise plans. Without a valid payment method, usage is blocked when allowance is exhausted. With billing configured, paid usage and applicable budgets matter. Stopped resources still consume storage. [Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces), [included product usage](https://docs.github.com/en/billing/reference/product-usage-included).

The official machine-learning guide explicitly says GPU machines were a selected-customer trial and are **not generally available**. Installing CUDA or buying Pro does not prove access to a GPU machine. [Machine learning in Codespaces](https://docs.github.com/en/codespaces/developing-in-a-codespace/getting-started-with-github-codespaces-for-machine-learning).

The additional product terms, effective 27 August 2026, prohibit providing an integrated or standalone commercial service offering Codespaces, disproportionate server burden, and workloads unrelated to development/testing of the associated repository project. These constraints make it inappropriate to advertise Codespaces as an unrestricted general compute/training resource. [Codespaces product terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#codespaces).

The REST API supports create/start/stop/delete and machine discovery, but API availability does not override these terms. [Codespaces REST API](https://docs.github.com/en/rest/codespaces), [machine discovery API](https://docs.github.com/en/rest/codespaces/machines).

**App implication (inference):** retain only as a repository-development/CPU-test helper with included allowance. Do not count it in the free GPU pool, and do not enable the app's general training-worker integration merely because a personal quota exists.

## JupyterHub

JupyterHub is open-source multi-user notebook infrastructure that can run on cloud servers, institutional equipment or a user's own hardware. The project does not supply a universal hosted free CPU/GPU allowance. [JupyterHub project](https://jupyter.org/hub).

Its Kubernetes deployment documentation supports administrator-configured CPU, memory and GPU limits/profiles. Its GPU example explicitly assumes compatible GPUs already exist on a Kubernetes node; creating a GPU profile does not provision free hardware. [User resource configuration](https://z2jh.jupyter.org/en/stable/jupyterhub/customizing/user-resources.html).

The Hub REST API documents scoped authentication and user server start/stop/status operations. Spawn options depend on the deployment's Spawner. These are documented integration capabilities, not evidence the user has permission or an allocation. [JupyterHub REST API](https://jupyterhub.readthedocs.io/en/stable/reference/rest-api.html).

**App implication (inference):** label **Institutional / user-provided hardware**, with host URL, authorization and actual allocated device/quota inspection. Costs, eligible workloads, egress, idle shutdown, checkpoints and access duration belong to the specific operator. Do not assign a JupyterHub-wide free/paid plan or GPU quota.

## Verification boundary

No live provider account was created, activated, billed or exercised for this audit. Published interfaces do not establish complete MultiGPU support. Launch, interruption, progress, artifact export and recovery still require connector/account qualification. Where fixed free GPU hours, entitlement scope or Enterprise credit coverage were absent from current official pages, the recommendation is to keep them unknown until observed rather than infer a guarantee.
