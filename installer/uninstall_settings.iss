; Uninstall: offer to remove the user's own data as well, as linux/uninstall.sh
; --purge does. Kept unless the user chooses Yes; a silent uninstall keeps it.
; Settings, including a remembered ComicVine key: HKCU\Software\ComicStitcher.
; Logs, review reports and saved reviews: %LOCALAPPDATA%\ComicStitcher.
[Code]
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
  begin
    if SuppressibleMsgBox('Also remove your Comic Stitcher settings?' + #13#10 + #13#10 +
        'This removes your preferences, a remembered ComicVine API key, saved reviews, ' +
        'pair review reports and logs. Your comics and their original files are not affected.' + #13#10 + #13#10 +
        'Choose No to keep them in case you reinstall.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    begin
      RegDeleteKeyIncludingSubkeys(HKCU, 'Software\ComicStitcher');
      DelTree(ExpandConstant('{localappdata}\ComicStitcher'), True, True, True);
    end;
  end;
end;
