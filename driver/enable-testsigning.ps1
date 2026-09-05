<#
.SYNOPSIS
    Prepares this machine to load the test-signed Nimbus Mouse Filter.
    Run from an elevated PowerShell. A reboot is required afterwards.

.DESCRIPTION
    1. Installs the WDK test certificate the build created (driver\out\nimbus_moufilter.cer)
       into the machine Root and TrustedPublisher stores.
    2. Suspends BitLocker for one reboot if the OS volume is protected, so the
       boot configuration change below does not trigger a recovery prompt.
    3. Turns test signing on (bcdedit /set testsigning on).

    Consequences: a "Test Mode" watermark on the desktop, and anti-cheat games
    (EasyAntiCheat, BattlEye, Vanguard) refuse to start while test signing is
    on. Undo with: bcdedit /set testsigning off, then reboot.
#>
param(
    [string]$Cert = (Join-Path $PSScriptRoot 'out\nimbus_moufilter.cer')
)

$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }
if (-not (Test-Path $Cert)) { throw "Test certificate not found: $Cert (run build.ps1 first)" }

# Native commands do not honour $ErrorActionPreference in PowerShell 5.1, so
# every one below is followed by an explicit exit-code check.
Write-Host "Installing test certificate $Cert into Root and TrustedPublisher"
certutil -addstore -f Root $Cert | Out-Null
if ($LASTEXITCODE -ne 0) { throw "certutil -addstore Root failed (exit $LASTEXITCODE); the certificate was not installed." }
certutil -addstore -f TrustedPublisher $Cert | Out-Null
if ($LASTEXITCODE -ne 0) { throw "certutil -addstore TrustedPublisher failed (exit $LASTEXITCODE); the certificate was not installed." }

# Secure Boot refuses the bcdedit change outright ("The value is protected by
# Secure Boot policy"), so say so up front instead of after a pointless reboot.
$secureBoot = $false
try { $secureBoot = [bool](Confirm-SecureBootUEFI -ErrorAction Stop) } catch { <# legacy BIOS or no UEFI: not enforced #> }
if ($secureBoot) {
    throw 'Secure Boot is on, so Windows will not accept "bcdedit /set testsigning on". Turn Secure Boot off in the firmware settings and run this script again, or use an attestation-signed build of the driver.'
}

$osDrive = $env:SystemDrive
try {
    $bl = Get-BitLockerVolume -MountPoint $osDrive -ErrorAction Stop
    if ($bl.ProtectionStatus -eq 'On') {
        Write-Host "BitLocker is on for $osDrive; suspending protection for one reboot"
        Suspend-BitLocker -MountPoint $osDrive -RebootCount 1 | Out-Null
    }
} catch {
    Write-Host "BitLocker status not available ($($_.Exception.Message)); continuing"
}

Write-Host 'Enabling test signing'
bcdedit /set testsigning on
if ($LASTEXITCODE -ne 0) {
    throw "bcdedit /set testsigning on failed (exit $LASTEXITCODE). Test signing is NOT on; do not run install-dev.ps1 until it is. If the message above mentions Secure Boot, turn Secure Boot off in the firmware settings first."
}
$stored = bcdedit /enum '{current}' | Select-String 'testsigning'
$stored
if (-not ($stored -match 'testsigning\s+Yes')) {
    throw 'bcdedit reported success but {current} does not show "testsigning Yes"; check the output above before rebooting.'
}
Write-Host 'Done. The setting takes effect at the next boot: reboot, then run driver\install-dev.ps1 from an elevated PowerShell.'
