; KADRAJ — Inno Setup 6 kurulum betiği (kullanıcı başına kurulum, yönetici izni gerekmez).
;
; Önkoşul:  pyinstaller packaging\kadraj.spec --noconfirm   →  dist\KADRAJ\  (KADRAJ.exe + KADRAJ-console.exe + _internal\)
; Derleme:  ISCC.exe /DAppVersion=0.1.0 packaging\windows\kadraj.iss   →  dist\KADRAJ-0.1.0-windows-x64-setup.exe
; Sürüm kadraj/version.py'den gelir; .github/workflows/release.yml onu /DAppVersion ile geçirir.
; (Bu dosya UTF-8 BOM'lu kaydedilmelidir: Türkçe harfler için Inno Setup BOM arar.)

#ifndef AppVersion
  #error AppVersion verilmedi. Örnek: ISCC.exe /DAppVersion=0.1.0 packaging\windows\kadraj.iss
#endif

#define AppName "KADRAJ"
#define AppExe  "KADRAJ.exe"

[Setup]
; Sabit kimlik: aynı kimlikle yeniden kurulum eskisinin üzerine yazar (güncelleme) ve tek bir "Uygulamalar" girdisi olur.
AppId={{1BF9AD20-6217-4E03-B5B8-5569A379746D}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=İnsibağ
; Kullanıcı başına: %LOCALAPPDATA%\Programs\KADRAJ, HKCU kaldırma kaydı, kullanıcının Başlat menüsü/masaüstü.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
SetupIconFile=..\..\kadraj\ui\assets\icon\kadraj.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=KADRAJ-{#AppVersion}-windows-x64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
; Yedek dil İngilizce; Windows Türkçe ise kurulum Türkçe açılır.
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\KADRAJ\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "..\..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
; Güncellemede eski sürümün kütüphaneleri yeni olanlarla karışmasın.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; [UninstallDelete] bilerek yok: kaldırma yalnızca {app} klasörünü siler.
; Kullanıcı verisi (%USERPROFILE%\.kadraj — ayarlar, kasa, son projeler) ve proje klasörleri korunur.

[CustomMessages]
english.FfmpegMissing=KADRAJ needs FFmpeg for video work, and it was not found on this computer.%n%nTo install it, open Terminal (or PowerShell) and run:%n%n    winget install Gyan.FFmpeg%n%nThen restart KADRAJ.
turkish.FfmpegMissing=KADRAJ video işleri için FFmpeg'e ihtiyaç duyar; bu bilgisayarda bulunamadı.%n%nKurmak için Terminal'i (ya da PowerShell'i) açıp şunu çalıştırın:%n%n    winget install Gyan.FFmpeg%n%nSonra KADRAJ'ı yeniden başlatın.

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  { Sessiz kurulumda soru/ileti gösterilmez. FFmpeg yoksa nasıl kurulacağı söylenir; kurulum yine de tamamlanır. }
  if (CurStep = ssPostInstall) and (not WizardSilent) then
    if FileSearch('ffmpeg.exe', GetEnv('PATH')) = '' then
      MsgBox(CustomMessage('FfmpegMissing'), mbInformation, MB_OK);
end;
