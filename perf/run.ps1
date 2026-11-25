# Runs one k6 test with the run's outputs in results/<run id>/: the k6 HTML report, the printed summary and the
# deployed image. Metrics also go to Grafana Cloud when K6_PROMETHEUS_RW_SERVER_URL is set (see README).
#   .\run.ps1 workload.js average            a workload profile: smoke, average, stress, spike, soak
#   .\run.ps1 iso\tiles.js
#   .\run.ps1 iso\gpu.js -e GPU_HOTSPOTS=30    anything after the script (and profile) is passed on to `k6 run`
# No param() block on purpose: k6's own flags (-e, -q, ...) must reach k6 instead of PowerShell's common parameters.
$ErrorActionPreference = "Stop"
if (-not $args.Count) { throw "Usage: .\run.ps1 <script> [profile] [k6 run flags...]" }
$script = $args[0]
$rest = @($args | Select-Object -Skip 1)
$loadProfile = ""
if ($rest.Count -and -not "$($rest[0])".StartsWith("-")) {
    $loadProfile = $rest[0]
    $rest = @($rest | Select-Object -Skip 1)
}
Set-Location $PSScriptRoot

$name = [IO.Path]::GetFileNameWithoutExtension($script)
$runId = (Get-Date -Format "yyyyMMdd-HHmm") + "-$name" + $(if ($loadProfile) { "-$loadProfile" } else { "" })
$out = New-Item -ItemType Directory -Force (Join-Path "results" $runId)

$env:K6_WEB_DASHBOARD = "true"
$env:K6_WEB_DASHBOARD_EXPORT = Join-Path $out "report.html"
$env:K6_PROMETHEUS_RW_TREND_STATS = "p(50),p(95),p(99),max"
$env:K6_PROMETHEUS_RW_PUSH_INTERVAL = "15s"

$k6Args = @("run", "--tag", "testid=$runId")
if ($env:K6_PROMETHEUS_RW_SERVER_URL) { $k6Args += @("-o", "experimental-prometheus-rw") }
if ($loadProfile) { $k6Args += @("-e", "PROFILE=$loadProfile") }
$k6Args += $rest
$k6Args += $script

# Which backend build was measured. Needs the AWS CLI signed in and WSI_INSTANCE_ID set to backend.INSTANCE_ID of
# `terraform output github_variables` (backend/infra).
if ($env:WSI_INSTANCE_ID) {
    try {
        $command = aws ssm send-command --region eu-north-1 --instance-ids $env:WSI_INSTANCE_ID `
            --document-name AWS-RunShellScript --parameters 'commands=["cat /opt/wsi/.env"]' `
            --query Command.CommandId --output text
        Start-Sleep -Seconds 3
        aws ssm get-command-invocation --region eu-north-1 --command-id $command --instance-id $env:WSI_INSTANCE_ID `
            --query StandardOutputContent --output text | Out-File -Encoding utf8 (Join-Path $out "image.txt")
    } catch { Write-Warning "Could not read the deployed image: $_" }
}

Write-Host "Run $runId -> $out"
# Only stdout (the end-of-test summary) goes to the file; k6 logs to stderr, which stays on screen.
& k6 @k6Args | Tee-Object -FilePath (Join-Path $out "summary.txt")
exit $LASTEXITCODE
