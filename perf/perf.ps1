# One entry point for the performance tests. Settings come from backend/infra/terraform.tfvars (bucket, admin
# password, Grafana Cloud) and perf/.local.env (the k6 accounts' password, generated on first use; overrides go there).
#
#   .\perf.ps1 setup            once: k6, the test slides in S3, the test accounts and hotspots (GPU worker must run)
#   .\perf.ps1 all              every test in order (~2.5 h); soak is left out, run it on its own
#   .\perf.ps1 <test>           one test: smoke tiles annotation gpu import average stress spike soak
#   .\perf.ps1 cleanup          remove what aborted runs left behind; -All removes all test data (end of campaign)
#
# Each run's report and summary land in results/<run>/; with Grafana configured, metrics go to the dashboard too.
param([string]$Command = "help", [switch]$All)

# Native tools (k6, aws) write progress to stderr; in Windows PowerShell 5.1 "Stop" would turn that into errors.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
Set-Location $PSScriptRoot

$K6Version = "2.4.0"
$Infra = Join-Path $PSScriptRoot "..\infra"
$LocalEnv = Join-Path $PSScriptRoot ".local.env"
$SlideCache = Join-Path $PSScriptRoot ".cache\slides"
$TestData = "https://openslide.cs.cmu.edu/download/openslide-testdata"
# Bucket key -> download. A key ending in "/" is an extracted multi-file slide (the .mrxs file and its folder).
$Slides = [ordered]@{
    "perf/CMU-1.svs"     = "$TestData/Aperio/CMU-1.svs"
    "perf/CMU-2.svs"     = "$TestData/Aperio/CMU-2.svs"
    "perf/Mirax2.2-1/"   = "$TestData/Mirax/Mirax2.2-1.zip"
}
$ImportSource = "perf/CMU-2.svs"
$BaseUrl = "https://d3s9lf5kpej9we.cloudfront.net"
$Tests = [ordered]@{
    smoke      = @("workload.js", "smoke")
    tiles      = @("iso/tiles.js", "")
    annotation = @("iso/annotation.js", "")
    gpu        = @("iso/gpu.js", "")
    import     = @("iso/import.js", "")
    average    = @("workload.js", "average")
    stress     = @("workload.js", "stress")
    spike      = @("workload.js", "spike")
    soak       = @("workload.js", "soak")
}
$AllOrder = @("smoke", "tiles", "annotation", "gpu", "import", "average", "stress", "spike")

function Fail($message) { Write-Host $message -ForegroundColor Red; exit 1 }

# A `name = "value"` line of terraform.tfvars (the grafana_cloud block included), or "".
function Read-Tfvar($name) {
    $file = Join-Path $Infra "terraform.tfvars"
    if (-not (Test-Path $file)) { return "" }
    $match = Select-String -Path $file -Pattern "^\s*$name\s*=\s*""([^""]*)""" | Select-Object -First 1
    if ($match) { return $match.Matches[0].Groups[1].Value } else { return "" }
}

