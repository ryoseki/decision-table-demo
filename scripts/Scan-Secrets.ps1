[CmdletBinding()]
param(
    [ValidateSet('Worktree', 'Staged', 'History')]
    [string]$Scope = 'Worktree'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$version = '1.10.0'
$installDir = Join-Path $repoRoot ".tools/betterleaks/$version"
$binary = Join-Path $installDir 'betterleaks.exe'

if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) {
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    $archiveName = "betterleaks_${version}_windows_x64.zip"
    $archive = Join-Path $installDir $archiveName
    $expectedHash = 'bafd52abcb440c2a06ea36d5e1fff217315db0aac517e60d74e64b2bb9f18468'
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf) -or
        (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $expectedHash) {
        Invoke-WebRequest -Uri "https://github.com/betterleaks/betterleaks/releases/download/v$version/$archiveName" -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $expectedHash) {
        throw 'Betterleaksの配布ファイルが公式のSHA256と一致しません。'
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $installDir -Force
}

# The Go engine runs without the RE2 WebAssembly cache in the Windows sandbox.
$scanOptions = @('--regex-engine=stdlib', '--redact=100', '--validation=false', '--no-banner', '--no-color', '--exit-code=10')
if ($Scope -eq 'Staged') {
    & $binary git --staged @scanOptions -- $repoRoot
    exit $LASTEXITCODE
}
if ($Scope -eq 'History') {
    & $binary git @scanOptions -- $repoRoot
    exit $LASTEXITCODE
}

$fileList = & git --no-optional-locks -C $repoRoot ls-files --cached --others --exclude-standard -z
if ($LASTEXITCODE -ne 0) {
    throw 'Gitから検査対象の一覧を取得できませんでした。'
}
$files = @(($fileList -join "`n").Split([char]0, [StringSplitOptions]::RemoveEmptyEntries) |
    Select-Object -Unique |
    Where-Object { $_ -notmatch '^(?:\.git|\.tools|\.symphony)(?:/|$)' } |
    ForEach-Object {
        $item = Get-Item -LiteralPath (Join-Path $repoRoot $_) -Force -ErrorAction SilentlyContinue
        if ($item -is [IO.FileInfo] -and -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $item.FullName
        }
    })

if ($files.Count -eq 0) {
    Write-Host '検査対象のファイルはありません。'
    exit 0
}
Write-Host "Betterleaksで作業ツリーの$($files.Count)ファイルを検査します。"
& $binary dir @scanOptions -- @files
exit $LASTEXITCODE
