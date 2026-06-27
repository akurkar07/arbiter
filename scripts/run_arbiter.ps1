param(
    [int]$Port = 8000,
    [switch]$NoInstall,
    [switch]$Dashboard
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $RepoRoot

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[arbiter] $Message" -ForegroundColor Cyan
}

function Test-PythonModule {
    param([string]$Module)
    & $VenvPython -c "import $Module" *> $null
    return ($LASTEXITCODE -eq 0)
}

if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating virtual environment at .venv"
    python -m venv .venv
}

if (-not (Test-Path $VenvPython)) {
    throw "Could not find .venv Python at $VenvPython"
}

$NeedsInstall = -not (Test-PythonModule "uvicorn") -or -not (Test-PythonModule "fastapi")

if ($NeedsInstall) {
    if ($NoInstall) {
        throw "Missing dashboard dependencies. Re-run without -NoInstall to install them."
    }

    Write-Step "Installing Arbiter dashboard dependencies"
    & $VenvPython -m pip install -e ".[dev,web,stripe,llm]"
}

$StripeMode = if ($env:STRIPE_SECRET_KEY) { "real Stripe test-mode" } else { "mock Stripe" }
$NvidiaMode = if ($env:NVIDIA_API_KEY -or $env:NVIDIA_NIM_KEY) { "real NVIDIA NIM" } else { "mock Nemotron" }
$Path = if ($Dashboard) { "/dashboard.html" } else { "/" }
$Url = "http://127.0.0.1:$Port$Path"

Write-Step "Starting Arbiter on $Url"
Write-Step "Rails: $StripeMode; $NvidiaMode"
if ($Dashboard) {
    Write-Step "Opening the dashboard directly. Press Go live to run /run_operator"
} else {
    Write-Step "Opening the landing page. Use -Dashboard to skip straight to the app"
}

Start-Process $Url

& $VenvPython -m uvicorn arbiter.web.server:app --port $Port
