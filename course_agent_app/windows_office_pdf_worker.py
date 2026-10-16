"""Isolated Microsoft Office COM worker for Windows PDF rendering."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


OFFICE_PROG_IDS = {
    ".docx": "Word.Application",
    ".xlsx": "Excel.Application",
    ".pptx": "PowerPoint.Application",
}


def configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except (AttributeError, OSError, ValueError):
            continue


def convert_with_office(source: Path, output: Path) -> None:
    if os.name != "nt":
        raise RuntimeError("Microsoft Office COM conversion is available only on Windows.")
    suffix = source.suffix.lower()
    prog_id = OFFICE_PROG_IDS.get(suffix)
    if not prog_id:
        raise ValueError(f"Unsupported Microsoft Office file type: {suffix or '(none)'}")
    if not source.is_file():
        raise FileNotFoundError(source)
    if output.suffix.lower() != ".pdf":
        raise ValueError("The Office conversion output must use a .pdf suffix.")

    import pythoncom  # type: ignore
    import win32com.client  # type: ignore

    output.parent.mkdir(parents=True, exist_ok=True)
    source_text = str(source.resolve())
    output_text = str(output.resolve())
    pythoncom.CoInitialize()
    app = None
    document = None
    try:
        app = win32com.client.DispatchEx(prog_id)
        try:
            app.Visible = False
        except Exception:
            pass
        try:
            app.DisplayAlerts = 0
        except Exception:
            pass
        try:
            # msoAutomationSecurityForceDisable: do not execute document macros.
            app.AutomationSecurity = 3
        except Exception:
            pass

        if suffix == ".docx":
            document = app.Documents.Open(
                source_text,
                ConfirmConversions=False,
                ReadOnly=True,
                AddToRecentFiles=False,
                Visible=False,
            )
            document.ExportAsFixedFormat(output_text, 17, OpenAfterExport=False)
        elif suffix == ".xlsx":
            document = app.Workbooks.Open(
                source_text,
                UpdateLinks=0,
                ReadOnly=True,
                AddToMru=False,
            )
            document.ExportAsFixedFormat(0, output_text, OpenAfterPublish=False)
        else:
            document = app.Presentations.Open(
                source_text,
                ReadOnly=True,
                Untitled=False,
                WithWindow=False,
            )
            # ppSaveAsPDF is more reliable through late-bound COM than
            # ExportAsFixedFormat, whose optional PrintRange parameter expects
            # a PowerPoint COM object even when omitted by pywin32.
            document.SaveAs(output_text, 32)
    finally:
        if document is not None:
            try:
                if suffix in {".docx", ".xlsx"}:
                    document.Close(False)
                else:
                    document.Close()
            except Exception:
                pass
        if app is not None:
            try:
                app.Quit()
            except Exception:
                pass
        pythoncom.CoUninitialize()

    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("Microsoft Office did not create a non-empty PDF.")


def main() -> int:
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description="Render one Office file to PDF through Windows COM.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    convert_with_office(Path(args.source), Path(args.output))
    print(f"Rendered PDF with Microsoft Office: {Path(args.output).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
