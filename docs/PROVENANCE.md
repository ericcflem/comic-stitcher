# Comic Stitcher — licensing scope and provenance

Eric Fleming selected the MIT License on 7 October 2026 (America/Chicago).
The project's own code, documentation, build helpers, original page icons, and
app-specific artwork are offered under LICENSE to the extent the contributors
hold applicable rights. Third-party material retains its own licenses.

The copyright notice does not assert that every AI-generated element is exclusively
copyrightable or that Eric personally wrote every line. Crediting an AI-assisted
process does not establish copyright ownership. No additional restrictions on
reuse or sale are added to MIT.

## Development history

- The original spread_pairer.py was developed by Eric with Grok. Eric confirmed
  that it did not come from another project. The GUI detector was adapted from
  that supplied script. This records Eric's account; it is not an independent
  guarantee that generated output cannot resemble other code.
- The application was developed with OpenAI Codex assistance under Eric's design
  direction and testing. AI tool output permissions do not replace third-party
  license obligations.
- The three page icons were made from scratch by Eric.
- The application icon originated through Gemini/Grok generation and Eric's manual
  editing. This process is documented without claiming exclusive copyright in all
  generated portions. Applicable provider terms remain part of provenance review.
- Phosphor interface icons remain MIT-licensed under their retained upstream notice.
- `comic_builder/cmyk_profile.py` embeds `CGATS001Compat-v2-micro.icc` from Clinton
  Ingram's Compact ICC Profiles (public domain, CC0 1.0), byte for byte; its SHA-256
  is recorded in the module and checked by the tests.

## Dependencies

The original script's Pillow/NumPy minimum-version requirements identify dependencies;
they do not constitute a license for the script itself. Preserve notices from the
actual packages shipped. The inspected Pillow 12.3.0 Windows combined notice identifies
MIT-CMU (plus incorporated component notices); NumPy identifies BSD-3-Clause plus
incorporated notices. Do not substitute an old package classifier or a remembered
license label for those complete texts.

Qt/PySide/Shiboken, archive codecs, Python, PDF libraries, native runtimes, and UnRAR
remain separately licensed. MIT for Comic Stitcher does not waive corresponding-source,
replacement/rebuild, notice, or redistribution obligations for those components.

## Release records

Exact wheel versions and hashes are in wheel-inputs.json. Complete selected
library archives are in retained-source-manifest.json. The shared-runtime
packaging changes are declared in installer/shared-runtime-inputs.json and
package_shared_runtime.py; original supplier wheels are retained unmodified in
the build cache. No modified-library test artifact is part of the reference app.

SOURCE_ACCESS.md describes versioned source delivery. Raw build and test logs
containing personal paths are not part of the public source package.
