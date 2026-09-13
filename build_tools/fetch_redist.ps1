<#
.SYNOPSIS
    Downloads the third-party driver setups that installer.nsi bundles.

.DESCRIPTION
    Nimbus needs two drivers it does not own: vJoy (DirectInput output) and
    ViGEmBus (Xbox 360 output). The installer carries both so a user with a bad
    connection, a proxy, or no connection at all still ends up with working
    drivers, and so a moved or renamed release asset can never break an
    installer that already shipped.

    Each file is pinned by SHA-256 and checked for a valid Authenticode
    signature from the expected publisher before it is accepted. A file that
    fails either check is deleted, not kept.

    Run this before makensis. The output lands in build_tools\redist\, which is
    gitignored: these are third-party binaries, not our source.

.PARAMETER Force
    Re-download even when a verified copy is already present.
#>
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$redist = Join-Path $PSScriptRoot 'redist'

# Both projects are frozen. ViGEmBus was archived on 2023-11-02 with 1.22.0 as
# its final release. vJoy is deliberately the 2016 2.1.9.1 in Justin Shafer's
# 2019 build, not the newer njz3 2.2.1: on Windows 11 the 2.2.x driver fails
# to load with 0xC000009A (WdfCollectionCreate; njz3/vJoy issue 17, open since
# 2023, reproduced on the dev machine on 2026-09-06 on a clean boot), and the
# BrunnerInnovation 2.2.2.0 build is reported failing the same way. The 2.1.9.1
# build's vJoy.sys and catalog are signed by the Microsoft Windows Hardware
# Compatibility Publisher (attestation), so it is also on the right side of the
# April 2026 driver policy. Bumping either item means a new hash and
# thumbprint here and a new version in installer.nsi.
$items = @(
    @{
        Name      = 'vJoySetup-2.1.9.1.exe'
        Url       = 'https://github.com/jshafer817/vJoy/releases/download/v2.1.9.1/vJoySetup.exe'
        Sha256    = 'F103CED4E7FF7CCB49C8415A542C56768ED4DA4FEA252B8F4FFDAC343074654A'
        Signer    = 'On-site Dental Systems'
        Thumbprint = '6B05E5CC0DB88A0D8962482ECB9CD959E1B05790'   # CN=On-site Dental Systems (Justin Shafer)
        Purpose   = 'vJoy 2.1.9.1, jshafer817 build, Microsoft-attestation-signed driver (Inno Setup, /VERYSILENT /SUPPRESSMSGBOXES /NORESTART)'
    },
    @{
        Name      = 'ViGEmBus_1.22.0_x64_x86_arm64.exe'
        Url       = 'https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe'
        Sha256    = '89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A'
        Signer    = 'Nefarius Software Solutions'
        Thumbprint = '1F431092EC96A80B41AB5317F53AC02EA6F9B89B'   # CN=Nefarius Software Solutions e.U.
        Purpose   = 'ViGEmBus 1.22.0 (Advanced Installer bootstrapper, silent flags /exenoui /qn /norestart)'
    }
)

function Test-Redist($Item, $Path) {
    if (-not (Test-Path $Path)) { return $false }
    $hash = (Get-FileHash $Path -Algorithm SHA256).Hash
    if ($hash -ne $Item.Sha256) {
        Write-Warning "$($Item.Name): SHA-256 is $hash, expected $($Item.Sha256)"
        return $false
    }
    $sig = Get-AuthenticodeSignature $Path
    if ($sig.Status -ne 'Valid') {
        Write-Warning "$($Item.Name): Authenticode status is $($sig.Status)"
        return $false
    }
    # The SHA-256 pin above is the real gate; these two are defence in depth for
    # the day someone bumps a version and has to re-pin. Thumbprint is exact.
    # The subject check is a substring match on the whole DN, so it would also
    # accept an unrelated certificate whose DN happens to contain the name:
    # keep it as a readable second opinion, not as the thing being trusted.
    if ($sig.SignerCertificate.Thumbprint -ne $Item.Thumbprint) {
        Write-Warning "$($Item.Name): signer thumbprint is $($sig.SignerCertificate.Thumbprint), expected $($Item.Thumbprint)"
        return $false
    }
    if ($sig.SignerCertificate.Subject -notlike "*$($Item.Signer)*") {
        Write-Warning "$($Item.Name): signed by '$($sig.SignerCertificate.Subject)', expected $($Item.Signer)"
        return $false
    }
    return $true
}

New-Item -ItemType Directory -Force $redist | Out-Null
# Add TLS 1.2 rather than assigning it, so a host that already negotiates 1.3
# keeps it. Older PowerShell defaults to SSL3/TLS1.0 and needs this.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

foreach ($item in $items) {
    $path = Join-Path $redist $item.Name
    if (-not $Force -and (Test-Redist $item $path)) {
        Write-Host "OK (cached)  $($item.Name)"
        continue
    }
    if (Test-Path $path) { Remove-Item $path -Force }

    Write-Host "Downloading   $($item.Name)"
    Write-Host "              $($item.Purpose)"
    $progress = $ProgressPreference
    $ProgressPreference = 'SilentlyContinue'   # the progress bar makes this an order of magnitude slower
    try {
        Invoke-WebRequest -Uri $item.Url -OutFile $path -UseBasicParsing
    } finally {
        $ProgressPreference = $progress
    }

    if (-not (Test-Redist $item $path)) {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        throw "$($item.Name) failed verification and was deleted. Do not build an installer around an unverified driver setup."
    }
    Write-Host "OK            $($item.Name)"
}

Write-Host ''
Write-Host "Redist ready in $redist"
Get-ChildItem $redist -File | ForEach-Object { "  {0,-36} {1,10:N0} bytes" -f $_.Name, $_.Length }
Write-Host ''
Write-Host 'Next: build the app with PyInstaller, then makensis build_tools\installer.nsi'
