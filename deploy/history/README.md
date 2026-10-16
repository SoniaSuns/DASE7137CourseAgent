# Archived one-off deployment scripts

Each script here was written for a single 2026-10-07..09 incident and checks
hard-coded run IDs, Moodle targets and report folders from that day. They are
kept only as a record. Do not run them; they resolve paths relative to this
folder and will fail.

Use `..\..\deploy_v2.ps1` instead: it deploys the committed git HEAD after the
full test suite passes and the listener is idle.
