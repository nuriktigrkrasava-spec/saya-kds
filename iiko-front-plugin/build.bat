@echo off
REM ============================================================
REM  Сборка плагина Saya KDS без Visual Studio (через csc.exe).
REM  Положи в ОДНУ папку: build.bat, SayaKdsForwarder.cs,
REM  Resto.Front.Api.exe, Resto.Front.Api.V8.exe  — и запусти этот файл.
REM  Результат: SayaKds.dll  (его кладём в Plugins на кассе).
REM ============================================================

setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" (
  echo [ОШИБКА] Не найден csc.exe (компилятор C#). Установи .NET Framework 4.x.
  pause & exit /b 1
)

if not exist "Resto.Front.Api.exe" echo [ВНИМАНИЕ] нет Resto.Front.Api.exe рядом
if not exist "Resto.Front.Api.V8.exe" echo [ВНИМАНИЕ] нет Resto.Front.Api.V8.exe рядом

"%CSC%" /nologo /target:library /out:SayaKds.dll ^
  /reference:Resto.Front.Api.exe ^
  /reference:Resto.Front.Api.V8.exe ^
  /reference:System.Net.Http.dll ^
  SayaKdsForwarder.cs

if exist SayaKds.dll (
  echo.
  echo [OK] Собрано: SayaKds.dll
  echo Скопируй его в  ...\iikoFront\Plugins\SayaKds\  и перезапусти iikoFront.
) else (
  echo.
  echo [ОШИБКА] Сборка не удалась. Прочитай сообщения выше.
  echo Если ругается на отсутствующую ссылку (напр. System.Reactive) —
  echo добавь её файл рядом и пропиши ещё одну строку /reference:ИМЯ.dll
)
pause
