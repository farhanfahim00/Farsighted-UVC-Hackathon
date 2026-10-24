# Run in your own PowerShell. Input stays out of command history and chat.
$ErrorActionPreference = 'Stop'
$pilotRoot = Split-Path -Parent $PSScriptRoot
$pilotEnvPath = Join-Path $pilotRoot '.env'
$pilotGit = & git -C $pilotRoot check-ignore -- .env
if ($LASTEXITCODE -ne 0) { throw '.env must be ignored by Git before saving the key.' }
$pilotTracked = & git -C $pilotRoot ls-files -- .env
if ($pilotTracked) { throw '.env is tracked by Git. Stop and remove it from tracking before saving a key.' }
$pilotSecure = Read-Host 'Paste Claude API key (input is hidden)' -AsSecureString
$pilotPointer = [IntPtr]::Zero
try {
    $pilotPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pilotSecure)
    $pilotKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pilotPointer).Trim()
    if (-not $pilotKey -or $pilotKey -match '[\r\n]') { throw 'Enter one nonempty API key.' }
    $pilotExisting = if (Test-Path -LiteralPath $pilotEnvPath) { [IO.File]::ReadAllText($pilotEnvPath) } else { '' }
    $pilotOtherLines = @($pilotExisting -split '\r?\n' | Where-Object { $_ -notmatch '^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=' })
    $pilotText = (($pilotOtherLines -join "`n").TrimEnd() + "`nANTHROPIC_API_KEY=" + $pilotKey + "`n").TrimStart("`r", "`n")
    [IO.File]::WriteAllText($pilotEnvPath, $pilotText, [Text.UTF8Encoding]::new($false))
    Write-Output 'Saved locally in .env (ignored by Git). The key was not printed.'
} finally {
    if ($pilotPointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pilotPointer) }
    $pilotKey = $null
    $pilotText = $null
    $pilotExisting = $null
    $pilotSecure.Dispose()
}
