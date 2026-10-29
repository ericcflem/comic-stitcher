[Code]
// Included after the other [Code] files. Pascal comments only.
// Windows limits file paths to 260 characters unless LongPathsEnabled is 1.
// Comic Stitcher's executable declares long path support, so the setting is all
// it needs; the long paths task is offered only while the setting is off.
function LongPathsOff: Boolean;
var
  Enabled: Cardinal;
begin
  Result := not (RegQueryDWordValue(HKLM, 'SYSTEM\CurrentControlSet\Control\FileSystem',
                                    'LongPathsEnabled', Enabled) and (Enabled = 1));
end;
