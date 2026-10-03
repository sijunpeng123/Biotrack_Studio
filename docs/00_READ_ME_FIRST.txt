BIOTRACK STUDIO - START HERE
============================

No programming is required.

1. Read BioTrack_Studio_User_Guide.pdf before the first analysis.
2. Extract the complete ZIP. Do not run BioTrack Studio inside the ZIP.

WINDOWS - FIRST USE
  Double-click setup_conda.bat, wait for it to finish, then double-click
  winstart.bat.

WINDOWS - LATER USE
  Double-click winstart.bat.

MACOS OR LINUX - FIRST USE
  Open Terminal in the extracted BioTrack_Studio folder (one level above the
  docs folder containing this file) and run:
    bash setup_conda.sh
    bash start.sh

MACOS OR LINUX - LATER USE
  Open Terminal in the extracted BioTrack_Studio folder and run:
    bash start.sh

FIRST TEST
  Use the included demo_data folder. It contains 20 consecutive HeLa
  H2B-mCherry TIFF frames for installation and workflow testing. Select
  cellpose_nuclei and sctrack, and use a separate empty output folder. The
  source, CC BY 4.0 license, and crop are recorded in DATA_SOURCE.txt. The demo
  is not ground truth or a biological accuracy benchmark.

DOCUMENTS
  BioTrack_Studio_User_Guide.pdf is the short illustrated protocol for normal
  use. BioTrack_Studio_Demo.ipynb is an optional extended reference containing
  formats, parameters, server notes, reports, and scientific limitations.

HELP
  Retry the same launcher once. If the problem remains, keep the installation
  log and report the operating system, selected action, and last visible
  message at:
  https://github.com/sijunpeng123/Biotrack_Studio/issues

Do not upload private microscopy data, passwords, tokens, or patient data.
