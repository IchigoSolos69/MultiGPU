# MultiGPU interface

## Version 0.5 performance evidence

Keep the existing Developer Tool / CLI Companion direction, framed sheet, Segoe/Consolas typography, blue action token and neutral ledger surfaces. This is a working training dashboard: the user needs measured costs and a deliberate resource choice, not additional decorative KPI tiles. Performance appears as an expandable ledger in the run timeline; unknown hardware/network observations remain explicit. Preview recommendations sit next to the reviewed plan, name their saved-run evidence, and require acceptance followed by another preview. Image acceleration lives inside advanced training settings, defaults off, and explains checkpoint/numerical implications. Reuse 6px controls, 24px content rhythm, existing focus states and responsive stacking; add no motion or fonts.

Actual Chrome review at 1440px and 390px confirmed the ledger, recommendation controls and image options fit the existing frame without horizontal overflow. Sub-second estimates now retain useful precision; an expanded performance ledger stays open during polling even after focus moves. Matching current selections do not show an unnecessary acceptance action. Browser acceptance verifies that accepting a changed suggestion re-previews without launching training. AI Slop Score 2/10; Distinctiveness 7/10. The performance ledger stays deliberately quiet and evidence-led; no new decorative dashboard language was introduced.

## Version 0.4.4 provider research

Extend the existing developer-tool workspace with a compact evidence ledger. The provider directory combines integrated resources and researched options, with separate integration and cost labels. Keep training selectors limited to existing integrations. Search, cost/access filters and 25-row pagination make the larger directory usable; do not create a wall of provider cards.

Reuse the existing Segoe/Consolas typography, neutral sheet surfaces, blue action token, 6px controls and quiet panel borders. A readable in-app report viewer shows the bundled research, source dates and official links. Provider detail sheets lead with integration status, offer conditions and actual training mode. Research reports use restrained headings and horizontally contained tables; phone controls stack and preserve keyboard focus. No remote fonts, decorative motion or new palette is needed.

Chrome review at 1440px and 390px verified directory pagination, readable research tables, contained horizontal scrolling, escaped HTML, failed-read retry, late-response handling and keyboard focus restoration. A mobile header issue was repaired with a nonshrinking 44px close control and scroll reset; its bounds are tested in fresh and reopened readers. Design review: AI Slop Score 2/10; Distinctiveness 7/10. The familiar control-panel frame remains, while the offer/integration evidence comparison and offline research reader carry the product-specific workflow. Live provider execution remains outside this UI qualification.

## Version 0.4 journey refinements

Operate mode and the existing framed workspace remain. Built-in recipes and custom scripts are peer choices. Account verification, allowance, actual hardware and qualification use separate labels. Review shows the exact resolved mode; blocked actions lead to corrections. Manual sessions emphasize notebook handoff/import instead of empty live charts. Recovery shows the saved attempt, explicit console confirmation and its provenance; a focused form keeps the snapshot the user reviewed. Polling updates status and actions without replacing focused controls or selected logs. Named prediction features and verified delivery lead the completed run. Windows recovery and tray notifications use native surfaces.

The primary question is **Where can I train within my allowance?** The first screen answers with real resource readiness, actual runs, and a clear next action. A new workspace has no training charts, generated GPU counts, or invented quotas.

The direction is the design-taste frontend skill's light **framed-sheet** app style: a cool gray L-frame, 220px labelled sidebar, and one white working sheet. The product face comes from its four-block compute mark and the resource/allowance/readiness comparison that leads the overview. Blue belongs to training data and the primary action; navigation uses neutral selection.

## Tokens and layout

- Ground `#eef1f5`, sheet `#ffffff`, inset surface `#f7f8fa`, ink `#202935`, secondary text `#5d6878`, line `#e5e8ed`, control line `#cbd2dc`, blue `#2e62d9`.
- Native Segoe UI Variable/Segoe UI typography without external network requests. Titles 23–27px; body and controls 12–14px; identifiers and logs use Consolas. Tabular numeric alignment throughout.
- 24px content gutter. At a 1440px viewport: 220px sidebar + 9px outside inset + 1211px sheet; sheet content subtracts 68px padding and 2px border. The main grid is flexible content + 24px gap + 286px supporting column.
- One 8px panel radius, 6px controls, 12px outer sheet. Flat panels and quiet rules; a shadow only on the modal and transient toast.
- Sidebar becomes a menu below 1000px. Columns stack below 760px. Provider tables become complete two-line list rows below 540px; form fields stack, controls grow to touch size, and charts remain within the viewport.

## Behavioral rules

