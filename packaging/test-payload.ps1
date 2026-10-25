param([string]$Payload, [string]$TestRoot, [switch]$UsePayloadInPlace)
$ErrorActionPreference='Stop'
$repository=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (!$Payload) { $Payload=(Get-Content -LiteralPath (Join-Path $repository 'build/latest-windows-payload.txt') -Raw).Trim() }
if (!$TestRoot) { $TestRoot=Join-Path $repository ('build/smoke install '+[guid]::NewGuid().ToString('N')) }
New-Item -ItemType Directory -Path $TestRoot -Force | Out-Null
if ($UsePayloadInPlace) { $app=[IO.Path]::GetFullPath($Payload) } else {
    $app=Join-Path $TestRoot 'app'
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    Get-ChildItem -LiteralPath $Payload -Force | Copy-Item -Destination $app -Recurse -Force
}
$manifest=Get-Content -LiteralPath (Join-Path $app 'build-manifest.json') -Raw | ConvertFrom-Json
foreach ($entry in $manifest.files) {
    if ($entry.path -match '(^|/)(\.env|\.multigpu|\.git|tests|exports)(/|$)') { throw 'Private or development files entered the installer payload.' }
    $file=Join-Path $app $entry.path
    if ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256) { throw "Installed file checksum mismatch: $($entry.path)" }
}
# The test cannot discover a system, Codex, or Store Python through PATH.
$env:PATH=Join-Path $env:SystemRoot 'System32'
$env:MULTIGPU_PYTHON=''
. (Join-Path $app 'packaging/launcher-common.ps1')
$runtime=Get-MultiGPUEnvironment -AppRoot $app -EnvironmentRoot (Join-Path $TestRoot 'environment')
$identity=(& $runtime.Python -c "import json,sys; print(json.dumps(dict(executable=sys.executable,base_prefix=sys.base_prefix,prefix=sys.prefix,version='.'.join(map(str,sys.version_info[:3])))))") | ConvertFrom-Json
if ($identity.version -ne '3.12.15' -or $identity.prefix -eq $identity.base_prefix -or !$identity.base_prefix.StartsWith($app,[StringComparison]::OrdinalIgnoreCase)) { throw 'The test did not use the relocated bundled Python and isolated environment.' }
$workspace=Join-Path $TestRoot 'workspace'
$connectionFile=Join-Path $workspace 'controller-connection.json'
New-Item -ItemType Directory -Path $workspace -Force | Out-Null
$process=$null
$connection=$null
function Start-TestController {
    $arguments=@('-m','multigpu','--port','0','--no-browser','--data-dir',$workspace,'--connection-file',$connectionFile)
    $line=($arguments | ForEach-Object { ConvertTo-MultiGPUArgument $_ }) -join ' '
    $script:process=Start-Process -FilePath $runtime.Python -ArgumentList $line -WorkingDirectory $app -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $TestRoot 'stdout.log') -RedirectStandardError (Join-Path $TestRoot 'stderr.log')
    $deadline=[DateTime]::UtcNow.AddSeconds(30)
    do {
        if ($script:process.HasExited) { throw ('Bundled controller exited: '+(Get-Content -LiteralPath (Join-Path $TestRoot 'stderr.log') -Raw)) }
        if (Test-Path -LiteralPath $connectionFile) { $script:connection=Get-Content -LiteralPath $connectionFile -Raw | ConvertFrom-Json }
        Start-Sleep -Milliseconds 100
    } until ($script:connection -or [DateTime]::UtcNow -gt $deadline)
    if (!$script:connection) { throw 'The bundled controller did not publish its address.' }
    $bootstrap=Invoke-WebRequest -Uri ($script:connection.url+'/api/bootstrap') -UseBasicParsing -TimeoutSec 5
    $script:headers=@{'X-MultiGPU-Token'=$bootstrap.Headers['X-MultiGPU-Token']}
    $script:bootstrapData=$bootstrap.Content | ConvertFrom-Json
}
function Stop-TestController {
    if ($script:connection) {
        Invoke-RestMethod -Uri ($script:connection.url+'/api/shutdown') -Method Post -Headers $script:headers -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
        if (!$script:process.WaitForExit(20000)) { throw 'The test controller did not shut down cleanly.' }
        if (Test-Path -LiteralPath $connectionFile) { throw 'The stopped controller left a stale connection file.' }
        $script:connection=$null
    }
}
try {
    Start-TestController
    if ($bootstrapData.app.version -ne $manifest.version) { throw 'Payload and controller application versions differ.' }
    if (@($bootstrapData.providers).Count -ne 14 -or @($bootstrapData.research_providers).Count -ne 142) { throw 'Bundled provider and research directories have unexpected counts.' }
    if (@($bootstrapData.research_providers | Where-Object { !$_.research_only -or $_.capabilities.submit -or $_.capabilities.notebook_bundle -or $_.capabilities.automatic_free_only }).Count) { throw 'Research-only entries acquired execution capabilities.' }
    $googleResearch=$bootstrapData.research_providers | Where-Object { $_.id -eq 'google_cloud' }
    $coiledResearch=$bootstrapData.research_providers | Where-Object { $_.id -eq 'coiled' }
    if (!$googleResearch -or $googleResearch.cost_tags -contains 'free_gpu' -or !$coiledResearch -or $coiledResearch.cost_tags -notcontains 'platform_credit') { throw 'Research cost distinctions were lost in the payload.' }
    if (@($bootstrapData.research_documents).Count -ne 13) { throw 'Bundled research document index is incomplete.' }
    $researchDocuments=@()
    foreach ($document in $bootstrapData.research_documents) {
        $observed=Invoke-RestMethod -Uri ($connection.url+'/api/research/documents/'+$document.id) -TimeoutSec 5
        if ($observed.id -ne $document.id -or !$observed.content -or $observed.content.Length -lt 50) { throw ('Bundled research document is unreadable: '+$document.id) }
        $researchDocuments+=$observed.id
    }
    $researchCsv=Invoke-WebRequest -Uri ($connection.url+'/api/research/directory.csv') -UseBasicParsing -TimeoutSec 5
    if (@($researchCsv.Content | ConvertFrom-Csv).Count -ne 142) { throw 'Bundled research comparison CSV is incomplete.' }
    $spec=@{name='Bundled runtime smoke test';recipe='starter';recipe_version=2;providers=@('local');device='cpu';steps=12;batch_size=4;output_dir=(Join-Path $TestRoot 'exports');free_only=$true}
    $plan=Invoke-RestMethod -Uri ($connection.url+'/api/jobs/preview') -Method Post -Headers $headers -ContentType 'application/json' -Body ($spec | ConvertTo-Json) -TimeoutSec 10
    if (!$plan.eligible) { throw ('Bundled local preview failed: '+($plan | ConvertTo-Json -Depth 6)) }
    $spec.preview_digest=$plan.preview_digest
    $job=Invoke-RestMethod -Uri ($connection.url+'/api/jobs') -Method Post -Headers $headers -ContentType 'application/json' -Body ($spec | ConvertTo-Json) -TimeoutSec 10
    $deadline=[DateTime]::UtcNow.AddSeconds(40)
    do {
        Start-Sleep -Milliseconds 100
        $job=Invoke-RestMethod -Uri ($connection.url+'/api/jobs/'+$job.id) -TimeoutSec 5
    } until (($job.status -eq 'completed' -and $job.export_path) -or $job.status -eq 'error' -or $job.export_error -or [DateTime]::UtcNow -gt $deadline)
    if ($job.status -ne 'completed' -or !$job.export_path -or !(Test-Path -LiteralPath (Join-Path $job.export_path 'manifest.json'))) { throw ('Bundled training/export failed: '+(@{id=$job.id;status=$job.status;phase=$job.phase;message=$job.message;export_error=$job.export_error} | ConvertTo-Json)) }
    if ($job.performance.source -ne 'worker' -or $job.performance.completed_steps -ne 12 -or $job.performance.memory.gpu_peak_allocated_bytes -ne $null) { throw 'Bundled performance observations are missing or mislabel unavailable GPU memory.' }
    $snapshot=Invoke-RestMethod -Uri ($connection.url+'/api/runtime') -TimeoutSec 5
    if ($snapshot.PSObject.Properties.Name -contains 'research_providers' -or $snapshot.PSObject.Properties.Name -contains 'providers') { throw 'Runtime polling repeated the static provider catalog.' }
    $unchanged=Invoke-RestMethod -Uri ($connection.url+'/api/jobs/'+$job.id+'?observations_revision='+$job.observations_revision) -TimeoutSec 5
    if (!$unchanged.observations_unchanged -or $unchanged.PSObject.Properties.Name -contains 'metrics') { throw 'Unchanged observations were retransmitted.' }
    $verified=& $runtime.Python -c "import sys; from pathlib import Path; from multigpu.artifacts import verify_export; print(len(verify_export(Path(sys.argv[1]))['files']))" $job.export_path
    if ($LASTEXITCODE -or [int]$verified -lt 1) { throw 'The bundled model export did not pass integrity verification.' }
    Stop-TestController
    Start-TestController
    $restored=Invoke-RestMethod -Uri ($connection.url+'/api/jobs/'+$job.id) -TimeoutSec 5
    if ($restored.status -ne 'completed' -or $restored.export_path -ne $job.export_path) { throw 'The bundled controller did not restore its completed run after restart.' }
    Stop-TestController
    $result=@{passed=$true;python=$identity;app=$app;workspace=$workspace;job_id=$job.id;export_path=$job.export_path;restart_restored=$true;provider_jobs_launched=$false;research_entries=142;research_documents=$researchDocuments;research_csv_entries=142;research_local_api_read=$true;research_execution_disabled=$true;performance_observations=$true;lightweight_runtime=$true;unchanged_history_omitted=$true}
    $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $TestRoot 'acceptance-result.json') -Encoding UTF8
    Write-Output ($result | ConvertTo-Json -Depth 5)
} finally {
    if ($connection -and $process -and !$process.HasExited) { try { Stop-TestController } catch { Write-Warning 'The isolated test controller still needs shutdown.' } }
}
