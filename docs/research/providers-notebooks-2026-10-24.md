# Notebook-provider compute research — 24 October 2026

Scope: Deepnote, Hex, JetBrains Datalore and Fastero. Only each provider's official website/documentation is used as evidence for its own offering. Findings describe advertised access for a new ordinary account; education grants, institutional contracts, trial promotions and AI-assistant balances do not establish a general free GPU entitlement. No live account or billing test was performed.

## Classification for MultiGPU

| Provider | Recurring no-subscription compute | GPU classification | Recommended label |
| --- | --- | --- | --- |
| Deepnote | Basic CPU notebooks: 2 vCPU, 5 GB RAM; usage and session restrictions | Paid plan / account-qualified credits; free-trial GPU entitlement unverified | Free CPU · Paid GPU |
| Hex | Community CPU notebook, Small profile with 4 GB RAM | Paid Team/Enterprise advanced compute; explicitly excluded from free trials | Free CPU · Paid GPU |
| Datalore | 120 CPU S machine-hours/month, 10 GB storage | Paid Cloud plan and Datalore credits; Free and Cloud trial have no credits | Free CPU · Paid GPU |
| Fastero | Free hosted notebooks/Python; exact machine specifications and execution allowance not published in reviewed pages | No documented GPU entitlement or GPU purchasing interface found | Free notebook compute · GPU unverified |

These classifications are conclusions from the sources below, not a claim that MultiGPU has qualified automatic execution on these services.

## Deepnote

