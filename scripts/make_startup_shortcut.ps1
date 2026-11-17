param(
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][string]$Name,
    [string]$Desc = "EcoPolice Web"
)
$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup $Name
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($link)
$s.TargetPath = $Target
$s.WorkingDirectory = Split-Path -Parent $Target
$s.WindowStyle = 7
$s.Description = $Desc
$s.Save()
Write-Host "Created shortcut: $link"
