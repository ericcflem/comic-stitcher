[Code]
// Acceptance also applies when the runtime is skipped.
function InitializeSetup: Boolean;
begin
  Result := True;
  if WizardSilent and
      (ExpandConstant('{param:ACCEPTMICROSOFTTERMS|no}') <> 'yes') then begin
    Log('Silent Setup blocked: read the component terms and explicitly supply /ACCEPTMICROSOFTTERMS=yes.');
    Result := False;
  end;
end;
