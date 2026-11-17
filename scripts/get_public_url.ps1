$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root "logs\cloudflared.log"
$out = Join-Path $root "public_url.txt"
for ($i = 0; $i -lt 60; $i++) {
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
        if ($m) {
            $url = $m.Matches[0].Value
            Set-Content -Path $out -Value $url -Encoding Ascii
            Write-Host ""
            Write-Host "=============================================" -ForegroundColor Cyan
            Write-Host "  公网地址: $url" -ForegroundColor Green
            Write-Host "=============================================" -ForegroundColor Cyan
            Start-Process $url
            exit 0
        }
    }
    Start-Sleep -Seconds 1
}
Write-Host "未获取到公网地址，请查看 logs\cloudflared.log" -ForegroundColor Yellow
exit 1
