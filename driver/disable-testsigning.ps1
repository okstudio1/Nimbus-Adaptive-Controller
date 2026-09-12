<#
.SYNOPSIS
    Turn test signing off safely: detach the Nimbus Mouse Filter first, then
    change the boot setting. Run from an elevated PowerShell.

.DESCRIPTION
    Turning test signing off is what you do to play an EasyAntiCheat,
    BattlEye or Vanguard title, or to get rid of the "Test Mode" watermark.
    Doing it with "bcdedit /set testsigning off" alone, while the filter is
    still registered as a mouse class upper filter, means the test-signed
    driver stops loading at the next boot. A class upper filter is mandatory
    once listed, so Windows then refuses to start the mouse devices and you
    reboot into a machine with no mouse (problem code 52,
    STATUS_INVALID_IMAGE_HASH). That is a real incident on this project, not
    a hypothetical: 2026-09-07.

    So the order here is: detach, then disable. It is the same two commands
    you would run by hand, in the only order that cannot strand you.

    Re-arm afterwards with enable-testsigning.ps1, reboot, then
    install-dev.ps1.

.PARAMETER Restart
    Reboot when finished. The setting only takes effect at the next boot.
#>
[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }

$filters = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
$filters = if ($filters) { @($filters) } else { @() }

if ($filters -contains $service) {
    Write-Host 'The mouse filter is registered, and it will not load once test signing is off.'
    Write-Host 'Detaching it first so this machine keeps its mouse.'
    $recover = Join-Path $PSScriptRoot 'recover-mouse.ps1'
    if (-not (Test-Path $recover)) { throw "Cannot detach the filter: $recover is missing. Run uninstall-dev.ps1, then this script again." }
    & $recover
    $after = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
    if (@($after) -contains $service) {
        throw 'The filter is still registered after the detach attempt. Not touching test signing; fix that first, or the mouse will not come back after the reboot.'
    }
    Write-Host ''
} else {
    Write-Host "$service is not registered as a mouse class filter; nothing to detach."
}

# Native commands do not honour $ErrorActionPreference in PowerShell 5.1, so
# check the exit code explicitly. Same pattern as enable-testsigning.ps1.
Write-Host 'Turning test signing off'
bcdedit /set testsigning off
if ($LASTEXITCODE -ne 0) {
    throw "bcdedit /set testsigning off failed (exit $LASTEXITCODE). Test signing is unchanged. The filter has already been detached, so the mouse is safe either way."
}

$stored = bcdedit /enum '{current}' | Select-String 'testsigning'
if ($stored) { Write-Host "Stored setting now: $($stored.ToString().Trim())" }

Write-Host ''
Write-Host 'Done. Reboot for it to take effect; the Test Mode watermark goes away and anti-cheat games start again.'
Write-Host 'To get the filter back:  driver\enable-testsigning.ps1  (reboot)  then  driver\install-dev.ps1'

if ($Restart) {
    Write-Host 'Rebooting in 10 seconds (Ctrl+C to cancel)...'
    Start-Sleep -Seconds 10
    Restart-Computer -Force
}
