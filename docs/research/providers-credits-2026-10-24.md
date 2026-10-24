# Official-source review: Modal, Lightning AI and local hardware

Checked 24 October 2026. Published offers are separate from a verified account entitlement and from MultiGPU connector qualification. No accounts were created, modified or billed.

## Modal — recurring compute credits, then paid usage

Starter has no monthly subscription fee and includes $30 of compute credits each month. CPU, memory and GPU use consume the allowance; excess or uncovered usage is billable. This is a recurring credit allowance on a metered service, not unlimited free hardware. The pricing page lists separate storage/egress allowances. It excludes token-billed Shared Endpoints from the free compute offer. [Official pricing](https://modal.com/pricing)

GPU use requires a valid payment method. Functions accept explicit GPU resources, including multiple GPUs on supported machines; allocation still depends on capacity and the account. [GPU guide](https://modal.com/docs/guide/gpu)

Modal retired its hosted notebook editor on 23 October 2026. Functions remain a supported compute interface for training and can attach Volumes for saved models. Existing hosted notebooks can be downloaded but not run in the retired editor. [Shutdown notice](https://modal.com/changelog/sunsetting-modal-notebooks), [notebook availability](https://modal.com/docs/guide/notebooks), [Functions](https://modal.com/docs/guide/functions), [model storage](https://modal.com/docs/guide/model-weights)

A Workspace usage budget caps usage before credits; a separate spend limit applies to net charges after credits. Environment budgets are paid-plan features, cover compute only and do not represent the whole invoice. Published spending controls are not evidence that MultiGPU can independently read and enforce the account's settings. [Budgets and spend limits](https://modal.com/docs/guide/budgets)

**Classification:** recurring credit-backed CPU/GPU; payment method required for GPU; paid usage possible. MultiGPU's automatic Functions/Volumes connector exists, but its free-only gate remains closed because the app does not independently verify zero-spend enforcement. That implementation limit must not be misrepresented as absence of Modal's advertised monthly allowance. Confidence: high for the published offer, account access untested.

## Lightning AI Studios — ongoing free CPU, introductory GPU credits

The current pricing page advertises one free CPU Studio and up to 30 starting credits. It explains five credits at registration and 25 more after adding a card, with a 12-month expiry for unused starting credits. Free CPU sessions require restart after four hours. The marketing estimate of up to 80 starting GPU hours is variable by hardware and price; it is not a fixed monthly GPU quota. Paid subscriptions' monthly credits are bundled paid benefits. [Official pricing](https://studio.lightning.ai/pricing/)

Billing documentation confirms a free four-CPU Studio and the first 10 GB of storage; other machines and excess storage consume credits. Promotional grants depend on eligibility and may change. A low-balance graceful shutdown attempt is not a demonstrated independently enforced zero-bill integration. [Billing documentation](https://studio.lightning.ai/docs/platform/overview/faq/billing/)

The GPU demo documentation explicitly conditions no-out-of-pocket GPU hosting on a verified payment method and staying within available credits. This does not establish every training account's card or credit entitlement; inspect the user's account rather than promising cardless free GPU access. [Official hosting guide](https://studio.lightning.ai/docs/platform/build/host-web-apps/host-demos/)

The official Studio SDK supports external command execution, file transfer and start/stop operations, so automated training is technically viable. MultiGPU's implementation requires a dedicated Studio and shutdown authorization. [Studio SDK](https://api.lightning.ai/docs/platform/developers/sdk/studio/)

**Classification:** ongoing free CPU; eligible introductory GPU credits; paid GPU afterward. Do not put it in an unconditional recurring free-GPU group. MultiGPU currently requires explicit paid opt-in for automatic execution while independent free-only billing enforcement is unqualified. Confidence: high for the published categories; grant, card eligibility and live execution untested.

## Local computer — user-owned compute

The app can use its user's CPU and compatible NVIDIA CUDA hardware. It does not grant hardware or a cloud quota; electricity, equipment, disk space and connectivity belong to the user. Other accelerators are not implemented by the built-in recipes. [Official PyTorch installation selector](https://pytorch.org/get-started/locally/)

**Classification:** local/user-owned hardware, outside the free-versus-paid cloud-provider split. MultiGPU's tested configuration and package versions are recorded in [VALIDATION.md](../../VALIDATION.md); that evidence does not qualify every device or operating system.
