@echo off
setlocal
set CSC=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe
if not exist "%CSC%" set CSC=%WINDIR%\Microsoft.NET\Framework\v4.0.30319\csc.exe
if not exist "%CSC%" echo [ERROR] csc.exe ne nayden - ustanovi .NET Framework 4.x & pause & exit /b 1
"%CSC%" /nologo /target:library /out:SayaKds.dll /reference:Resto.Front.Api.exe /reference:Resto.Front.Api.V8.exe /reference:System.Net.Http.dll SayaKdsForwarder.cs
if exist SayaKds.dll echo [OK] Sobrano: SayaKds.dll  --  kopiruy v ...\iikoFront\Plugins\SayaKds\
if not exist SayaKds.dll echo [ERROR] Sborka ne udalas - smotri soobshcheniya vyshe
pause
