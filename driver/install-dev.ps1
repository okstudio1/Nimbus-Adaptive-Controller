<#
.SYNOPSIS
    Installs (or updates) the Nimbus Mouse Filter as a mouse-class upper filter
    for development. Run from an elevated PowerShell with test signing on.

.DESCRIPTION
    1. Creates a System Restore point (if System Restore is enabled).
    2. Copies driver\out\nimbus_moufilter.sys to System32\drivers and creates
       the kernel service.
    3. Appends "nimbus_moufilter" to the mouse class UpperFilters list.
    4. Restarts every present mouse so the filter attaches. No reboot.
    5. Verifies the driver is running and every mouse reports Status OK. If
       not, ROLLS BACK automatically (removes the UpperFilters entry and
       restarts the mice again) so you are never left without a mouse.

    A class upper filter is mandatory once listed: if the driver fails to
    load, Windows does not start the mouse devices (Code 39 / Code 19) until
    the UpperFilters entry is removed. That is what step 5 protects against.
    The keyboard is never touched, so recovery is always possible from the
    keyboard: Win+X, A (elevated PowerShell), then driver\uninstall-dev.ps1.

    The filter passes everything through until a client (Nimbus) turns
    isolation on, so a successful install changes nothing on its own.

.PARAMETER NoRollback
    Keep the filter registered even if verification fails (for debugging with
    a second pointing device or over remote access).
#>
param(
    [string]$Sys = (Join-Path $PSScriptRoot 'out\nimbus_moufilter.sys'),
    [switch]$NoRestart,
    [switch]$NoRollback
)

$ErrorActionPreference = 'Stop'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

function Get-UpperFilters {
    $v = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
    if ($v) { @($v) } else { @() }
}

function Set-UpperFilters([string[]]$filters) {
    # mouclass itself lives in this list on HID-mouse machines. Never write a
    # list without it and never delete the value; that would stop every mouse.
    if ($filters -notcontains 'mouclass') { throw "Refusing to write UpperFilters without mouclass ($($filters -join ', '))" }
    Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$filters) -Type MultiString
}

function Restart-Mice {
    Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  restarting $($_.FriendlyName)"
        try { $_ | Restart-PnpDevice -Confirm:$false -ErrorAction Stop } catch { Write-Warning "    restart failed: $($_.Exception.Message)" }
    }
    Start-Sleep -Seconds 3
}

function Get-Verification {
    $drv = Get-CimInstance Win32_SystemDriver -Filter "Name='$service'" -ErrorAction SilentlyContinue
    $mice = @(Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue)
    $bad = @($mice | Where-Object { $_.Status -ne 'OK' })
    [pscustomobject]@{
        DriverState = $(if ($drv) { $drv.State } else { 'not registered' })
        MiceTotal   = $mice.Count
        MiceBad     = $bad
        Ok          = ($drv -and $drv.State -eq 'Running' -and $bad.Count -eq 0 -and $mice.Count -gt 0)
    }
}

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }
if (-not (Test-Path $Sys)) { throw "Driver not found: $Sys (run build.ps1 first)" }

$sig = Get-AuthenticodeSignature $Sys
Write-Host ("Driver signature: {0} ({1})" -f $sig.Status, $sig.SignerCertificate.Subject)
$testSigning = [bool](bcdedit /enum '{current}' | Select-String 'testsigning\s+Yes')
if (-not $testSigning -and $sig.Status -ne 'Valid') {
    throw 'Test signing is off and the driver is not trusted-signed; it would not load and the mice would stop working. Run enable-testsigning.ps1 and reboot first.'
}
if ($sig.Status -ne 'Valid') {
    throw "The driver's signature does not verify ($($sig.Status)); the test certificate is not trusted on this machine. Run enable-testsigning.ps1."
}

$originalFilters = Get-UpperFilters
Write-Host "Mouse class UpperFilters before: $(if ($originalFilters) { $originalFilters -join ', ' } else { '(none)' })"

try {
    Checkpoint-Computer -Description 'Before Nimbus Mouse Filter dev install' -RestorePointType MODIFY_SETTINGS -ErrorAction Stop
    Write-Host 'System Restore point created'
} catch {
    Write-Warning "Could not create a restore point ($($_.Exception.Message)). Continuing; the automatic rollback below still applies."
}

$dest = Join-Path $env:SystemRoot 'System32\drivers\nimbus_moufilter.sys'
Write-Host "Copying $Sys -> $dest"
Copy-Item $Sys $dest -Force

$existing = Get-CimInstance Win32_SystemDriver -Filter "Name='$service'" -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "Creating kernel service $service"
    sc.exe create $service type= kernel start= demand error= ignore binPath= "\SystemRoot\System32\drivers\nimbus_moufilter.sys" DisplayName= "Nimbus Mouse Filter" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "sc create failed ($LASTEXITCODE)" }
} else {
    Write-Host "Service $service already exists (state: $($existing.State))"
}

if ($originalFilters -notcontains 'mouclass') {
    throw "Refusing to continue: the mouse class UpperFilters list ($($originalFilters -join ', ')) does not contain mouclass, which is not a configuration this script understands."
}
if ($originalFilters -notcontains $service) {
    # Filters attach in list order, first listed closest to the function driver.
    # Ours must sit between mouhid and mouclass to see IOCTL_INTERNAL_MOUSE_CONNECT,
    # so it goes in front of mouclass.
    $filters = @($service) + $originalFilters
    Write-Host "Setting mouse class UpperFilters = $($filters -join ', ')"
    Set-UpperFilters $filters
} else {
    Write-Host "UpperFilters already contains $service"
}

if ($NoRestart) {
    Write-Host 'Skipping device restart (-NoRestart). The filter attaches when the mice restart or on reboot. No verification performed.'
    exit 0
}

Write-Host 'Restarting mouse devices so the filter attaches'
Restart-Mice

$v = Get-Verification
Write-Host ("Driver state: {0}; mice present: {1}; mice with problems: {2}" -f $v.DriverState, $v.MiceTotal, $v.MiceBad.Count)
$v.MiceBad | ForEach-Object { Write-Warning ("  {0}: {1}" -f $_.FriendlyName, $_.Status) }

if ($v.Ok) {
    Write-Host 'Install verified. The filter is attached and passing through.'
    Write-Host 'Next:  venv\Scripts\python -m src.mouse_isolation_win --status'
    exit 0
}

if ($NoRollback) {
    Write-Warning 'Verification failed and -NoRollback was given; the filter stays registered. Run uninstall-dev.ps1 to remove it.'
    exit 2
}

Write-Warning 'Verification failed: rolling back so the mouse keeps working.'
Set-UpperFilters (Get-UpperFilters | Where-Object { $_ -ne $service })
Restart-Mice
$after = Get-Verification
Write-Host ("After rollback: driver state {0}; mice with problems: {1}" -f $after.DriverState, $after.MiceBad.Count)
Write-Host 'Why it failed: System event log, Service Control Manager events 7000/7026, and Microsoft-Windows-CodeIntegrity/Operational events 3077/3004.'
Get-WinEvent -FilterHashtable @{LogName='System'; Id=7000,7026; StartTime=(Get-Date).AddMinutes(-5)} -MaxEvents 5 -ErrorAction SilentlyContinue |
    ForEach-Object { Write-Host ("  {0} {1}" -f $_.TimeCreated, $_.Message.Split("`n")[0]) }
exit 1
