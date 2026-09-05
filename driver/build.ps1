<#
.SYNOPSIS
    Builds the Nimbus Mouse Filter driver and collects the outputs in driver\out.

.DESCRIPTION
    Requires Visual Studio 2022 with the "Windows Driver Kit" component, the
    Spectre-mitigated libraries for the MSVC toolset the driver platform pins
    (14.38 as of WDK 10.0.26100.6584), and WDK 10.0.26100. See
    docs\vision\WINDOWS_MOUSE_FILTER_PLAN.md, section 6.

    The kit root is passed explicitly because the 64-bit registry view of
    KitsRoot10 can point at the wrong folder, and the 64-bit MSBuild is used
    because the 32-bit one cannot load InfVerif.

.PARAMETER Configuration
    Release (default) or Debug.
#>
param(
    [ValidateSet('Release', 'Debug')] [string]$Configuration = 'Release',
    [ValidateSet('x64', 'ARM64')] [string]$Platform = 'x64'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) { throw "Visual Studio installer not found ($vswhere)" }
$vs = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -property installationPath
if (-not $vs) { throw 'No Visual Studio with MSBuild found' }
$msbuild = Join-Path $vs 'MSBuild\Current\Bin\amd64\MSBuild.exe'
if (-not (Test-Path $msbuild)) { throw "MSBuild not found ($msbuild)" }
$kit = "${env:ProgramFiles(x86)}\Windows Kits\10\"
if (-not (Test-Path (Join-Path $kit 'build\10.0.26100.0\WindowsDriver.Common.targets'))) {
    throw "WDK 10.0.26100 build targets not found under $kit"
}

$proj = Join-Path $root 'nimbus_moufilter\nimbus_moufilter.vcxproj'
$kitArg = '/p:WDKContentRoot=' + $kit + '\'
Write-Host "Building $Configuration|$Platform with $msbuild"
& $msbuild $proj "/p:Configuration=$Configuration" "/p:Platform=$Platform" $kitArg /m /nologo /v:m
if ($LASTEXITCODE -ne 0) { throw "MSBuild failed with exit code $LASTEXITCODE" }

$bin = Join-Path $root "nimbus_moufilter\$Platform\$Configuration"
$out = Join-Path $root 'out'
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item (Join-Path $bin 'nimbus_moufilter.sys') $out -Force
Copy-Item (Join-Path $bin 'nimbus_moufilter.inf') $out -Force
Copy-Item (Join-Path $bin 'nimbus_moufilter.pdb') $out -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $bin 'nimbus_moufilter.cer') $out -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $bin 'nimbus_moufilter\nimbus_moufilter.cat') $out -Force -ErrorAction SilentlyContinue

Write-Host "Outputs in $out"
Get-ChildItem $out | ForEach-Object { "  {0,-24} {1,8} bytes" -f $_.Name, $_.Length }
$sig = Get-AuthenticodeSignature (Join-Path $out 'nimbus_moufilter.sys')
Write-Host ("  signature: {0} ({1})" -f $sig.Status, $sig.SignerCertificate.Subject)
