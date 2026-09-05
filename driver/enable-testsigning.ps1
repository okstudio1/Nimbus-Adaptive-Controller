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

Write-Host "Installing test certificate $Cert into Root and TrustedPublisher"
certutil -addstore -f Root $Cert | Out-Null
certutil -addstore -f TrustedPublisher $Cert | Out-Null

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
bcdedit /set testsigning on | Out-Null
bcdedit /enum '{current}' | Select-String 'testsigning'
Write-Host 'Done. Reboot, then run driver\install-dev.ps1 from an elevated PowerShell.'
