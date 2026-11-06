[CmdletBinding()]
param(
    [switch]$Check,
    [string]$ProjectUrl,
    [string]$Distribution = 'Ubuntu',
    [ValidateRange(1024, 65535)][int]$Port = 4090,
    [string]$CodexAuthFile
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$shellScript = Join-Path $PSScriptRoot 'symphony.sh'
if (-not $CodexAuthFile) {
    $authRoot = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
    $CodexAuthFile = Join-Path $authRoot 'auth.json'
}
if (-not (Test-Path -LiteralPath $CodexAuthFile -PathType Leaf)) {
    throw 'Codexの認証ファイルがありません。Codexにログインしてから実行してください。'
}
if (-not $Check) {
    if (-not $ProjectUrl) { throw '-ProjectUrl にLinearプロジェクトのURLまたはslugIdを指定してください。' }
    if ($ProjectUrl -match '^https://linear\.app/[^/]+/project/(?:[^/?#]+-)?([a-f0-9]{12})(?:[/?#].*)?$') {
        $projectSlug = $Matches[1]
    } elseif ($ProjectUrl -match '^[a-zA-Z0-9][a-zA-Z0-9-]{0,159}$') {
        $projectSlug = $ProjectUrl
    } else {
        throw 'LinearのプロジェクトURLをコピーするか、project slugIdを指定してください。'
    }
}

$variableNames = @('WSLENV', 'LINEAR_API_KEY', 'SYMPHONY_LINEAR_PROJECT_SLUG', 'SYMPHONY_DASHBOARD_PORT', 'SYMPHONY_CODEX_AUTH_FILE')
$savedEnvironment = @{}
foreach ($name in $variableNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:SYMPHONY_CODEX_AUTH_FILE = (Resolve-Path -LiteralPath $CodexAuthFile).Path
    $env:SYMPHONY_DASHBOARD_PORT = [string]$Port
    $env:SYMPHONY_LINEAR_PROJECT_SLUG = if ($Check) { '' } else { $projectSlug }
    $passThrough = @('LINEAR_API_KEY', 'SYMPHONY_LINEAR_PROJECT_SLUG', 'SYMPHONY_DASHBOARD_PORT', 'SYMPHONY_CODEX_AUTH_FILE/p')
    $env:WSLENV = ((@($env:WSLENV -split ':' | Where-Object { $_ }) + $passThrough) | Select-Object -Unique) -join ':'
    if (-not $Check -and -not $env:LINEAR_API_KEY) {
        $secureKey = Read-Host 'Linear Personal API key（入力内容は表示されません）' -AsSecureString
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try { $env:LINEAR_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer) }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer); $secureKey.Dispose() }
    }
    $windowsScript = $shellScript.Replace('\', '/')
    $linuxScript = (& wsl.exe -d $Distribution --exec wslpath -a $windowsScript | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $linuxScript) { throw 'WSLのスクリプトパスを解決できません。' }
    $mode = if ($Check) { 'check' } else { 'start' }
    & wsl.exe -d $Distribution --exec bash $linuxScript $mode
    if ($LASTEXITCODE -ne 0) { throw "Symphonyの$mode が終了コード$LASTEXITCODE で失敗しました。" }
} finally {
    foreach ($name in $variableNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