- The current Free plan includes Basic machines (2 vCPU, 5 GB RAM), up to three editors and five projects. Team is advertised at $39/editor/month with annual billing, and advertises monthly CPU and GPU credits. Those credits are bundled with a paid subscription, not a free-tier GPU allocation. The page also offers a 14-day Team trial. Its lower comparison table contains older-looking hardware/allowance entries; do not copy their K80 SKU or the older dollar-equivalent compute allowance into the app. [Official pricing](https://deepnote.com/pricing)
- The machine documentation identifies paid-plan stronger machines and current GPU choices as T4 (16 GB VRAM) and L4 (24 GB VRAM). Deepnote does not currently support attaching the user's own cloud compute through this machine selector. [Selecting hardware](https://deepnote.com/docs/selecting-hardware)
- Free machine-hours are intended to be unlimited, but the provider may restrict unusually high or irregular usage. This is a fair-use service, not a guaranteed fixed pool that can be summed across accounts. [Additional machine hours](https://deepnote.com/docs/machine-hours)
- Free projects stop after eight hours of continuous execution and can stop earlier. Idle shutdown applies when no notebook cells are running and there has been no activity for 15 minutes; terminal code does not prevent shutdown. Team has a 48-hour runtime plus a grace/extension mechanism. A notebook cell should own training, with frequent checkpoints and local export. [Long-running jobs](https://deepnote.com/docs/long-running-jobs)
- The notebook-execution API is explicitly limited to Team and Enterprise. A free account should therefore be offered an assisted CPU notebook workflow, not an automatic API connector. [Deepnote API](https://deepnote.com/docs/deepnote-api)
- New signup advertises a Team trial, but the reviewed sources do not independently establish no-card GPU access or a specific GPU trial-credit grant. Keep trial GPU eligibility unknown until confirmed in the user's account. [Official signup](https://deepnote.com/sign-up)
- Its acceptable-use policy prohibits infrastructure overload and mining, among other abuses. Account aggregation must not become quota circumvention. [Acceptable-use policy](https://deepnote.com/docs/acceptable-use-policy)

**Training fit (inference):** small CPU models, preprocessing and demonstrations on Free; paid GPU training if the account explicitly enables the necessary machine. API integration is a paid-plan candidate. Confidence: high for the free-CPU/paid-GPU split; unknown for trial GPU/card conditions.

## Hex

- Community is a free individual plan with Small compute. The pricing table lists Small as 4 GB RAM and 0.5 CPU; paid Professional includes Medium compute, while Team/Enterprise offer advanced compute add-ons. Advertised GPU rates are $2.93/hour for L4 and $4.06/hour for A10G, billed per minute. The 14-day Team trial needs no payment card, automatically falls back to Community, and is limited to Medium compute. The page's AI-credit allowances pay for Hex's AI features, not GPU kernel time. [Official pricing](https://hex.tech/pricing/)
- Current environment documentation lists Small as 4 GB RAM and four CPUs, conflicting with the pricing page's fractional CPU number. It agrees on CPU-only Small and paid GPU profiles. Display 4 GB RAM and CPU-only, and detect/report actual capacity rather than promising a CPU count. [Environment / compute profiles](https://learn.hex.tech/docs/explore-data/projects/environment-configuration/environment-views)
- Advanced compute requires Team or Enterprise, is disabled until an administrator enables it, and is unavailable during a free trial. Its spend limit stops new advanced-compute runs; existing runs may continue and exceed the limit. It is therefore not proof of a zero-spend hard stop. [Advanced compute administration](https://learn.hex.tech/docs/administration/workspace_settings/compute)
- Official public APIs support listing projects, running published projects and project-run management. Core Admin APIs, including run management, require Team or Enterprise; some observability endpoints are Enterprise-only. Do not promise a free automatic connector from a free notebook UI. [Public API overview](https://learn.hex.tech/docs/api-integrations/api/overview)

**Training fit (inference):** Free is suitable for small CPU experiments in the hosted notebook. GPU training and API orchestration are paid offerings. Do not assign free GPU hours to the no-card trial. Confidence: high for eligibility; CPU-count mismatch explicitly unresolved.

## JetBrains Datalore

- Cloud Free includes 120 CPU S machine-hours per month, 10 GB storage, two concurrent notebooks, notebook export and the Notebook API. Paid Cloud advertises 750 CPU L hours and six computation credits per member per month, plus larger machines and GPU access. The advertised starting annual-billing equivalent is $29.17/user/month; the page also presents $35 monthly pricing. The paid subscription cost still applies when its included credits pay for GPU time. [Official pricing](https://www.jetbrains.com/datalore/buy/)
- The documented 14-day Cloud trial uses CPU S, 120 hours per team, ten GB storage and no Datalore credits. Background execution is disabled, and trial scheduling is restricted to two schedules at least 24 hours apart. These trial limits must not be presented as free GPU access. [Plan and trial comparison](https://www.jetbrains.com/help/datalore/datalore-plans.html)
- GPU and larger CPU machines use Datalore credits. Only Cloud users can purchase credits; Free and trial users cannot obtain ordinary plan credits. Included credits expire monthly; purchased credits do not. Hardware uses a prepaid credit balance with no overdraft, and the displayed balance refreshes approximately every five minutes. Exceptional gift credits are not a general signup entitlement. [Use Datalore credits](https://www.jetbrains.com/help/datalore/use-datalore-credits.html)
- CPU S is documented as two vCPUs and 4 GB RAM. GPU options include single- and multi-GPU instances with spot/on-demand rates. Spot machines can be interrupted; switching to on-demand costs more, so the app must not silently choose that fallback. [CPU and GPU machine options](https://www.jetbrains.com/help/datalore/machine-options.html)
- The Run API supports token-authenticated execution, status and artifact paths; interactive-mode execution updates notebook outputs without separate run artifacts. The pricing's Notebook API entitlement plus these interfaces make a free CPU integration plausible, but endpoint-level Free-account entitlement, cancellation and artifact download still need qualification. [Run API](https://www.jetbrains.com/help/datalore/run-api.html)
- The Cloud terms require staying within the chosen subscription's resource/feature limits. A generic JetBrains evaluation FAQ is not Datalore-specific evidence for GPU access or a particular signup card rule; that card condition remains unverified here. [Cloud terms](https://www.jetbrains.com/legal/docs/datalore/datalore_cloud/)

**Training fit (inference):** Free CPU is useful for small models and preparation. Paid Cloud can train on GPU using prepaid credits, with explicit opt-in to subscription costs. Confidence: high for allowances and no-trial-GPU conclusion; automatic Free CPU lifecycle remains untested.

## Fastero

- Free has no time limit/card requirement, includes hosted notebooks/Python/Streamlit, 500 MB storage and three exports/month. Its initial $20 balance is AI usage; Free has no recurring AI top-up. New accounts receive 14 days of Pro, then downgrade unless they buy Pro. Pro costs $20/editor/month and includes 750 compute-hours/editor/month, shared within the workspace. Pricing lists later compute/storage overages, but says those are currently in a free measuring period. The Free comparison says compute is included with sleeping idle apps; it does not publish a precise Free-hour quota or CPU/RAM/GPU specification. Do not treat a temporary unbilled measuring period as a permanent guarantee. [Official pricing](https://fastero.com/pricing)
- Official hosted-app documentation supports Python notebooks, installing packages and inspecting live containers. It does not document GPU hardware, CUDA, machine selection, distributed training or guaranteed training-runtime duration. Its availability as a notebook service establishes neither GPU access nor successful PyTorch training. [Hosted apps](https://fastero.com/platform/deliver-embed/apps)
- Its official API/webhook page describes API-key access to workspace resources, triggers, workflows and deployments. It is marketing-level evidence for an API, not a verified training execution/status/cancellation/artifact interface or a Free-plan API-entitlement statement. [API and webhooks](https://fastero.com/product/api-webhooks)
- The terms' trial section says a chosen subscription may be charged at the end of the trial, while current pricing says no-card accounts downgrade to Free. This conflict needs provider/account confirmation before enabling any unattended billing-sensitive workflow. The terms also restrict use to offered purposes and restrict unapproved commercial endeavors. [Terms of service](https://fastero.com/legal/terms-of-service)

**Training fit (inference):** retain as an unqualified free notebook/Python candidate, not a free GPU source. Before enabling training, test package installation, actual CPU/RAM, execution lifetime, output downloads and recovery; clarify API/terms/billing conditions. Confidence: high that no GPU entitlement is established by the reviewed sources, medium for suitability as a training runtime.

## Required app corrections implied by this research

1. Separate `free CPU`, `free notebook compute / hardware unverified`, `paid GPU` and `trial / account-specific credits`. Show each resource category alongside its payment and API eligibility.
2. Keep Deepnote, Hex and Datalore visible under free CPU resources; hide them from any filter meaning free GPU training.
3. Label Fastero GPU availability unknown, remove any inference that AI balances buy training compute, and do not expose an automatic training connector until independently qualified.
4. Checkpoints must precede documented runtime and fair-use limits. Never bypass provider inactivity controls with keepalive automation.
5. Keep dates and direct official citations on provider detail pages. Recheck contradictory pricing/docs and actual account entitlements before launch; research is not an entitlement probe.
