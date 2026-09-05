<#
.SYNOPSIS
    Removes the development install of the Nimbus Mouse Filter.
    Run from an elevated PowerShell.

.DESCRIPTION
    Removes "nimbus_moufilter" from the mouse class UpperFilters list, restarts
    the mouse devices so the filter detaches (the driver unloads when the last
    filtered mouse restarts), deletes the service, and deletes the file.
#>
param([switch]$NoRestart)

$ErrorActionPreference = 'Continue'
$service = 'nimbus_moufilter'
$classKey = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E96F-E325-11CE-BFC1-08002BE10318}'

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw 'Run this from an elevated PowerShell.' }

$current = (Get-ItemProperty -Path $classKey -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
if ($current -and ($current -contains $service)) {
    $remaining = @($current | Where-Object { $_ -ne $service })
    # mouclass itself is normally in this list; it must stay, and the value is never deleted.
    if ($remaining -notcontains 'mouclass') { $remaining = @('mouclass') + $remaining }
    Write-Host "Setting mouse class UpperFilters = $($remaining -join ', ')"
    Set-ItemProperty -Path $classKey -Name UpperFilters -Value ([string[]]$remaining) -Type MultiString
} else {
    Write-Host "UpperFilters does not contain $service"
}

if (-not $NoRestart) {
    Write-Host 'Restarting mouse devices so the filter detaches'
    Get-PnpDevice -Class Mouse -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "  $($_.FriendlyName)"
        try { $_ | Restart-PnpDevice -Confirm:$false -ErrorAction Stop } catch { Write-Warning "    restart failed: $($_.Exception.Message)" }
    }
    Start-Sleep -Seconds 2
}

sc.exe stop $service 2>$null | Out-Null
sc.exe delete $service | Out-Null
Write-Host "Service $service deleted (exit $LASTEXITCODE)"

$dest = Join-Path $env:SystemRoot 'System32\drivers\nimbus_moufilter.sys'
if (Test-Path $dest) {
    try { Remove-Item $dest -Force -ErrorAction Stop; Write-Host "Deleted $dest" }
    catch { Write-Warning "Could not delete $dest yet (still loaded?). It goes away after a reboot." }
}
Write-Host 'Done.'
