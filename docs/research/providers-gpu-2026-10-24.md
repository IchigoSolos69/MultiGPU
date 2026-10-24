# Official-source review: Paperspace, Kaggle and consumer Colab

Checked **24 October 2026**, using the client date. This is a public-document review, not an account entitlement test. Only provider-owned documentation, pricing pages and repositories are used below. A free account, free editor or subscription-included instance does not establish universally available free GPU compute.

## Classification for MultiGPU

| Provider | Publicly documented no-payment compute | Paid compute | Recommended directory classification | Confidence |
| --- | --- | --- | --- | --- |
| Kaggle Notebooks | CPU notebooks and quota-limited GPUs/TPUs | No separate general paid Kaggle GPU plan established; an eligible active Colab subscription can grant promotional extra Kaggle GPU hours | **Free CPU + free GPU, quota/capacity restricted** | High for documented free compute; actual account availability untested |
| Google Colab consumer | CPU, GPU and TPU runtimes with dynamic access restrictions | Pro, Pro+, Pay As You Go and eligible paid Google AI plan benefits | **Free CPU + free GPU, dynamic availability; optional paid upgrades** | High for documented free compute; actual account availability untested |
| Paperspace Notebooks | Current DigitalOcean documentation still lists free C4 CPU and M4000 GPU notebooks, but this research cannot confirm that a new account can obtain either | Hourly CPU/GPU machines; paid Gradient plans and storage overages | **Paid available; free availability unverified/disputed — exclude from the dependable free pool until an actual entitlement is verified** | High for paid availability; insufficient evidence for new-account free availability |

The Paperspace recommendation is deliberately conservative. It is not a claim that an official retirement announcement was found. The user's report that no free compute is offered is material account evidence, while the public documentation still advertises free instances. Do not continue to promise a free M4000 in the app.

## Paperspace: evidence and unresolved availability

