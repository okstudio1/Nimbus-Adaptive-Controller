<#
.SYNOPSIS
    Installs (or updates) the Nimbus Mouse Filter as a mouse-class upper filter
    for development. Run from an elevated PowerShell with test signing on.

.DESCRIPTION
    Copies driver\out\nimbus_moufilter.sys to System32\drivers, creates the
    kernel service, appends "nimbus_moufilter" to the mouse class UpperFilters
    list, and restarts every present mouse so the filter attaches. No reboot.

    The filter passes everything through until a client (Nimbus) turns
    isolation on, so installing it changes nothing on its own.

    If the driver fails to load, the mouse keeps working: a class upper filter
    that does not start does not stop mouclass. Check with:
        Get-CimInstance Win32_SystemDriver -Filter "Name='nimbus_moufilter'"
    and the System event log (Service Control Manager, event 7000).
#>
param(
    [string]$Sys = (Join-Path $PSScriptRoot 'out\nimbus_moufilter.sys'),
    [switch]$NoRestart
)

$ErrorActionPreference = 'Stop'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }
if (-not (Test-Path $Sys)) { throw "Driver not found: $Sys (run build.ps1 first)" }

$sig = Get-AuthenticodeSignature $Sys
Write-Host ("Driver signature: {0} ({1})" -f $sig.Status, $sig.SignerCertificate.Subject)
$ts = (bcdedit /enum '{current}' | Select-String 'testsigning\s+Yes')
if (-not $ts -and $sig.Status -ne 'Valid') {
    Write-Warning 'Test signing is off and the driver is not trusted-signed; it will not load. Run enable-testsigning.ps1 and reboot first.'
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

$filters = @()
$current = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
if ($current) { $filters = @($current) }
if ($filters -notcontains $service) {
    $filters += $service
    Write-Host "Setting mouse class UpperFilters = $($filters -join ', ')"
    Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$filters) -Type MultiString
} else {
    Write-Host "UpperFilters already contains $service"
}

if (-not $NoRestart) {
    Write-Host 'Restarting mouse devices so the filter attaches'
    Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  $($_.FriendlyName)"
        try { $_ | Restart-PnpDevice -Confirm:$false -ErrorAction Stop } catch { Write-Warning "    restart failed: $($_.Exception.Message)" }
    }
    Start-Sleep -Seconds 2
}

$drv = Get-CimInstance Win32_SystemDriver -Filter "Name='$service'" -ErrorAction SilentlyContinue
Write-Host ("Driver state: {0}" -f ($(if ($drv) { $drv.State } else { 'not registered' })))
Write-Host 'Check the control device with:  venv\Scripts\python -m src.mouse_isolation_win --status'