function Read-LocalEnv {
    $values = @{}
    if (Test-Path $LocalEnv) {
        foreach ($line in Get-Content $LocalEnv) {
            if ($line -match "^\s*([A-Z0-9_]+)\s*=\s*(.*)$") { $values[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    if (-not $values.ContainsKey("K6_USER_PASSWORD")) {
        $values["K6_USER_PASSWORD"] = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
        Add-Content -Path $LocalEnv -Value "K6_USER_PASSWORD=$($values["K6_USER_PASSWORD"])" -Encoding ascii
        Write-Host "Generated the k6 accounts' password in perf\.local.env"
    }
    return $values
}

# Everything k6 and the AWS CLI need, as environment variables of this process.
function Set-Settings {
    $local = Read-LocalEnv
    $admin = Read-Tfvar "admin_username"
    $env:K6_USER_PASSWORD = $local["K6_USER_PASSWORD"]
    $env:K6_ADMIN_USER = $(if ($local["K6_ADMIN_USER"]) { $local["K6_ADMIN_USER"] } elseif ($admin) { $admin } else { "admin" })
    $env:K6_ADMIN_PASSWORD = $(if ($local["K6_ADMIN_PASSWORD"]) { $local["K6_ADMIN_PASSWORD"] } else { Read-Tfvar "admin_password" })
    $script:Bucket = "s3://" + $(if ($local["BUCKET"]) { $local["BUCKET"] } else { Read-Tfvar "bucket_name" })
    if ($script:Bucket -eq "s3://") { Fail "No bucket_name in infra\terraform.tfvars (or BUCKET in perf\.local.env)." }
    $prometheus = Read-Tfvar "prometheus_url"
    if ($prometheus) {
        $env:K6_PROMETHEUS_RW_SERVER_URL = $prometheus
        $env:K6_PROMETHEUS_RW_USERNAME = Read-Tfvar "prometheus_user"
        $env:K6_PROMETHEUS_RW_PASSWORD = Read-Tfvar "token"
    }
    $env:K6_PROMETHEUS_RW_TREND_STATS = "p(50),p(95),p(99),max"
    $env:K6_PROMETHEUS_RW_PUSH_INTERVAL = "15s"
    $env:AWS_REGION = "eu-north-1"
}

function Assert-K6 {
    $k6 = Get-Command k6 -ErrorAction SilentlyContinue
    if ($k6 -and ((& k6 version) -match "v$K6Version")) { return }
    Write-Host "Installing k6 v$K6Version with winget..."
    winget install GrafanaLabs.k6 --version $K6Version --silent --accept-package-agreements --accept-source-agreements | Out-Host
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command k6 -ErrorAction SilentlyContinue)) { Fail "k6 is still not on PATH: open a new PowerShell window and try again." }
}

function Assert-Aws {
    if (-not (Get-Command aws -ErrorAction SilentlyContinue)) { Fail "The AWS CLI is missing (winget install Amazon.AWSCLI), then sign in." }
    aws sts get-caller-identity --query Account --output text *> $null
    if ($LASTEXITCODE -ne 0) { Fail "The AWS CLI is not signed in (aws configure / aws sso login)." }
}

# Puts each test slide into the bucket unless it is already there.
function Publish-Slides {
    New-Item -ItemType Directory -Force $SlideCache | Out-Null
    foreach ($key in $Slides.Keys) {
        aws s3 ls "$Bucket/$key" *> $null
        if ($LASTEXITCODE -eq 0) { Write-Host "In the bucket: $key"; continue }
        $url = $Slides[$key]
        $file = Join-Path $SlideCache ([IO.Path]::GetFileName($url))
        if (-not (Test-Path $file)) {
            Write-Host "Downloading $url"
            curl.exe -fL --retry 3 -o $file $url
            if ($LASTEXITCODE -ne 0) { Remove-Item $file -ErrorAction SilentlyContinue; Fail "Download failed: $url" }
        }
        Write-Host "Uploading $key"
        if ($key.EndsWith("/")) {
            $folder = Join-Path $SlideCache ([IO.Path]::GetFileNameWithoutExtension($file))
            if (-not (Test-Path $folder)) { Expand-Archive $file -DestinationPath $folder }
            # The zip may hold one top-level folder; upload the folder that has the .mrxs file in it.
            $mrxs = Get-ChildItem $folder -Recurse -Filter *.mrxs | Select-Object -First 1
            if (-not $mrxs) { Fail "No .mrxs file in $file" }
            aws s3 cp $mrxs.Directory.FullName "$Bucket/$key" --recursive --only-show-errors
        } else {
            aws s3 cp $file "$Bucket/$key" --only-show-errors
        }
        if ($LASTEXITCODE -ne 0) { Fail "Upload of $key failed." }
    }
}

# The backend image the runs measured (best effort: needs Terraform state and SSM access).
function Get-DeployedImage {
    try {
        $variables = terraform "-chdir=$Infra" output -json github_variables 2>$null | ConvertFrom-Json
        $instance = $variables.backend.INSTANCE_ID
        $id = aws ssm send-command --instance-ids $instance --document-name AWS-RunShellScript `
            --parameters 'commands=["cat /opt/wsi/.env"]' --query Command.CommandId --output text 2>$null
        Start-Sleep -Seconds 4
        return (aws ssm get-command-invocation --command-id $id --instance-id $instance `
            --query StandardOutputContent --output text 2>$null)
    } catch { return "" }
}

# Runs one k6 script; returns its exit code (0 passed, 99 thresholds crossed, anything else broke).
function Invoke-K6($file, $loadProfile, $extra = @(), $label = "") {
    $name = $(if ($label) { $label } else { [IO.Path]::GetFileNameWithoutExtension($file) })
    $runId = (Get-Date -Format "yyyyMMdd-HHmm") + "-$name"
    $out = New-Item -ItemType Directory -Force (Join-Path "results" $runId)
    if ($script:Image) { $script:Image | Out-File -Encoding utf8 (Join-Path $out "image.txt") }
    $env:K6_WEB_DASHBOARD = "true"
    $env:K6_WEB_DASHBOARD_EXPORT = Join-Path $out "report.html"
    $arguments = @("run", "--tag", "testid=$runId")
    if ($env:K6_PROMETHEUS_RW_SERVER_URL) { $arguments += @("-o", "experimental-prometheus-rw") }
    if ($loadProfile) { $arguments += @("-e", "PROFILE=$loadProfile") }
    $arguments += $extra
    $arguments += $file
    Write-Host "`n=== $runId -> $out" -ForegroundColor Cyan
    # Out-Host: k6's output must not become part of this function's return value.
    & k6 @arguments | Tee-Object -FilePath (Join-Path $out "summary.txt") | Out-Host
    return $LASTEXITCODE
}

# After an overload the API keeps working through requests its clients gave up on, and stays unusable for minutes:
# the next test waits until a login answers quickly again, so it measures the app and not the previous test's backlog.
function Wait-Api {
    $body = @{ username = "k6-manager"; password = $env:K6_USER_PASSWORD } | ConvertTo-Json
    $started = Get-Date
    for (;;) {
        $clock = [Diagnostics.Stopwatch]::StartNew()
        try {
            Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/auth/login" -ContentType "application/json" -Body $body -TimeoutSec 10 | Out-Null
            if ($clock.ElapsedMilliseconds -lt 2000) { break }
        } catch {}
        $waited = [int]((Get-Date) - $started).TotalSeconds
        if ($waited -gt 1200) { return $false }
        Write-Host "Waiting for the API to recover ($waited s)..." -ForegroundColor Yellow
        Start-Sleep -Seconds 15
    }
    $waited = [int]((Get-Date) - $started).TotalSeconds
    if ($waited -gt 0) { Write-Host "API answering again after $waited s." }
    return $true
}

function Invoke-Test($test) {
    $file, $loadProfile = $Tests[$test]
    $extra = @()
    if ($test -eq "import") {
        # Deleting the imported slide deletes its file too, so the test imports a fresh copy each time.
        $key = "perf/import/k6-import-$(Get-Date -Format yyyyMMddHHmmss)-$([IO.Path]::GetFileName($ImportSource))"
        aws s3 cp "$Bucket/$ImportSource" "$Bucket/$key" --only-show-errors | Out-Host
        if ($LASTEXITCODE -ne 0) { Write-Host "Could not copy $ImportSource for the import test." -ForegroundColor Red; return 1 }
        $extra = @("-e", "IMPORT_KEY=$key")
    }
    return Invoke-K6 $file $loadProfile $extra $test
}

function Write-Outcome($test, $code) {
    $text = switch ($code) { 0 { "passed" } 99 { "thresholds crossed" } default { "failed (k6 exit $code)" } }
    $color = switch ($code) { 0 { "Green" } 99 { "Yellow" } default { "Red" } }
    Write-Host ("{0,-11} {1}" -f $test, $text) -ForegroundColor $color
    return ("{0,-11} {1}" -f $test, $text)
}

switch ($Command) {
    "setup" {
        Set-Settings
        Assert-K6
        Assert-Aws
        Publish-Slides
        Write-Host "`nCreating the test accounts and hotspots. The GPU worker has to be running for the hotspots."
        & k6 run -e "SLIDE_KEYS=$($Slides.Keys -join ',')" setup/seed.js
        if ($LASTEXITCODE -ne 0) { Fail "Seeding failed (see above). It is safe to run '.\perf.ps1 setup' again." }
        Write-Host "`nReady. Next: .\perf.ps1 all" -ForegroundColor Green
    }
    "all" {
        Set-Settings
        Assert-K6
        Assert-Aws
        Write-Host "This loads the app the whole team uses for about 2.5 hours (stress and spike can make it unusable)." -ForegroundColor Yellow
        Write-Host "The GPU worker must be running. Starting in 15 s; Ctrl+C to cancel." -ForegroundColor Yellow
        Start-Sleep -Seconds 15
        $script:Image = Get-DeployedImage
        $lines = @()
        foreach ($test in $AllOrder) {
            if (-not (Wait-Api)) { Write-Host "Stopping: the API has not recovered for 20 minutes." -ForegroundColor Red; break }
            $code = Invoke-Test $test
            $lines += Write-Outcome $test $code
            # Crossed thresholds are a result (the ramps are meant to find the limit); anything else means the
            # script or the app broke, and later tests would only repeat it.
            if ($code -ne 0 -and $code -ne 99) { Write-Host "Stopping: fix the failure above first." -ForegroundColor Red; break }
        }
        & k6 run -q setup/cleanup.js | Out-Null
        Write-Host "`n=== Summary" -ForegroundColor Cyan
        $lines | ForEach-Object { Write-Host $_ }
        $lines | Out-File -Encoding utf8 (Join-Path "results" "$(Get-Date -Format yyyyMMdd-HHmm)-all.txt")
    }
    "cleanup" {
        Set-Settings
        Assert-K6
        $extra = $(if ($All) { @("-e", "ALL=1") } else { @() })
        & k6 run @extra setup/cleanup.js
    }
    default {
        if (-not $Tests.Contains($Command)) {
            Get-Content $PSCommandPath | Select-Object -Skip 3 -First 4 | ForEach-Object { $_ -replace "^# ?", "" }
            exit $(if ($Command -eq "help") { 0 } else { 1 })
        }
        Set-Settings
        Assert-K6
        if ($Command -eq "import") { Assert-Aws }
        $code = Invoke-Test $Command
        Write-Outcome $Command $code | Out-Null
        exit $code
    }
}
