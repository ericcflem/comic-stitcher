param([string]$Compiler = '', [string]$RuntimeFile = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$policy = Get-Content -LiteralPath 'installer\shared-runtime-inputs.json' -Raw | ConvertFrom-Json
if ($policy.minimum_runtime_version -ne '14.44.35211.0') {
    throw 'Update and retest the Inno prerequisite/version policy before changing the runtime minimum.'
}
$python = Join-Path $PSScriptRoot '.venv-release\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Use the pinned .venv-release testing environment.' }
& $python package_shared_runtime.py verify 'dist\ComicStitcher'
if ($LASTEXITCODE -ne 0) { throw 'Shared runtime bundle validation failed. Run rebuild_release_windows.bat first.' }
& $python installer_evidence.py capture 'dist\ComicStitcher' --record 'runtime-integration\input-bundle.json'
if ($LASTEXITCODE -ne 0) { throw 'Bundle inventory/notice preflight failed.' }
& (Join-Path $PSScriptRoot 'prepare_microsoft_runtime_windows.ps1') -RuntimeFile $RuntimeFile
& $python verify_microsoft_terms.py --runtime 'vendor\microsoft\vc_redist.x64.exe'
if ($LASTEXITCODE -ne 0) { throw 'Microsoft prerequisite terms/evidence validation failed.' }
$candidates = @($Compiler, $env:INNO_ISCC)
$onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($onPath) { $candidates += $onPath.Source }
foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs'), ${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
    foreach ($version in @('7', '6')) { $candidates += Join-Path $base "Inno Setup $version\ISCC.exe" }
}
$iscc = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $iscc) { throw 'ISCC.exe not found. Supply -Compiler or set INNO_ISCC.' }
$info = Get-Item -LiteralPath $iscc
New-Item -ItemType Directory -Force -Path 'runtime-integration' | Out-Null
& $iscc 'setup_windows.iss' 2>&1 | Tee-Object -FilePath 'runtime-integration\compiler.log'
if ($LASTEXITCODE -ne 0) { throw 'Inno compilation failed.' }
$installer = 'installer_dist\ComicStitcher-Setup-0.7.0.exe'
if (-not (Test-Path -LiteralPath $installer)) { throw 'Expected installer was not produced.' }
& $python installer_evidence.py compare 'dist\ComicStitcher' --record 'runtime-integration\input-bundle.json' --output 'runtime-integration\input-unchanged-result.json'
if ($LASTEXITCODE -ne 0) { throw 'Bundle changed during compilation.' }
$record = [ordered]@{
    compiler_name = $info.Name
    compiler_file_version = $info.VersionInfo.FileVersion
    compiler_sha256 = (Get-FileHash -LiteralPath $iscc -Algorithm SHA256).Hash.ToLowerInvariant()
    installer_name = (Split-Path -Leaf $installer)
    installer_sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    setup_script_sha256 = (Get-FileHash -LiteralPath 'setup_windows.iss' -Algorithm SHA256).Hash.ToLowerInvariant()
    prerequisite_script_sha256 = (Get-FileHash -LiteralPath 'installer\microsoft_runtime.iss' -Algorithm SHA256).Hash.ToLowerInvariant()
    component_terms_script_sha256 = (Get-FileHash -LiteralPath 'installer\microsoft_terms.iss' -Algorithm SHA256).Hash.ToLowerInvariant()
    component_terms_record_sha256 = (Get-FileHash -LiteralPath 'installer\microsoft-terms-review.json' -Algorithm SHA256).Hash.ToLowerInvariant()
    microsoft_installer = (Get-Content -LiteralPath 'vendor\microsoft\runtime-provenance.json' -Raw | ConvertFrom-Json)
    licensing_clearance = $false
}
$record | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath 'runtime-integration\installer-build.json' -Encoding UTF8
Write-Host 'PASS: installer compiled with the verified Microsoft prerequisite. Output: installer_dist'
