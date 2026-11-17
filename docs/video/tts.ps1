Add-Type -AssemblyName System.Speech
$root = $PSScriptRoot
$jsonPath = Join-Path $root "slides\narration.json"
$text = [IO.File]::ReadAllText($jsonPath, [Text.Encoding]::UTF8)
$items = $text | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.SelectVoice("Microsoft Huihui Desktop")
$synth.Rate = 1
for ($i = 0; $i -lt $items.Count; $i++) {
    $n = "{0:D2}" -f ($i + 1)
    $out = Join-Path $root ("audio\slide{0}.wav" -f $n)
    $synth.SetOutputToWaveFile($out)
    $synth.Speak($items[$i])
}
$synth.Dispose()
Write-Output ("generated {0} wav files" -f $items.Count)