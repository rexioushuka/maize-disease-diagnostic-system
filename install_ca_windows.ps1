[CmdletBinding()]
param(
    [switch]$Machine
)

$ErrorActionPreference = "Stop"
$caFile = Join-Path $PSScriptRoot "instance\certs\maize-ca-cert.crt"

if (-not (Test-Path -LiteralPath $caFile)) {
    throw "CA certificate not found at $caFile. Start the system once to generate it."
}

$store = if ($Machine) { "Cert:\LocalMachine\Root" } else { "Cert:\CurrentUser\Root" }
$certificate = Import-Certificate -FilePath $caFile -CertStoreLocation $store

Write-Host "Installed the Maize Disease System local CA."
Write-Host "Store:      $store"
Write-Host "Subject:    $($certificate.Subject)"
Write-Host "Thumbprint: $($certificate.Thumbprint)"
Write-Host "Expires:    $($certificate.NotAfter)"
Write-Host "Restart the browser after installation."