- Status, quota, metrics and hardware come from the local API. Missing allowance is unknown, never zero. Refresh time means last successful fetch; retained data is visibly marked if the controller becomes unavailable.
- Poll at five seconds; distinguish that from the trainer's five-minute summaries. Preserve unsaved form inputs and dialog focus during polling.
- Provider setup has source links, restrictions, actual readiness probes and clear guided/automatic/CPU-only distinctions. The UI never collects provider account passwords.
- Free-only is the default. Paid allowance is an explicit choice. Preview shows eligibility and blockers before any launch. Guided preparation creates a downloadable notebook bundle and never implies that a managed runtime was launched.
- Charts render observed loss/step pairs only, with unsmoothed line segments, one blue series and labeled axes. Empty runs explain when the first measurement will appear.
- Native dialog semantics, keyboard dismissal, visible focus, associated form labels, readable errors, safe external links, responsive layouts and reduced-motion support are required.

## Screen questions

- Overview: Where can I train within my allowance?
- Providers: Which of my providers can run this workload, and what setup remains?
- New training: What will run, where, and within which limits?
- Training runs: What happened to each run?
- Run detail: Is training progressing, and is its checkpoint safe?
- Settings: Where is local data stored, and which native setup is required?

No sample account data or synthetic training history is included.

## Visual quality checks

The integrated local dashboard was inspected in a real Chrome browser at desktop, tablet, and 390px phone widths. The first round reported zero failures and three warnings. Screenshots showed a coherent frame, honest first-run state, and a useful next action. Review identified small secondary text, low contrast on the version label, and narrow phone footer gutters; the implementation raises working labels and controls to 13px, strengthens version contrast, and gives the mobile footer 16px side padding. The phone header includes the MultiGPU mark and name. The final browser inspection is performed after these corrections.

The strongest part is the provider readiness/allowance comparison: its source, uncertain quota, and manual setup states remain separate. Typography and density prioritize a working developer tool over a large empty dashboard. Loss charts have one blue line drawn from recorded metrics. There is no sample account history to replace.

Static lint: zero failures. Its warnings for monospace paths/identifiers and shadows on the dialog, toast, and mobile navigation are intentional, permitted app-screen conventions. No resting panel has a shadow. Native browser automation through CUA was unavailable on this host; the implementation team used the skill's real Chrome inspector instead.

Final Chrome measurements after corrections: **0 failures and 0 warnings at 1440, 768 and 390 pixels**. Every fold and full-page screenshot was reviewed, as were real completed-training views on desktop and phone. Playwright acceptance exercised fourteen directory entries, search/details, CPU training, verified model download, pause/resume, assisted notebook download and mobile navigation. Tests use an isolated workspace. Axis labels are redrawn when the viewport changes.

Final critique: the resource table answers the screen's question first, and its setup actions lead directly to training. Working text is readable and colors communicate readiness and observed loss. Numbers and empty states come from the controller. Long checkpoint paths wrap, and raw logs remain technical by design. Anti-slop score: **2/10** (a familiar control-panel shell); distinctiveness **7/10**; implementation readiness **8/10**, limited by untested live cloud accounts and CUDA. No imagery, remote fonts or fabricated account statistics require replacement.

## Version 0.2 journey

The resource comparison remains the product's starting point. Training setup is
now four stages: Task & data, Training, Compute & limits, and Review. Tabular and
image classification have separate input guidance; custom scripts and detailed
hyperparameters use progressive disclosure. The selected primary resource and
fallback order are explicit. Drafts persist locally and restore without execution
confirmations.

Run pages prioritize execution state and required action, then readable events,
observed loss, evaluation, recovery and local delivery. Technical logs and raw
prediction payloads are expandable. A verification time describes the recorded
artifact verification; downloads and prediction verify the actual files again.
Controller connectivity and worker/progress observation times remain separate.

Version 0.2 browser acceptance uses disposable workspaces, including real CPU
training/pause/resume/export/inference, guided preparation and failed-import
handling, ordered-resource restoration, keyboard focus and 390px layouts.
Screenshots live under .test-runs/browser/. Earlier score and three-viewport
inspection notes above describe the 0.1 baseline; they are not a new certification
of every 0.2 state. Live provider billing, CUDA and clean-machine installer
qualification are separate release gates.

## Version 0.3 setup surfaces

Mode: Operate. Preserve the framed workspace, Segoe typography, existing blue
action color, neutral surfaces and compact working controls. The Windows shell
supplies its own title bar and taskbar identity. External account/authentication
pages open in the user's browser; application navigation stays in the window.

Provider setup and guided notebook execution use a short sequence with a visible
step list, one active instruction group, contextual preparation/check actions and
Back/Continue controls. Instructions are readable without marking them complete.
User acknowledgements and observed readiness use distinct labels. Progress is
saved in the workspace. Instruction files are not a primary action; only actual
training notebooks, necessary payloads and verified model outputs are downloads.
