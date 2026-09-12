<#
.SYNOPSIS
    Emergency: give the mouse back by detaching the Nimbus Mouse Filter.
    Run from an elevated PowerShell.

.DESCRIPTION
    A class upper filter is mandatory once listed: if nimbus_moufilter cannot
    load, Windows refuses to start the mouse devices at all (problem code 52
    when the signature is rejected, 39 or 19 for other load failures). This
    script removes "nimbus_moufilter" from the mouse class UpperFilters list
    and restarts the mice, which brings them back without a reboot.

    It leaves the service and the .sys in place, so re-arming the filter later
    is install-dev.ps1 again (or just re-adding the UpperFilters entry). Use
    uninstall-dev.ps1 instead when you want the dev install gone entirely.

    This is the script you run when the mouse is already dead, so it is
    deliberately self-contained: no dot-sourcing, no repo layout assumptions,
    nothing that fails if the working tree moved. It is safe to run when
    nothing is wrong; it says so and changes nothing.

    Keyboard-only recovery, no mouse required:
        Win+X, then A          (elevated PowerShell)
        Set-ExecutionPolicy -Scope Process Bypass -Force
        & "C:\path\to\driver\recover-mouse.ps1"

    If the repo is not reachable, the same fix by hand:
        reg add "HKLM\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}" /v UpperFilters /t REG_MULTI_SZ /d mouclass /f
    then replug the mouse or reboot. Never delete the value: mouclass has to
    stay in it.
#>
[CmdletBinding()]
param(
    # Report what is wrong and what would change, without touching anything.
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Continue'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell (Win+X, then A).' }

function Show-Mice([string]$when) {
    $mice = @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)
    Write-Host "Mice present $($when): $($mice.Count)"
    foreach ($m in $mice) {
        $problem = ''
        try {
            $code = ($m | Get-PnpDeviceProperty -KeyName 'DEVPKEY_Device_ProblemCode' -ErrorAction Stop).Data
            if ($code) { $problem = " (problem code $code)" }
        } catch { }
        Write-Host "  [$($m.Status)] $($m.FriendlyName)$problem"
    }
    return $mice
}

$before = Show-Mice 'before'

$current = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
$current = if ($current) { @($current) } else { @() }
Write-Host "Mouse class UpperFilters: $(if ($current) { $current -join ', ' } else { '(none)' })"

if ($current -notcontains $service) {
    Write-Host "$service is not registered as a mouse class filter, so it is not what is holding the mouse."
    if (@($before | Where-Object { $_.Status -ne 'OK' }).Count -gt 0) {
        Write-Warning 'A mouse is still reporting a problem. This script cannot help with that; check Device Manager and the System event log.'
    }
    exit 0
}

# mouclass is the mouse class driver itself and lives in this same list.
# Writing the list without it, or deleting the value, stops every mouse.
$remaining = @($current | Where-Object { $_ -ne $service })
if ($remaining -notcontains 'mouclass') { $remaining = @('mouclass') + $remaining }

if ($WhatIfOnly) {
    Write-Host "Would set UpperFilters = $($remaining -join ', ') and restart the mice. Nothing was changed."
    exit 0
}

Write-Host "Setting mouse class UpperFilters = $($remaining -join ', ')"
Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$remaining) -Type MultiString

# Same restart as pnp-common.ps1's Restart-Mice, inlined so this script keeps
# working when the rest of the repo does not.
Write-Host 'Restarting mouse devices so the filter detaches'
foreach ($dev in @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)) {
    Write-Host "  restarting $($dev.FriendlyName)"
    pnputil /restart-device "$($dev.InstanceId)" | Out-Null
    if ($LASTEXITCODE -eq 0) { continue }
    if ($LASTEXITCODE -eq 3010) {
        Write-Warning '    Windows says this device needs a reboot to restart; the change applies after the reboot.'
        continue
    }
    Write-Warning "    pnputil /restart-device exited $LASTEXITCODE; trying Disable-PnpDevice / Enable-PnpDevice"
    try {
        $dev | Disable-PnpDevice -Confirm:$false -ErrorAction Stop
        $dev | Enable-PnpDevice -Confirm:$false -ErrorAction Stop
    } catch {
        Write-Warning "    restart failed: $($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 3

$after = Show-Mice 'after'
$bad = @($after | Where-Object { $_.Status -ne 'OK' })
if ($after.Count -gt 0 -and $bad.Count -eq 0) {
    Write-Host 'The mouse is back. The filter is detached; the driver service and .sys are untouched.'
} else {
    Write-Warning 'A mouse is still not OK. Replug it, or reboot; the UpperFilters entry is already gone, so it will start clean.'
}

Write-Host ''
Write-Host 'Why the driver would not load (most recent first):'
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-PnP'; Id=219} -MaxEvents 3 -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host "  $($_.TimeCreated)  $(($_.Message -split "`n" | Where-Object { $_ -match 'Status:' }) -join ' ')" }
Write-Host '  Status 0xC0000428 (STATUS_INVALID_IMAGE_HASH) means test signing is not active in this boot.'
Write-Host '  Check with:  driver\check-mouse-filter.ps1     Re-arm with:  driver\enable-testsigning.ps1 (reboot), then driver\install-dev.ps1'
