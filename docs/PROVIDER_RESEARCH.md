# Provider compute audit and free/paid classification

**Checked: 24 October 2026. Scope: all 13 requested services plus the local computer. Official sources only.**

This review uses provider-owned product pages, pricing, documentation, terms and official repositories. It does not use comparison blogs, affiliate rankings or community replies as evidence. No signup, account entitlement, GPU allocation or billing transaction was tested. “Published” means the provider describes an offer; it does not guarantee capacity or eligibility for a particular user.

## Classification

A provider can offer free CPU and paid GPU simultaneously. Credits, trials, institution-owned hardware and an available free GPU are different products. Use the categories below rather than a single free/paid boolean.

| Provider | No-payment offer described by official sources | GPU training category | Separation for MultiGPU | Official evidence |
|---|---|---|---|---|
| Kaggle Notebooks | CPU and quota-limited accelerators | Ongoing free allowance, subject to quota/capacity | Free GPU / CPU | [GPU usage](https://www.kaggle.com/docs/efficient-gpu-usage), [quota interface](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/quota.md) |
| Google Colab | Managed CPU/accelerator runtimes with variable access | Free GPU availability is dynamic; paid upgrades separate | Free GPU / CPU + optional paid | [FAQ](https://research.google.com/colaboratory/faq.html) |
| Databricks Free Edition | Limited serverless CPU; eligible users may unlock AI Runtime | Conditional GPU access, personal/non-commercial only | Free CPU + conditional free GPU | [Free Edition limitations](https://docs.databricks.com/aws/en/getting-started/free-edition-limitations), [AI Runtime](https://docs.databricks.com/aws/en/machine-learning/ai-runtime/) |
| Modal | Recurring Starter compute credits | GPU consumes credits; payment method required; overage can bill | Recurring credits + paid compute | [Pricing](https://modal.com/pricing), [GPU eligibility](https://modal.com/docs/guide/gpu) |
| Lightning AI Studios | One free CPU Studio; eligible starting credits | Introductory credit-backed GPU, then paid | Free CPU + introductory credits + paid GPU | [Pricing](https://studio.lightning.ai/pricing/), [billing](https://studio.lightning.ai/docs/platform/overview/faq/billing/) |
| Deepnote | Basic CPU notebook tier | GPU is a paid/account-qualified offering | Free CPU / paid GPU | [Pricing](https://deepnote.com/pricing), [hardware](https://deepnote.com/docs/selecting-hardware) |
| Hex | Community CPU notebook tier | Paid Team/Enterprise advanced compute; trial excludes GPU | Free CPU / paid GPU | [Pricing](https://hex.tech/pricing/), [advanced compute](https://learn.hex.tech/docs/administration/workspace_settings/compute) |
| Datalore | Monthly CPU S allowance | GPU requires paid-plan computation credits; Free/trial lack them | Free CPU / paid GPU | [Plans](https://www.jetbrains.com/help/datalore/datalore-plans.html), [credits](https://www.jetbrains.com/help/datalore/use-datalore-credits.html) |
| GitHub Codespaces | Included personal-account CPU/storage allowance | GPUs not generally available | Free/paid CPU development helper; outside general training pool | [Billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces), [ML guide](https://docs.github.com/en/codespaces/developing-in-a-codespace/getting-started-with-github-codespaces-for-machine-learning) |
| Fastero | Hosted notebook/Python compute | GPU entitlement and hardware not established | Free notebook compute / GPU unverified | [Pricing](https://fastero.com/pricing), [hosted apps](https://fastero.com/platform/deliver-embed/apps) |
| Paperspace Notebooks | Conflicting free-machine descriptions; new-account access unverified | Paid GPU available; dependable free entitlement not established | Paid / free availability unverified | [DigitalOcean pricing](https://docs.digitalocean.com/products/paperspace/pricing/), [notebook selection](https://docs.digitalocean.com/products/paperspace/notebooks/how-to/create-notebooks/), [marketing pricing](https://www.paperspace.com/pricing) |
| Colab Enterprise | No established recurring free Enterprise runtime allowance | Metered Google Cloud; eligible credits may offset charges | Paid / cloud-credit eligible | [Pricing](https://cloud.google.com/colab/pricing), [Free Program restrictions](https://docs.cloud.google.com/free/docs/free-cloud-features) |
| JupyterHub | Software, not a universal hosted allowance | Operator allocates existing CPU/GPU hardware | Institutional / user-provided hardware | [Project](https://jupyter.org/hub), [resource configuration](https://z2jh.jupyter.org/en/stable/jupyterhub/customizing/user-resources.html) |
| Your computer | Existing user-owned CPU/CUDA hardware | Local resources and costs | Local hardware, outside cloud pricing groups | [PyTorch setup](https://pytorch.org/get-started/locally/) |

## Free and paid lists

**Published ongoing free GPU access:** Kaggle and consumer Colab, with quota/capacity restrictions. Neither is a guaranteed continuously available GPU.

**Conditional no-payment GPU:** Databricks Free Edition only where its account-specific verification offer unlocks AI Runtime and the use is non-commercial.

**GPU compute paid from credits:** Modal has a recurring allowance; Lightning has eligible introductory credits. Neither should be presented as unlimited or automatically safe from charges. GPU payment/verification requirements still apply.

**Free CPU or notebook resources:** Deepnote, Hex, Datalore and Lightning; Fastero has free hosted Python with unverified hardware. Codespaces is a separate repository-development helper.

**Paid GPU options:** Paperspace; Colab Enterprise; eligible paid Deepnote, Hex and Datalore plans; Lightning after/above its grant; Modal after/above credits. Consumer Colab also offers paid products. A free plan name or paid subscription's included credit does not make its GPU a recurring free tier.

**Existing hardware:** JupyterHub and the local computer. Payment and allocations depend on the owner/operator.

## Paperspace: why it is not in the dependable free list

The user's account experience and current public pages do not establish a usable free offering. Some official documentation still advertises free C4/M4000 machines and queues. Other pricing presentation emphasizes hourly instances. No reviewed official retirement or new-account exclusion notice resolved that discrepancy. Therefore this audit does **not** assert that every free product has officially been removed; it also does **not** advertise free Paperspace access as verified. Require an actual current account entitlement before admitting a free-only job. The conflicting privacy and free-machine descriptions also need confirmation before private input upload. See the [Paperspace evidence trail](research/providers-gpu-2026-10-24.md#paperspace-evidence-and-unresolved-availability).

## Important distinctions for the product

1. A free account/editor/AI assistant balance does not establish a training GPU. Store CPU and GPU offers separately.
2. Distinguish ongoing allowances, recurring credits, introductory credits, conditional grants, paid resources and external hardware. Show multiple tags when appropriate.
3. Keep published offering, observed account allowance, requested hardware and tested app integration as separate facts.
4. Missing or conflicting evidence remains unknown. Do not silently convert an unknown balance into permission to spend.
5. Cloud trial credits are not GPU entitlement: nonbillable Google Cloud trials prohibit GPU VM allocation. Activating billing changes the risk and does not establish quota/capacity.
6. API access can require a paid plan even when a free notebook exists. Codespaces workload terms also rule out treating it as an unrestricted commercial training backend.
7. Modal hosted notebooks are retired; the Functions API remains the relevant training path. No self-hosted-Jupyter workaround is implemented.
8. Fixed free-hour claims must not be extrapolated from variable credits or unpublished limits. Runtime timers do not establish full compute/storage/transfer billing enforcement.

## Source detail and unresolved questions

- [Paperspace, Kaggle, consumer Colab](research/providers-gpu-2026-10-24.md): free-machine conflict, hardware retirement, quotas, restrictions, API availability and account checks.
- [Modal, Lightning and local hardware](research/providers-credits-2026-10-24.md): credit renewal/expiry, card conditions, billing controls and connector implications.
- [Deepnote, Hex, Datalore and Fastero](research/providers-notebooks-2026-10-24.md): CPU/GPU separation, trial exclusions, API plans, conflicting CPU counts and AI-versus-compute balances.
- [Databricks, Colab Enterprise, Codespaces and JupyterHub](research/providers-cloud-2026-10-24.md): conditional GPUs, non-commercial use, trial restrictions, service budgets and operator-owned hardware.

## Research versus shipping application

This is a research/documentation update. The 0.4.3 installer was built before this audit; its binary and embedded provider copy have not been rebuilt by writing these documents. In particular, historical catalog wording advertising a free Paperspace M4000 should not be treated as current verified availability. A subsequent product change should synchronize the directory tags, filters, eligibility rules and official source links against this classification, with tests for unknown and conflicting entitlements.

Current implemented connector and validation boundaries are described in the [project README](../README.md) and [validation report](../VALIDATION.md). This research grants no permission to bypass limits, keep sessions artificially alive or activate paid fallback.
