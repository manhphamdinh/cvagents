@echo off
REM Dong goi ma nguon nop Khoa: ZIP gom thu muc Source + Readme.md. Chay tu thu muc _dong_goi.
setlocal
set "HERE=%~dp0"
set "PROJ=%~dp0.."
set "STAGE=%TEMP%\kl_dong_goi_%RANDOM%"
set "ZIPOUT=%~dp0..\..\11236150_PhamDinhManh_MaNguon.zip"
if not exist "%PROJ%\pyproject.toml" (echo Khong thay thu muc code. & pause & exit /b 1)

mkdir "%STAGE%\Source"
robocopy "%PROJ%" "%STAGE%\Source" /E /NFL /NDL /NJH /NJS /NP /XD "_dong_goi" "__pycache__" ".pytest_cache" ".venv" "models" "results" "*.egg-info" /XF "*.pyc" "tach_ban_nop.bat" "xoa_file_thua.bat" >nul
if errorlevel 8 (echo LOI khi chep ma nguon. & pause & exit /b 1)
copy /y "%HERE%config.toml" "%STAGE%\Source\src\cvagents\resources\config.toml" >nul
copy /y "%HERE%Readme.md" "%STAGE%\Readme.md" >nul

if exist "%ZIPOUT%" del /f /q "%ZIPOUT%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path (Join-Path $env:STAGE '*') -DestinationPath $env:ZIPOUT -Force; $s=(Get-Item -LiteralPath $env:ZIPOUT).Length; Write-Host ('Da tao ZIP, dung luong: {0:N2} MB' -f ($s/1MB))"
if errorlevel 1 (echo LOI khi nen ZIP. & pause & exit /b 1)
rmdir /s /q "%STAGE%"
echo File nop: %ZIPOUT%
pause
cd /d "%PROJ%"
(goto) 2>nul & rmdir /s /q "%HERE%"