The [DigitalOcean pricing reference](https://docs.digitalocean.com/products/paperspace/pricing/), marked **last verified 13 July 2026**, separately lists ordinary M4000 compute at $0.45/hour and a free-machine table containing C4 CPU and M4000 GPU on the Free/Pro/Growth plans. The Free Gradient plan includes 5 GB storage; additional storage is metered. Higher free-machine SKUs are included only in paid subscriptions and therefore are not a no-payment tier. Stopped paid machines can continue to incur charges for attached storage, IP addresses and add-ons. These distinctions mean the $0.45/hour M4000 entry alone does not prove that the distinct free M4000 product was removed.

The [notebook features page](https://docs.digitalocean.com/products/paperspace/notebooks/details/features/), also marked **13 July 2026**, says: **“Notebooks include a free GPU plan.”** It describes a six-hour maximum auto-shutdown timeout on free-tier machines. The [creation guide](https://docs.digitalocean.com/products/paperspace/notebooks/getting-started/create-jupyter-notebook/), marked **22 January 2026**, restricts free machines to personal accounts and also specifies six hours.

The newer [machine selection guide](https://docs.digitalocean.com/products/paperspace/notebooks/how-to/create-notebooks/), marked **3 September 2026**, still describes a pending notebook queuing for an available free machine and recommends paid machines for immediate access. It does not establish that capacity is currently obtainable for a new account.

The [marketing pricing page](https://www.paperspace.com/pricing) has internally overlapping plan blocks: a $0 plan with hourly compute, another block distinguishing paid instances from the free tier, and an FAQ exempting free instance types from compute charges. Its paid Pro/Growth labels must not be interpreted as a recurring free GPU credit grant. No dated free-tier shutdown notice, new-account exclusion notice, or universal current free capacity guarantee was found on the reviewed official pages. The documentation therefore does not settle the user's observed absence of free machines.

Eligibility/payment detail remains uncertain. An [older official ML Showcase FAQ](https://ml-showcase.paperspace.com/guide) advertises free CPU/GPU without a card, but it is undated and cannot establish today's new-account policy. The [account/team documentation](https://docs.digitalocean.com/products/paperspace/accounts-and-teams/) describes account verification and service limits; neither proves new-account free entitlement. Do not inherit DigitalOcean Droplet promotional credits or general billing eligibility into Paperspace without explicit applicability.

The [machine features reference](https://docs.digitalocean.com/products/paperspace/machines/details/features/), marked **1 September 2026**, also says free-plan free notebooks are public, one free notebook may run at a time, and one hour of inactivity shuts it down. This is a separate privacy/continuity issue even if a $0 instance becomes available: never upload private training data/code on the assumption that a free account supplies private execution.

For integration, the [Paperspace product updates](https://docs.digitalocean.com/products/paperspace/) state that legacy Gradient/Core API endpoints became unavailable on **15 May 2024** and applications must use the new API. Some notebook pages still reference old tools. An assisted user-launched notebook is the conservative app mode until the current documented notebook lifecycle API is tested. Paid machine provisioning should remain separate from any unverified free notebook path.

**App implication:** keep Paperspace in a paid/entitlement-unverified section, show a documentation conflict note, remove unconditional “Free M4000” advertising, and require a current account-level $0 instance selection before any free-only training eligibility. A pending capacity queue must not trigger a paid fallback.

## Kaggle Notebooks

The [official efficient-GPU guide](https://www.kaggle.com/docs/efficient-gpu-usage) establishes free GPUs and a weekly quota, described as **30 hours or sometimes higher depending on demand/resources**. It recommends using the API to submit notebooks without an interactive session. This is a documented ongoing allowance, not introductory cash credit; it is not a promise that every account can immediately allocate a GPU.

The [official notebook documentation](https://www.kaggle.com/docs/notebooks) describes free CPU/GPU/TPU notebooks, limited accelerator availability, approximately 12-hour CPU/GPU sessions, 20 GB saved working space, and additional ephemeral scratch storage. Some hardware text is stale: it still names P100/v3. It also describes promotional extra Kaggle GPU hours for a linked active Colab subscription; the promotion can change/end, does not consume Colab compute units, and is not a new generally purchasable Kaggle GPU tier.

The newer [provider-owned CLI reference](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/kernels.md) explicitly treats P100 as retired: the server substitutes the default **NvidiaTeslaT4**, described as **T4 ×2**. Retired TPU v3 aliases similarly resolve to v5e-8. The CLI supports authenticated push/run, a bounded timeout, status, logs and versioned output retrieval. Therefore request and then observe actual hardware; do not freeze the outdated P100 marketing claim into eligibility. Kernel deletion is documented, but immediate cancellation semantics still need live validation.

The [official quota reference](https://github.com/Kaggle/kaggle-cli/blob/main/skills/references/quota.md) exposes GPU/TPU used, remaining, total and refresh time. Missing quota information stays unknown; it is not unlimited. This command is not evidence about billing or storage quotas.

For account verification, the provider's [official product announcement](https://www.kaggle.com/product-announcements/558078) states that phone verification already applies to notebooks and other platform features. Only the Kaggle staff announcement is relied on, not community replies. The [benchmark documentation](https://www.kaggle.com/docs/benchmarks) adds identity requirements for newer accounts executing benchmark task notebooks; that narrower rule should not be asserted as a universal ordinary-training requirement. No card requirement or independent paid Kaggle compute subscription was established by the reviewed official pages. Eligibility must still be checked in the user's account.

**App implication:** primary free automatic connector, read real weekly allowance, handle capacity queues and verification failures explicitly, preserve private outputs, and observe allocated hardware. Do not create extra accounts or evade provider limits.

## Google Colab consumer

The [current official FAQ](https://research.google.com/colaboratory/faq.html) confirms free CPU/GPU/TPU access with no guaranteed hardware or fixed published overall quota. Free sessions last at most 12 hours depending on availability and idle VMs can be deleted. Free-tier restrictions exclude external distributed workers, remote-control shells and primarily bypassing the notebook UI. Paid products provide separate entitlements; their names do not promise continuous GPU availability.

The [official paid-products page](https://colab.research.google.com/signup) requires sign-in in the research fetch, so region-specific current prices and purchase/card requirements were not verified. The app should show live provider offerings rather than hard-coded prices. A free runtime should not require buying a paid plan merely to use the assisted notebook flow.

The [official Colab CLI](https://github.com/googlecolab/google-colab-cli) documents provisioning, script/notebook execution, file retrieval, runtime termination and compute-unit usage inspection. It currently supports **Linux/macOS only; Windows is unsupported**. Its existence does not independently authorize prohibited free-tier external-worker behavior or guarantee a free accelerator entitlement. Maintain the user-operated notebook flow for free accounts; qualify CLI automation separately against account entitlement and current terms.

**App implication:** assisted execution with observed account entitlement, user-controlled recovery and no quota/keepalive workaround. Qualify paid automation separately.

## Verification still required

- Paperspace: a fresh account's actual available $0 machine types, billing eligibility, capacity and any support clarification resolving the public-doc conflict.
- Kaggle: account verification, returned weekly quota, actual accelerator, live interruption/cancellation and output recovery.
- Colab: actual runtime entitlement, current paid offering in the user's region, allowed automated mode, and real Drive persistence/reconnection.

No provider account was created or modified, no paid resource was launched, and no live GPU entitlement was certified by this research.
