# Gera installer\Output\ScreenCam_Setup.exe (no GitHub Actions ou em um PC Windows com internet).
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot
$pl   = Join-Path $root 'installer\payload'
$tmp  = Join-Path $env:TEMP 'screencam_build'
New-Item -ItemType Directory -Force (Join-Path $pl 'bin'), $tmp | Out-Null

function Get-File($url, $out) { Write-Host "Baixando $url"; Invoke-WebRequest $url -OutFile $out -UseBasicParsing }

# Python embutido (o PC do cliente nao precisa ter Python)
if (-not (Test-Path (Join-Path $pl 'python\pythonw.exe'))) {
    $z = Join-Path $tmp 'py.zip'
    Get-File 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip' $z
    Expand-Archive $z (Join-Path $pl 'python') -Force
}
# MediaMTX
if (-not (Test-Path (Join-Path $pl 'bin\sc_mediamtx.exe'))) {
    $z = Join-Path $tmp 'mtx.zip'
    Get-File 'https://github.com/bluenviron/mediamtx/releases/download/v1.9.3/mediamtx_v1.9.3_windows_amd64.zip' $z
    Expand-Archive $z (Join-Path $tmp 'mtx') -Force
    Copy-Item (Join-Path $tmp 'mtx\mediamtx.exe') (Join-Path $pl 'bin\sc_mediamtx.exe') -Force
}
# FFmpeg
if (-not (Test-Path (Join-Path $pl 'bin\sc_ffmpeg.exe'))) {
    $z = Join-Path $tmp 'ff.zip'
    Get-File 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' $z
    Expand-Archive $z (Join-Path $tmp 'ff') -Force
    $f = Get-ChildItem (Join-Path $tmp 'ff') -Recurse -Filter ffmpeg.exe | Select-Object -First 1
    Copy-Item $f.FullName (Join-Path $pl 'bin\sc_ffmpeg.exe') -Force
}

# Inno Setup (compilador)
function Find-Iscc {
    @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
      "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
$iscc = Find-Iscc
if (-not $iscc) {
    # tenta varios enderecos e confere se o arquivo baixado e um executavel valido (cabecalho "MZ", > 1 MB)
    $urls = @('https://files.jrsoftware.org/is/6/innosetup-6.4.3.exe', 'https://jrsoftware.org/download.php/is.exe')
    $setup = Join-Path $tmp 'innosetup.exe'
    foreach ($u in $urls) {
        if (Find-Iscc) { break }
        foreach ($method in 'iwr', 'curl') {
            try {
                Remove-Item $setup -Force -ErrorAction SilentlyContinue
                Write-Host "Baixando $u ($method)"
                if ($method -eq 'iwr') { Invoke-WebRequest $u -OutFile $setup -UseBasicParsing }
                else { & curl.exe -L --fail -s -o $setup $u }
                $ok = (Test-Path $setup) -and ((Get-Item $setup).Length -gt 1MB)
                if ($ok) { $hdr = [IO.File]::ReadAllBytes($setup)[0..1]; $ok = ($hdr[0] -eq 0x4D -and $hdr[1] -eq 0x5A) }
                if (-not $ok) { Write-Host 'Arquivo baixado invalido, tentando outra forma...'; continue }
                Unblock-File $setup
                Start-Process $setup -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CURRENTUSER' -Wait
                if (Find-Iscc) { break }
            } catch { Write-Host "Falhou: $($_.Exception.Message)" }
        }
    }
    $iscc = Find-Iscc
}
if (-not $iscc) { throw 'Nao foi possivel instalar o Inno Setup automaticamente. Instale manualmente em https://jrsoftware.org/isdl.php (versao 6) e rode este arquivo de novo; o resto ja esta baixado.' }

# confere se a versao do programa e a do instalador sao a mesma (evita gerar instalador desatualizado)
$verPy  = (Select-String -Path (Join-Path $root 'src\screencam.py') -Pattern '^VERSION = "([^"]+)"').Matches[0].Groups[1].Value
$verIss = (Select-String -Path (Join-Path $root 'installer\ScreenCam.iss') -Pattern '#define AppVer "([^"]+)"').Matches[0].Groups[1].Value
if ($verPy -ne $verIss) { throw "Versoes diferentes: screencam.py=$verPy, ScreenCam.iss=$verIss. Envie os dois arquivos atualizados." }
Write-Host "Gerando instalador da versao $verPy" -ForegroundColor Cyan

& $iscc (Join-Path $root 'installer\ScreenCam.iss')
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o instalador.' }
$out = Join-Path $root 'installer\Output\ScreenCam_Setup.exe'
Write-Host ''
Write-Host "PRONTO: $out" -ForegroundColor Green
if (-not $env:CI) { Start-Process explorer.exe (Split-Path $out) }
