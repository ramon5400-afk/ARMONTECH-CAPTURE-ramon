; ScreenCam - instalador (Inno Setup 6). Gerado pelo build.ps1 / GERAR_INSTALADOR.bat
#define AppName "ScreenCam"
#define AppVer "1.0.3"

[Setup]
AppId={{B2A7C5E1-6D43-4F0B-9C1A-5C3E7D2A9F10}
AppName={#AppName}
AppVersion={#AppVer}
DefaultDirName=C:\ScreenCam
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=Output
OutputBaseFilename=ScreenCam_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Dirs]
Name: "{app}"; Permissions: users-modify

[Files]
Source: "payload\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs ignoreversion
Source: "payload\bin\*"; DestDir: "{app}\bin"; Flags: ignoreversion
Source: "..\screencam.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "postinstall.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "preuninstall.ps1"; DestDir: "{app}"; Flags: ignoreversion

[Tasks]
Name: "nosleep"; Description: "Impedir que o Windows suspenda/hiberne o PC (recomendado)"

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\postinstall.ps1"" -AppDir ""{app}"" -DeviceName ""{code:GetDev}"" -User ""{code:GetUser}"" -Pass ""{code:GetPass}"""; Flags: runhidden waituntilterminated; StatusMsg: "Configurando firewall e inicialização automática..."
Filename: "powercfg.exe"; Parameters: "/change standby-timeout-ac 0"; Flags: runhidden; Tasks: nosleep
Filename: "powercfg.exe"; Parameters: "/change hibernate-timeout-ac 0"; Flags: runhidden; Tasks: nosleep
Filename: "http://localhost:8090"; Description: "Abrir o painel de controle"; Flags: postinstall shellexec skipifsilent

[UninstallRun]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\preuninstall.ps1"""; Flags: runhidden waituntilterminated; RunOnceId: "StopScreenCam"

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
var
  CfgPage: TInputQueryWizardPage;

function BadChars(const S: String): Boolean;
begin
  Result := (Pos('"', S) > 0) or (Pos('\', S) > 0) or (Pos(':', S) > 0);
end;

procedure InitializeWizard;
begin
  CfgPage := CreateInputQueryPage(wpWelcome, 'Dados da câmera',
    'Informe os dados usados para cadastrar este PC no DVR',
    'Anote a senha: ela será pedida no DVR e no painel de controle.');
  CfgPage.Add('Nome da câmera (ex.: PDV Loja 1):', False);
  CfgPage.Add('Usuário:', False);
  CfgPage.Add('Senha (mínimo 4 caracteres):', True);
  CfgPage.Values[0] := 'PDV';
  CfgPage.Values[1] := 'admin';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  // em atualizações, mantém a configuração existente
  Result := (PageID = CfgPage.ID) and FileExists('C:\ScreenCam\config.json');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = CfgPage.ID then
  begin
    if (Trim(CfgPage.Values[1]) = '') or BadChars(CfgPage.Values[0]) or BadChars(CfgPage.Values[1]) then
    begin
      MsgBox('Preencha o usuário. Nome e usuário não podem ter aspas, barra invertida ou dois-pontos.', mbError, MB_OK);
      Result := False;
    end
    else if (Length(CfgPage.Values[2]) < 4) or BadChars(CfgPage.Values[2]) then
    begin
      MsgBox('A senha deve ter pelo menos 4 caracteres e não pode ter aspas, barra invertida ou dois-pontos.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function GetDev(Param: String): String;
begin
  Result := Trim(CfgPage.Values[0]);
end;

function GetUser(Param: String): String;
begin
  Result := Trim(CfgPage.Values[1]);
end;

function GetPass(Param: String): String;
begin
  Result := CfgPage.Values[2];
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RC: Integer;
begin
  Result := '';
  // atualização: para o programa antes de substituir os arquivos
  if FileExists('C:\ScreenCam\preuninstall.ps1') then
    Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -File "C:\ScreenCam\preuninstall.ps1" -StopOnly',
         '', SW_HIDE, ewWaitUntilTerminated, RC);
end;
