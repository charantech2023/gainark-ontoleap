# Run the API locally against the shared brain, under a dev prefix.
#
# The archive (graph runs, registry events, vocabulary, the synonym review queue) and the
# verticals mirror live in gs://gainark-ontoleap-graph. Production uses the ontoleap/ and
# verticals/ prefixes there; this points a local run at dev/ instead, so experiments
# accumulate into a shared dev brain without touching what production learns from.
#
# The variables are set only while the API runs and are put back when it exits, Ctrl+C
# included: $env: belongs to the whole terminal, so a test run typed into the same window
# afterwards would otherwise write into the shared dev brain. Not in .env for the same
# reason - something the API imports loads the nearest .env.
#
#   .\scripts\dev_shared_brain.ps1                 # dev/ prefix
#   .\scripts\dev_shared_brain.ps1 -Prefix dev-sree
#
# Needs Application Default Credentials once per machine:
#   gcloud auth application-default login

param(
    [string]$Prefix = "dev",
    [string]$Bucket = "gainark-ontoleap-graph"
)

if ($Prefix -in @("ontoleap", "verticals", "")) {
    Write-Error "Prefix '$Prefix' is production's. Pick a dev prefix."
    exit 1
}

gcloud auth application-default print-access-token *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Error "No Application Default Credentials. Run: gcloud auth application-default login"
    exit 1
}

$saved = @{
    ONTOLEAP_GRAPH_ARCHIVE    = $env:ONTOLEAP_GRAPH_ARCHIVE
    ONTOLEAP_VERTICALS_MIRROR = $env:ONTOLEAP_VERTICALS_MIRROR
}
try {
    $env:ONTOLEAP_GRAPH_ARCHIVE = "gs://$Bucket/$Prefix/ontoleap"
    $env:ONTOLEAP_VERTICALS_MIRROR = "gs://$Bucket/$Prefix/verticals"
    Write-Host "Graph archive:   $env:ONTOLEAP_GRAPH_ARCHIVE"
    Write-Host "Verticals mirror: $env:ONTOLEAP_VERTICALS_MIRROR"

    $python = Join-Path $PSScriptRoot "..\venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    & $python (Join-Path $PSScriptRoot "..\api.py")
}
finally {
    # Runs on Ctrl+C too. Puts back what the terminal had, which is normally nothing.
    foreach ($name in $saved.Keys) {
        if ($null -eq $saved[$name]) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
        else { Set-Item "Env:$name" $saved[$name] }
    }
    Write-Host "Shared-brain variables cleared from this terminal."
}
