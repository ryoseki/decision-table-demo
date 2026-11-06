[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$symphonyDir = Join-Path $repoRoot '.tools/symphony'
$codexDir = Join-Path $repoRoot '.tools/codex'
New-Item -ItemType Directory -Path $symphonyDir, $codexDir -Force | Out-Null

function Get-VerifiedArtifact {
    param([string]$Url, [string]$Destination, [string]$Sha256)
    if ((Test-Path -LiteralPath $Destination) -and
        ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ieq $Sha256)) {
        Write-Host "Verified existing artifact: $(Split-Path -Leaf $Destination)"
        return
    }
    Invoke-WebRequest -Uri $Url -OutFile $Destination
    if ((Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -ine $Sha256) {
        throw "Checksum mismatch: $(Split-Path -Leaf $Destination)"
    }
    Write-Host "Downloaded and verified: $(Split-Path -Leaf $Destination)"
}

Get-VerifiedArtifact `
    -Url 'https://github.com/openai/symphony/releases/download/v0.0.3/symphony-v0.0.3-linux_x86_64' `
    -Destination (Join-Path $symphonyDir 'symphony-v0.0.3-linux_x86_64') `
    -Sha256 'ea35a04a54a6d37c0cafe3f195da871e47614a8c05765b90dbb4cac32e1435ee'

$codexArchive = Join-Path $codexDir 'codex-x86_64-unknown-linux-musl.tar.gz'
Get-VerifiedArtifact `
    -Url 'https://github.com/openai/codex/releases/download/rust-v0.166.0/codex-x86_64-unknown-linux-musl.tar.gz' `
    -Destination $codexArchive `
    -Sha256 'a0bf572d2f93f056c6712a6da348e96078dc84f3885fd7be67807ef389cb3978'
& tar -xzf $codexArchive -C $codexDir
if ($LASTEXITCODE -ne 0) { throw 'Codex archive extraction failed.' }
Write-Host 'Run: pwsh -File scripts/Start-Symphony.ps1 -Check'
