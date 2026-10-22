# MultiGPU interface

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
