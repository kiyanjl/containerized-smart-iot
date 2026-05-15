param(
    [int[]]$GrafanaPortCandidates = @(3100, 3200, 3300, 3400, 3500, 3600, 3700, 4400, 4500, 4600)
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root ".env"

function Get-EnvValue {
    param([string]$Name)
    if (-not (Test-Path $EnvPath)) {
        return $null
    }

    $line = Get-Content $EnvPath | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -First 1
    if (-not $line) {
        return $null
    }

    return ($line -split "=", 2)[1].Trim()
}

function Set-EnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path $EnvPath) {
        $lines = @(Get-Content $EnvPath)
    }

    $updated = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match "^$([regex]::Escape($Name))=") {
            $lines[$i] = "$Name=$Value"
            $updated = $true
            break
        }
    }

    if (-not $updated) {
        $lines += "$Name=$Value"
    }

    Set-Content -Path $EnvPath -Value $lines
}

function Get-ExcludedTcpRanges {
    $ranges = @()
    $output = netsh interface ipv4 show excludedportrange protocol=tcp
    foreach ($line in $output) {
        if ($line -match "^\s*(\d+)\s+(\d+)") {
            $ranges += [pscustomobject]@{
                Start = [int]$matches[1]
                End = [int]$matches[2]
            }
        }
    }
    return $ranges
}

function Test-PortUsable {
    param(
        [int]$Port,
        [object[]]$ExcludedRanges
    )

    foreach ($range in $ExcludedRanges) {
        if ($Port -ge $range.Start -and $Port -le $range.End) {
            return $false
        }
    }

    $connection = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) {
        return $false
    }

    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $listener.Start()
        $listener.Stop()
        return $true
    }
    catch {
        return $false
    }
}

$excludedRanges = @(Get-ExcludedTcpRanges)
$configuredGrafanaPort = Get-EnvValue "GRAFANA_PORT"
$candidatePorts = @()

if ($configuredGrafanaPort -match "^\d+$") {
    $candidatePorts += [int]$configuredGrafanaPort
}
$candidatePorts += $GrafanaPortCandidates
$candidatePorts = $candidatePorts | Select-Object -Unique

$selectedGrafanaPort = $null
foreach ($port in $candidatePorts) {
    if (Test-PortUsable -Port $port -ExcludedRanges $excludedRanges) {
        $selectedGrafanaPort = $port
        break
    }
}

if (-not $selectedGrafanaPort) {
    throw "No usable Grafana port found. Tried: $($candidatePorts -join ', ')"
}

# Also handle Dashboard port
$configuredDashboardPort = Get-EnvValue "DASHBOARD_PORT"
if (-not $configuredDashboardPort) {
    $configuredDashboardPort = 18501
}

if (-not (Test-PortUsable -Port [int]$configuredDashboardPort -ExcludedRanges $excludedRanges)) {
    Write-Host "Configured Dashboard port $configuredDashboardPort is not usable. Finding a new one..."
    $dashboardCandidates = @(18501, 18502, 18503, 18504, 18505)
    foreach ($p in $dashboardCandidates) {
        if (Test-PortUsable -Port $p -ExcludedRanges $excludedRanges) {
            $configuredDashboardPort = $p
            break
        }
    }
}

Set-EnvValue "GRAFANA_PORT" "$selectedGrafanaPort"
Set-EnvValue "GRAFANA_PUBLIC_URL" "http://localhost:$selectedGrafanaPort"
Set-EnvValue "DASHBOARD_PORT" "$configuredDashboardPort"

Write-Host "Using Grafana host port: $selectedGrafanaPort"
Write-Host "Using Dashboard host port: $configuredDashboardPort"
Write-Host "Starting Docker Compose stack..."

Push-Location $Root
try {
    docker compose up -d
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Stack startup requested."
Write-Host "Dashboard: http://localhost:$configuredDashboardPort"
Write-Host "Grafana:   http://localhost:$selectedGrafanaPort"
Write-Host "InfluxDB:  http://localhost:8086"
