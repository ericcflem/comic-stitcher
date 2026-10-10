param([string]$RuntimeFile = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$minimum = [version]'14.44.35211.0'
$officialUrl = 'https://aka.ms/vc14/vc_redist.x64.exe'
$terms = Get-Content -LiteralPath 'installer\microsoft-terms-review.json' -Raw | ConvertFrom-Json
foreach ($entry in $terms.files.PSObject.Properties) {
    $path = Join-Path $PSScriptRoot $entry.Name
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) {
        throw "Retained Microsoft terms/evidence changed: $($entry.Name). Review before building."
    }
}
$folder = Join-Path $PSScriptRoot 'vendor\microsoft'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$destination = Join-Path $folder 'vc_redist.x64.exe'
$download = Join-Path $folder 'vc_redist.download'
function Assert-MicrosoftInstaller([string]$Path) {
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    if ($signature.Status -ne 'Valid' -or -not $signature.SignerCertificate -or
        $signature.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Microsoft Corporation(,|$)') {
        throw 'Runtime must have a valid Microsoft Corporation Authenticode signature.'
    }
    $info = (Get-Item -LiteralPath $Path).VersionInfo
    $version = [version]::new($info.FileMajorPart, $info.FileMinorPart, $info.FileBuildPart, $info.FilePrivatePart)
    if ($version -lt $minimum) { throw "Runtime installer $version is older than $minimum." }
    if ($info.ProductName -notmatch 'Visual C\+\+' -or $info.ProductName -notmatch 'x64') { throw 'File is not a Visual C++ redistributable installer.' }
    $hash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $terms.runtime_sha256 -or $version.ToString() -ne $terms.runtime_version) {
        throw 'Microsoft prerequisite differs from the reviewed version/hash. Retain the matching executable or review the new runtime and its agreement before building.'
    }
    return [ordered]@{
        official_download = $officialUrl
        reviewed_download_url = $terms.reviewed_download_url
        sha256 = $hash
        version = $version.ToString()
        signer_subject = $signature.SignerCertificate.Subject
        signer_thumbprint = $signature.SignerCertificate.Thumbprint
        signature_status = $signature.Status.ToString()
        minimum_runtime_version = $minimum.ToString()
        checked_utc = [DateTime]::UtcNow.ToString('o')
        terms_record_sha256 = (Get-FileHash -LiteralPath 'installer\microsoft-terms-review.json' -Algorithm SHA256).Hash.ToLowerInvariant()
        terms_eula_id = $terms.eula_id
        redistribution_clearance = $false
    }
}
if ($RuntimeFile) {
    $supplied = (Resolve-Path -LiteralPath $RuntimeFile).Path
    $null = Assert-MicrosoftInstaller $supplied
    if ($supplied -ne $destination) { Copy-Item -LiteralPath $supplied -Destination $destination -Force }
} elseif (-not (Test-Path -LiteralPath $destination)) {
    Write-Host 'Downloading the official Microsoft Visual C++ x64 installer (not installing it)...'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $terms.reviewed_download_url -OutFile $download -UseBasicParsing
        $null = Assert-MicrosoftInstaller $download
        Move-Item -LiteralPath $download -Destination $destination -Force
    } finally {
        if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download }
    }
}
$record = Assert-MicrosoftInstaller $destination
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $folder 'runtime-provenance.json') -Encoding UTF8
@(
    '// Generated only after Microsoft signature/version verification.'
    ('#define MicrosoftRuntimeSHA256 "' + $record.sha256 + '"')
    ('#define MicrosoftRuntimeVersion "' + $record.version + '"')
    ('#define MicrosoftComponentTermsSHA256 "' + $terms.files.'installer/component-terms.txt' + '"')
) | Set-Content -LiteralPath (Join-Path $folder 'runtime-verified.iss') -Encoding ASCII
Write-Host "PASS: Microsoft installer signature verified; version $($record.version). Cached for repeatable builds."
