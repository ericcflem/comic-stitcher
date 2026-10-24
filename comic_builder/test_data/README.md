# Test data

`CGATS001Compat-v2-micro.icc` is a small CMYK colour profile from Clinton
Ingram's Compact ICC Profiles (https://github.com/saucecontrol/Compact-ICC-Profiles),
dedicated to the public domain under CC0 1.0. The colour tests use it to check
that CMYK pages are converted with their embedded profile. The same profile is
embedded in `comic_builder/cmyk_profile.py` for CMYK pages that have none.
