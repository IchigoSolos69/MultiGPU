/* Research browsing only, against an isolated controller. Never opens provider accounts or starts compute. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');

(async () => {
  const base = process.env.MULTIGPU_TEST_URL;
  assert(base && /^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(base), 'Set MULTIGPU_TEST_URL to an isolated controller.');
  assert(!base.endsWith(':8765'), 'Do not test against the live user workspace.');
  const bootstrapResponse = await fetch(base + '/api/bootstrap');
  assert(bootstrapResponse.ok, 'The isolated controller is unavailable.');
  const metadata = await bootstrapResponse.json();
  assert.equal(metadata.research_providers.length, 142);
  assert.equal(new Set(metadata.research_providers.map(p => p.id)).size, 142);
  assert.equal(metadata.providers.length, 14);
  assert(metadata.research_providers.every(p => p.research_only && p.integration === 'research'));
  assert(metadata.research_providers.every(p => !p.capabilities.submit && !p.capabilities.notebook_bundle));
  const originalIds = new Set(metadata.providers.map(p => p.id));
  assert(metadata.research_providers.every(p => !originalIds.has(p.id)), 'Research entries leaked into the supported resource pool.');
  assert(metadata.research_documents.some(d => d.id === 'additional-providers'));
  const readDocument = async id => {
    const result = await fetch(base + '/api/research/documents/' + encodeURIComponent(id));
    assert(result.ok, `Bundled research document ${id} is unavailable.`);
    return result.json();
  };
  const [directoryDocument, gpuDocument] = await Promise.all([
    readDocument('additional-providers'), readDocument('providers-gpu'),
  ]);
  const out = path.resolve('.test-runs/browser');
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  let providerMutations = 0, jobLaunches = 0;
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (request.method() !== 'POST') return;
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/providers/') || pathname.startsWith('/api/desktop/provider-')) providerMutations++;
    if (pathname === '/api/jobs' || pathname === '/api/jobs/preview') jobLaunches++;
  });
  const dialog = page.locator('#provider-dialog');
  const search = () => page.locator('#provider-search');
  const cost = () => page.locator('#provider-cost-filter');
  const researchRow = id => page.locator(`[data-research-provider="${id}"]`);
  const noPageOverflow = async label => {
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${label} overflows the viewport.`);
  };
  const closeDialog = async () => {
    if (await dialog.isVisible()) await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
  };
  const chooseCost = async value => {
    await cost().selectOption(value);
    await page.waitForFunction(value => document.querySelector('#provider-cost-filter')?.value === value, value);
  };
  const researchCloseBounds = async label => {
    const bounds = await dialog.getByRole('button', { name: 'Close research', exact: true }).boundingBox();
    const viewport = page.viewportSize();
    const detail = await dialog.evaluate(element => ({
      scrollTop: element.scrollTop,
      dialog: element.getBoundingClientRect().toJSON(),
      header: element.querySelector('.dialog-header')?.getBoundingClientRect().toJSON(),
    }));
    return { label, bounds, viewport, ...detail };
  };
  const assertCloseInsideViewport = ({ label, bounds, viewport }) => {
    assert(bounds, `${label}: the Close research button has no visible bounds.`);
    assert(bounds.width >= 24 && bounds.height >= 24, `${label}: Close research is smaller than a usable target: ${JSON.stringify(bounds)}`);
    assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= viewport.width && bounds.y + bounds.height <= viewport.height,
      `${label}: Close research is outside the viewport: ${JSON.stringify({ bounds, viewport })}`);
  };
  try {
    await page.goto(base + '/#providers');
    await page.getByRole('heading', { name: 'Your compute providers', exact: true }).waitFor();
    await page.evaluate(() => clearInterval(state.pollTimer));
    const firstPageIds = await page.locator('.provider-table tbody tr').evaluateAll(rows => rows.map(row => row.dataset.providerId || row.dataset.researchProvider));
    assert(firstPageIds.length > 0 && firstPageIds.length <= 25, 'The expanded directory must be paginated.');
    const nextPage = page.locator('[data-action="directory-page"][data-page="1"]');
    await nextPage.click();
    const secondPageIds = await page.locator('.provider-table tbody tr').evaluateAll(rows => rows.map(row => row.dataset.providerId || row.dataset.researchProvider));
    assert.notDeepEqual(secondPageIds, firstPageIds, 'Pagination did not change the visible providers.');
    await search().fill('Runpod');
    await researchRow('runpod').waitFor();
    assert.equal(await page.locator('.provider-table tbody tr').count(), 1);
    await page.locator('[data-action="research-provider"][data-id="runpod"]').first().click();
    await dialog.getByText('Integration not implemented', { exact: true }).waitFor();
    assert((await dialog.textContent()).includes('Runpod'));
    assert.equal(await dialog.locator('[data-action="probe"],[data-action="provider-signin"],[data-action="setup-next"]').count(), 0);
    assert.equal(await dialog.getByRole('button', { name: /^(Start training|Train|Set up|Save & check)$/i }).count(), 0);
    await noPageOverflow('Desktop provider detail');
    await page.screenshot({ path: path.join(out, 'research-provider-desktop.png'), fullPage: false });
    await closeDialog();
    assert(await researchRow('runpod').locator('.provider-title').evaluate(node => node === document.activeElement), 'Closing research did not restore keyboard focus to its provider.');

    // Published free GPU offers must not absorb unverified Paperspace or platform-only Coiled credits.
    await chooseCost('free_gpu');
    await search().fill('Paperspace');
    assert.equal(await page.locator('[data-provider-id="paperspace"]').count(), 0);
    await search().fill('Coiled');
    assert.equal(await researchRow('coiled').count(), 0);
    await chooseCost('credits');
    await search().fill('AceCloud');
    await researchRow('acecloud').waitFor();
    await search().fill('Yotta');
    await researchRow('yotta').waitFor();
    await chooseCost('all');
    await search().fill('');
    await page.locator('[data-action="filter"][data-filter="research"]').click();
    assert.equal(await page.locator('.provider-table [data-provider-id]').count(), 0);
    assert(await page.locator('[data-research-provider]').count() > 0);
    await page.screenshot({ path: path.join(out, 'research-directory-desktop.png'), fullPage: false });

    // A bundled report is readable in-app, including tables and external source links.
    await page.locator('[data-action="research-library"]').click();
    await page.locator('#research-content table').first().waitFor();
    assert((await page.locator('#research-content').textContent()).includes('142'));
    const sourceLinks = page.locator('#research-content a[href^="https://"]');
    assert(await sourceLinks.count() > 0);
    assert(await sourceLinks.evaluateAll(links => links.every(link => link.rel.includes('noopener'))));
    await page.screenshot({ path: path.join(out, 'research-library-desktop.png'), fullPage: false });

    // A document error can be retried without dismissing the reader or losing the selected report.
    await page.route('**/api/research/documents/provider-audit', route => route.fulfill({ status: 503, json: { error: 'Fixture research read failed.' } }), { times: 1 });
    await page.locator('#research-document-select').selectOption('provider-audit');
    await dialog.getByText('Fixture research read failed.', { exact: false }).waitFor();
    await page.locator('[data-action="research-retry"]').click();
    await page.locator('#research-content').getByText('Paperspace', { exact: false }).first().waitFor();

    // A slow response arriving after Escape must not reopen or replace another dialog.
    let heldRoute, release, markHeld;
    const held = new Promise(resolve => { release = resolve; });
    const routeEntered = new Promise(resolve => { markHeld = resolve; });
    await page.route('**/api/research/documents/providers-gpu', async route => {
      heldRoute = route;
      markHeld();
      await held;
      await route.fulfill({ json: gpuDocument });
    }, { times: 1 });
    await page.locator('#research-document-select').selectOption('providers-gpu');
    await routeEntered;
    assert(heldRoute);
    await closeDialog();
    const responseSeen = page.waitForResponse(response => new URL(response.url()).pathname === '/api/research/documents/providers-gpu');
    release();
    await responseSeen;
    await page.waitForTimeout(100);
    assert(!(await dialog.isVisible()), 'A stale document response reopened the reader.');

    // Markdown is untrusted display content: literal HTML and unsafe links cannot become active DOM.
    const unsafeDocument = {
      ...directoryDocument,
      content: '# Reader safety fixture\n\n<script>window.__researchExecuted=true</script>\n\n<img src="x" onerror="window.__researchExecuted=true">\n\n[Unsafe link](javascript:alert(1))\n\n[Official source](https://example.com/research)\n\n| Resource | Status |\n|---|---|\n| Fixture | Read only |',
    };
    await page.route('**/api/research/documents/additional-providers', route => route.fulfill({ json: unsafeDocument }), { times: 1 });
    await page.locator('[data-action="research-library"]').click();
    await page.locator('#research-content').getByText('Reader safety fixture', { exact: true }).waitFor();
    assert.equal(await page.locator('#research-content script, #research-content img, #research-content a[href^="javascript:"]').count(), 0);
    assert.equal(await page.evaluate(() => window.__researchExecuted), undefined);
    assert((await page.locator('#research-content').textContent()).includes('<script>'));
    await closeDialog();
    assert(await page.locator('[data-action="research-library"]').evaluate(node => node === document.activeElement), 'Closing the reader did not restore keyboard focus to the library button.');

    // Only established resources appear in the training resource selector.
    const bootstrap = await fetch(base + '/api/bootstrap');
    const token = bootstrap.headers.get('x-multigpu-token');
    const draftResponse = await fetch(base + '/api/draft', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MultiGPU-Token': token },
      body: JSON.stringify({ draft: { wizard_step: 2, primary: 'local', fallbacks: [], dataset_mode: 'sample', spec: { name: 'Research directory acceptance', recipe: 'starter', recipe_version: 2, providers: ['local'], device: 'cpu', steps: 12, free_only: true } } }),
    });
    assert(draftResponse.ok, 'Could not prepare an isolated training draft.');
    await page.goto(base + '/?research-acceptance=training#new');
    await page.getByLabel('Primary resource', { exact: true }).waitFor();
    const trainingIds = await page.getByLabel('Primary resource', { exact: true }).locator('option').evaluateAll(options => options.map(option => option.value));
    assert(trainingIds.length > 0);
    assert(!trainingIds.some(id => metadata.research_providers.some(provider => provider.id === id)), 'Research-only providers were offered for training.');

    // Narrow layout keeps filters, provider details and wide research tables inside the window.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(base + '/#providers');
    await page.getByRole('heading', { name: 'Your compute providers', exact: true }).waitFor();
    await page.locator('[data-action="filter"][data-filter="all"]').click();
    await chooseCost('all');
    await search().fill('Runpod');
    await researchRow('runpod').waitFor();
    await noPageOverflow('Mobile directory');
    await page.screenshot({ path: path.join(out, 'research-directory-mobile.png'), fullPage: false });
    await page.locator('[data-action="research-library"]').click();
    await page.locator('#research-content table').first().waitFor();
    const mobileHeaderBounds = [await researchCloseBounds('Fresh mobile research reader')];
    await closeDialog();
    await page.locator('[data-action="research-provider"][data-id="runpod"]').first().click();
    await dialog.getByText('Integration not implemented', { exact: true }).waitFor();
    await noPageOverflow('Mobile provider detail');
    await page.screenshot({ path: path.join(out, 'research-provider-mobile.png'), fullPage: false });
    await closeDialog();
    await page.locator('[data-action="research-library"]').click();
    await page.locator('#research-content table').first().waitFor();
    await noPageOverflow('Mobile research reader');
    mobileHeaderBounds.push(await researchCloseBounds('Mobile reader after provider details'));
    await fs.writeFile(path.join(out, 'research-mobile-header-bounds.json'), JSON.stringify(mobileHeaderBounds, null, 2));
    console.log(JSON.stringify({ mobile_header_bounds: mobileHeaderBounds }));
    mobileHeaderBounds.forEach(assertCloseInsideViewport);
    await page.screenshot({ path: path.join(out, 'research-library-mobile.png'), fullPage: false });
    await closeDialog();

    assert.equal(providerMutations, 0, 'Reading research triggered provider setup or authentication.');
    assert.equal(jobLaunches, 0, 'Reading research previewed or launched training.');
    assert.deepEqual(errors, []);
    const proof = { passed: true, research_entries: metadata.research_providers.length, supported_resources: metadata.providers.length,
      pagination: true, cost_filters: true, bundled_reader: true, document_retry: true, stale_response_ignored: true,
      markdown_html_escaped: true, research_excluded_from_training: true, mobile_reader_close_inside_viewport: true,
      viewports: [1440, 390], provider_mutations: providerMutations, training_launches: jobLaunches };
    await fs.writeFile(path.join(out, 'research-proof.json'), JSON.stringify(proof, null, 2));
    console.log(JSON.stringify(proof));
  } catch (error) {
    await page.screenshot({ path: path.join(out, 'research-failure.png'), fullPage: false }).catch(() => {});
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
