; Included by setup_windows.iss. Normal UI: never bypass Microsoft's agreement.
[Code]
// Setup remains x86 for upgrade compatibility. Sysnative selects the x64 system DLLs.
function RuntimeFileAvailable(const Name: String): Boolean;
var
  VersionMS, VersionLS: Cardinal;
begin
  Result := GetVersionNumbers(ExpandConstant('{sysnative}\') + Name, VersionMS, VersionLS);
  if Result then
    Result := (VersionMS > ((14 shl 16) + 44)) or
      ((VersionMS = ((14 shl 16) + 44)) and
       ((VersionLS shr 16) >= 35211));
  if not Result then
    Log('Required Microsoft x64 runtime missing/old: ' + Name);
end;

function MicrosoftRuntimeAvailable: Boolean;
begin
  Result := RuntimeFileAvailable('MSVCP140.dll') and
    RuntimeFileAvailable('MSVCP140_1.dll') and
    RuntimeFileAvailable('MSVCP140_2.dll') and
    RuntimeFileAvailable('VCRUNTIME140.dll') and
    RuntimeFileAvailable('VCRUNTIME140_1.dll');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RuntimePath: String;
  ResultCode: Integer;
begin
  Result := '';
  if MicrosoftRuntimeAvailable then begin
    Log('Microsoft Visual C++ x64 runtime is suitable; prerequisite skipped.');
    Exit;
  end;
  if WizardSilent then begin
    Result := 'Microsoft Visual C++ x64 runtime 14.44.35211.0 or later is required. Install it first, then run Comic Stitcher Setup again. Silent Setup does not accept Microsoft terms or launch its installer automatically.';
    Exit;
  end;
  if MsgBox('Comic Stitcher needs the Microsoft Visual C++ x64 runtime.' + #13#10#13#10 +
      'Setup can open the official Microsoft installer now. You will see Microsoft''s license agreement and Windows may ask for administrator permission.' + #13#10#13#10 +
      'Open the Microsoft installer?', mbConfirmation, MB_YESNO) <> IDYES then begin
    Result := 'The required Microsoft runtime was not installed. No Comic Stitcher files have been installed or changed. Install the runtime and run Setup again.';
    Exit;
  end;
  try
    ExtractTemporaryFile('vc_redist.x64.exe');
    RuntimePath := ExpandConstant('{tmp}\vc_redist.x64.exe');
    if GetSHA256OfFile(RuntimePath) <> '{#MicrosoftRuntimeSHA256}' then begin
      Result := 'Microsoft installer integrity check failed. Setup cannot continue.';
      Exit;
    end;
    // runas supplies elevation only for the Microsoft installer; this app remains per-user.
    if not ShellExec('runas', RuntimePath, '/install /norestart', '',
        SW_SHOWNORMAL, ewWaitUntilTerminated, ResultCode) then begin
      Result := 'The Microsoft installer could not start, or administrator permission was declined: ' + SysErrorMessage(ResultCode);
      Exit;
    end;
    Log('Microsoft installer exit code: ' + IntToStr(ResultCode));
    if (ResultCode = 3010) or (ResultCode = 1641) then begin
      NeedsRestart := True;
      Result := 'Microsoft requires a Windows restart. Restart Windows, then run Comic Stitcher Setup again. Comic Stitcher has not been installed or updated yet.';
      Exit;
    end;
    // Do not trust an exit code alone: the required files must actually be available.
    if (ResultCode <> 0) or not MicrosoftRuntimeAvailable then begin
      Result := 'The Microsoft runtime installation was cancelled, failed, or did not make the required x64 files available. Exit code: ' + IntToStr(ResultCode) + '. Comic Stitcher has not been installed or updated. Please resolve the Microsoft installation and try again.';
      Exit;
    end;
  except
    Result := 'Could not prepare the Microsoft installer: ' + GetExceptionMessage;
  end;
end;
