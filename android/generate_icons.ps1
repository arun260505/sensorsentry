Add-Type -AssemblyName System.Drawing
$srcPath = "C:\Users\abish\.gemini\antigravity\brain\5534e64d-a7b8-4268-91d5-aac51316ab0e\sensorsentry_logo_1788895928271.jpg"
$baseDir = "e:\hackathon\sensorsentry\android\app\src\main\res"

$sizes = @{
    "mdpi" = 48
    "hdpi" = 72
    "xhdpi" = 96
    "xxhdpi" = 144
    "xxxhdpi" = 192
}

$img = [System.Drawing.Image]::FromFile($srcPath)

foreach ($key in $sizes.Keys) {
    $size = $sizes[$key]
    $dir = "$baseDir\mipmap-$key"
    if (!(Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($img, 0, 0, $size, $size)
    $g.Dispose()
    
    $bmp.Save("$dir\ic_launcher.png", [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Save("$dir\ic_launcher_round.png", [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}
$img.Dispose()
Write-Host "Icons generated successfully!"
