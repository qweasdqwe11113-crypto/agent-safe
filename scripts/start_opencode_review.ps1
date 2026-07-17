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

python server.py --host 127.0.0.1 --port 8000
