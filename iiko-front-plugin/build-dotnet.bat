@echo off
REM Sborka cherez .NET SDK (dotnet). Postavit SDK: https://dotnet.microsoft.com/download
REM V etoy papke dolzhny lezhat: SayaKds.csproj, SayaKdsForwarder.cs, Resto.Front.Api.exe, Resto.Front.Api.V8.exe
dotnet build -c Release
if exist bin\Release\netstandard2.0\SayaKds.dll (
  echo.
  echo [OK] Gotovo: bin\Release\netstandard2.0\SayaKds.dll
  echo Kopiruy etot SayaKds.dll v ...\iikoFront\Plugins\SayaKds\ i perezapusti iikoFront
) else (
  echo.
  echo [ERROR] Sborka ne udalas - smotri soobshcheniya vyshe
)
pause
