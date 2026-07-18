$ErrorActionPreference = "Stop"

if (-not $env:APG_UPSTREAM_BASE_URL) {
    $env:APG_UPSTREAM_BASE_URL = "https://www.right.codes/codex/v1"
}

$env:APG_GATEWAY_REVIEW_MODE = "review-first"
if (-not $env:APG_GATEWAY_PROFILE) {
    $env:APG_GATEWAY_PROFILE = "coding"
}
if (-not $env:APG_NER_BACKEND) {
    $env:APG_NER_BACKEND = "heuristic"
}

$reviewUrl = "http://127.0.0.1:8000/debug"
$serverProcess = Start-Process `
    -FilePath "python" `
    -ArgumentList @("server.py", "--host", "127.0.0.1", "--port", "8000") `
    -NoNewWindow `
    -PassThru

try {
    $deadline = (Get-Date).AddSeconds(15)
    do {
        try {
            $health = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
            if ($health.StatusCode -eq 200) {
                break
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while ((Get-Date) -lt $deadline -and -not $serverProcess.HasExited)

    if ($serverProcess.HasExited) {
        throw "Agent Privacy Guard stopped before the review page became available."
    }
    if ((Get-Date) -ge $deadline) {
        throw "Timed out waiting for Agent Privacy Guard to start."
    }

    Start-Process $reviewUrl
    Write-Host "Review page opened: $reviewUrl"
    Write-Host "Keep this window open while using OpenCode. Press Ctrl+C to stop the proxy."
    Wait-Process -Id $serverProcess.Id
} finally {
    if (-not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
}
